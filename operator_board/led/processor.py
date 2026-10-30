"""A single LED processor driven by a protocol profile."""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from operator_board.core.config import LEDProcessorConfig
from operator_board.led.transports import Transport, TransportError, command_bytes, make_transport

log = logging.getLogger(__name__)

CAP_FREEZE = "freeze"
CAP_BLACKOUT = "blackout"
CAP_TEST_PATTERN = "test_pattern"
CAP_BRIGHTNESS = "brightness"


def novastar_checksum(frame: bytes) -> bytes:
    """NovaStar checksum: sum of all bytes after the 0x55 0xAA header, plus 0x5555, little-endian uint16."""
    total = (sum(frame[2:]) + 0x5555) & 0xFFFF
    return total.to_bytes(2, "little")


def _substitute(obj: Any, value: Any) -> Any:
    if isinstance(obj, str):
        if obj == "{value}":
            return value
        return obj.replace("{value}", str(value))
    if isinstance(obj, list):
        return [_substitute(v, value) for v in obj]
    if isinstance(obj, dict):
        return {k: _substitute(v, value) for k, v in obj.items()}
    return obj


def scale_value(percent: float, spec: Dict[str, Any]) -> Any:
    """Map an operator-facing 0-100% value onto the processor's native range."""
    lo = float(spec.get("min", 0))
    hi = float(spec.get("max", 100))
    raw = lo + (hi - lo) * max(0.0, min(100.0, float(percent))) / 100.0
    fmt = spec.get("format", "int")
    if fmt == "hex_byte":
        return f"{int(round(raw)) & 0xFF:02X}"
    if fmt == "float":
        return round(raw, 3)
    return int(round(raw))


class SimulatorTransport(Transport):
    kind = "simulator"

    def __init__(self) -> None:
        super().__init__("simulator", 0)
        self.sent: List[Dict[str, Any]] = []

    def send(self, command: Dict[str, Any]) -> Any:
        self.sent.append(command)
        return {"ok": True}


@dataclass
class ProcessorState:
    frozen: bool = False
    blackout: bool = False
    test_pattern: Optional[str] = None
    brightness: Optional[float] = None
    online: Optional[bool] = None
    last_error: str = ""
    last_command: str = ""
    last_ok: float = 0.0
    history: List[str] = field(default_factory=list)


class LEDProcessor:
    def __init__(self, config: LEDProcessorConfig, profile: Dict[str, Any],
                 transport: Optional[Transport] = None) -> None:
        self.config = config
        self.profile = profile
        self.state = ProcessorState()
        self._lock = threading.Lock()
        self.commands: Dict[str, Any] = copy.deepcopy(profile.get("commands", {}))
        for name, cmd in (config.command_overrides or {}).items():
            if cmd:
                cmd = dict(cmd)
                cmd.setdefault("confidence", "user")
                self.commands[name] = cmd
        self.test_patterns: List[Dict[str, Any]] = list(profile.get("test_patterns", []))
        for pattern in (config.options or {}).get("custom_test_patterns", []) or []:
            self.test_patterns.append(pattern)
        self.transport = transport or self._make_transport()

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def port(self) -> int:
        return self.config.port or int(self.profile.get("default_port", 0))

    def _make_transport(self) -> Transport:
        kind = self.profile.get("transport", "simulator")
        if kind == "simulator":
            return SimulatorTransport()
        return make_transport(kind, self.config.host, self.port, self.config.options or {})

    def capabilities(self) -> List[str]:
        caps = []
        if self.has("freeze_on") and self.has("freeze_off"):
            caps.append(CAP_FREEZE)
        if self.has("blackout_on") and self.has("blackout_off"):
            caps.append(CAP_BLACKOUT)
        if self.test_patterns:
            caps.append(CAP_TEST_PATTERN)
        if self.has("brightness"):
            caps.append(CAP_BRIGHTNESS)
        return caps

    def has(self, name: str) -> bool:
        return self._resolve(name) is not None

    def _resolve(self, name: str, depth: int = 0) -> Optional[Dict[str, Any]]:
        cmd = self.commands.get(name)
        if isinstance(cmd, dict) and "ref" in cmd and depth < 5:
            return self._resolve(cmd["ref"], depth + 1)
        return cmd if isinstance(cmd, dict) and cmd else None

    def confidence(self, name: str) -> str:
        cmd = self._resolve(name)
        return (cmd or {}).get("confidence", "assumed")

    # ---- sending -----------------------------------------------------------------
    def prepare(self, command: Dict[str, Any], value: Any = None) -> Dict[str, Any]:
        cmd = {k: v for k, v in command.items() if k not in ("confidence", "value", "description")}
        if value is not None:
            cmd = _substitute(cmd, value)
        if cmd.pop("checksum", None) == "novastar":
            frame = command_bytes(cmd)
            cmd = {"bytes": list(frame + novastar_checksum(frame))}
        return cmd

    def _send(self, commands: List[Dict[str, Any]], label: str, value: Any = None) -> None:
        with self._lock:
            try:
                for c in commands:
                    self.transport.send(self.prepare(c, value))
            except TransportError as exc:
                self.state.online = False
                self.state.last_error = str(exc)
                self._record(f"FAILED {label}: {exc}")
                raise
            self.state.online = True
            self.state.last_error = ""
            self.state.last_command = label
            self.state.last_ok = time.time()
            self._record(label)

    def _record(self, text: str) -> None:
        self.state.history.append(time.strftime("%H:%M:%S ") + text)
        del self.state.history[:-100]

    def send_named(self, name: str, value: Any = None) -> None:
        cmd = self._resolve(name)
        if cmd is None:
            raise TransportError(f"{self.name}: '{name}' is not supported by {self.profile.get('name')}")
        self._send([cmd], name, value)

    # ---- operations --------------------------------------------------------------
    def set_freeze(self, on: bool) -> None:
        self.send_named("freeze_on" if on else "freeze_off")
        self.state.frozen = on
        if on and self._display_mode_exclusive():
            self.state.blackout = False

    def set_blackout(self, on: bool) -> None:
        self.send_named("blackout_on" if on else "blackout_off")
        self.state.blackout = on
        if on and self._display_mode_exclusive():
            self.state.frozen = False

    def _display_mode_exclusive(self) -> bool:
        # NovaStar-style processors implement freeze & black as a single display mode.
        return self.commands.get("freeze_off", {}).get("ref") == "normal"

    def set_test_pattern(self, pattern_id: Optional[str]) -> None:
        if not pattern_id:
            self.send_named("test_pattern_off")
            self.state.test_pattern = None
            return
        pattern = next((p for p in self.test_patterns if p["id"] == pattern_id), None)
        if pattern is None:
            raise TransportError(f"{self.name}: unknown test pattern '{pattern_id}'")
        self._send(pattern.get("commands", []), f"test pattern {pattern_id}")
        self.state.test_pattern = pattern_id

    def set_brightness(self, percent: float) -> None:
        cmd = self._resolve("brightness")
        if cmd is None:
            raise TransportError(f"{self.name}: brightness is not supported")
        self._send([cmd], f"brightness {percent:.0f}%", scale_value(percent, cmd.get("value", {})))
        self.state.brightness = percent

    def check_connection(self) -> bool:
        status = self.profile.get("status_command")
        try:
            if status:
                self._send([status], "status check")
            else:
                self.transport.open()
                self.state.online = True
        except TransportError as exc:
            self.state.online = False
            self.state.last_error = str(exc)
            return False
        return True

    def close(self) -> None:
        self.transport.close()

    def describe_command(self, name: str) -> str:
        cmd = self._resolve(name)
        return json.dumps(cmd, indent=2) if cmd else "(not supported)"

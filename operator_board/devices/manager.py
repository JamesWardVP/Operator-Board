"""Tracks every Unreal instance (editor / nDisplay node) and drives them through their listeners."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from operator_board.core.config import AppConfig, DeviceConfig
from operator_board.core.events import EventBus
from operator_board.devices.launcher import unreal_launch_spec
from operator_board.listener.client import ListenerClient, decode_stdout

log = logging.getLogger(__name__)

CALLER = "OperatorBoard"
PROGRAM_NAME = "unreal"  # same program name Switchboard uses, so either tool can see the other's processes

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
ERROR = "error"


@dataclass
class DeviceRuntime:
    config: DeviceConfig
    state: str = DISCONNECTED
    client: Optional[ListenerClient] = None
    program_id: Optional[str] = None
    pending_start_id: Optional[str] = None
    pid: Optional[int] = None
    started_at: Optional[float] = None
    last_error: str = ""
    listener_version: str = ""
    log_tail: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    last_metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def uptime_s(self) -> float:
        return time.time() - self.started_at if self.started_at and self.state == RUNNING else 0.0


def _find_metrics(msg: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("syncStatus", "sync_status", "status"):
        if isinstance(msg.get(key), dict):
            return msg[key]
    return msg


class DeviceManager:
    def __init__(self, config: AppConfig, bus: EventBus) -> None:
        self.config = config
        self.bus = bus
        self._lock = threading.RLock()
        self._devices: Dict[str, DeviceRuntime] = {}
        self.reload()

    # ---- registry ---------------------------------------------------------
    def reload(self) -> None:
        with self._lock:
            wanted = {d.name: d for d in self.config.devices}
            for name in list(self._devices):
                if name not in wanted:
                    self._drop(name)
            for name, cfg in wanted.items():
                rt = self._devices.get(name)
                if rt is None:
                    self._devices[name] = DeviceRuntime(config=cfg)
                else:
                    rt.config = cfg
        self.bus.publish("devices.changed")

    def _drop(self, name: str) -> None:
        rt = self._devices.pop(name, None)
        if rt and rt.client:
            rt.client.disconnect("device removed")

    def devices(self) -> List[DeviceRuntime]:
        with self._lock:
            return [self._devices[d.name] for d in self.config.devices if d.name in self._devices]

    def get(self, name: str) -> Optional[DeviceRuntime]:
        return self._devices.get(name)

    def group_devices(self, group: str) -> List[DeviceRuntime]:
        g = self.config.group(group)
        if g is None:
            return []
        return [self._devices[n] for n in g.devices if n in self._devices]

    def _set_state(self, rt: DeviceRuntime, state: str, error: str = "") -> None:
        rt.state = state
        if error:
            rt.last_error = error
        self.bus.publish("device.state", name=rt.name, state=state, error=error)

    # ---- connection -------------------------------------------------------
    def connect(self, name: str) -> None:
        rt = self._require(name)
        if rt.client and rt.client.connected:
            return
        self._set_state(rt, CONNECTING)
        client = ListenerClient(
            rt.config.address, rt.config.listener_port,
            on_message=lambda msg, rt=rt: self._on_message(rt, msg),
            on_disconnect=lambda reason, rt=rt: self._on_disconnect(rt, reason),
        )
        try:
            client.connect()
        except OSError as exc:
            self._set_state(rt, ERROR, f"Cannot reach listener at {rt.config.address}:{rt.config.listener_port} ({exc})")
            return
        rt.client = client
        self._set_state(rt, CONNECTED)

    def disconnect(self, name: str) -> None:
        rt = self._require(name)
        if rt.client:
            rt.client.disconnect("user disconnect")
        rt.client = None
        self._set_state(rt, DISCONNECTED)

    def connect_all(self, names: Optional[List[str]] = None) -> None:
        self._parallel(self.connect, names or [d.name for d in self.devices() if d.config.enabled])

    def disconnect_all(self) -> None:
        for rt in self.devices():
            if rt.client:
                self.disconnect(rt.name)

    # ---- process control ----------------------------------------------------
    def start(self, name: str, map_override: str = "") -> None:
        rt = self._require(name)
        if rt.state == RUNNING:
            return
        if not (rt.client and rt.client.connected):
            self.connect(name)
            if not (rt.client and rt.client.connected):
                return
        try:
            spec = unreal_launch_spec(rt.config, self.config.project(), self.config.multiuser, map_override)
        except ValueError as exc:
            self._set_state(rt, ERROR, str(exc))
            return
        rt.log_tail.append(f"> {spec.as_command_line()}")
        rt.pending_start_id = rt.client.start_process(
            exe=spec.exe, args=spec.args, name=PROGRAM_NAME, caller=CALLER, working_dir=spec.working_dir)
        self._set_state(rt, STARTING)

    def stop(self, name: str) -> None:
        rt = self._require(name)
        if rt.program_id and rt.client and rt.client.connected:
            rt.client.kill_process(rt.program_id)
            self._set_state(rt, STOPPING)
        elif rt.state in (STARTING, RUNNING):
            self._set_state(rt, CONNECTED if rt.client else DISCONNECTED)

    def restart(self, name: str, map_override: str = "") -> None:
        rt = self._require(name)
        if rt.state in (RUNNING, STARTING):
            self.stop(name)
            deadline = time.monotonic() + 15
            while rt.state not in (CONNECTED, DISCONNECTED, ERROR) and time.monotonic() < deadline:
                time.sleep(0.1)
        self.start(name, map_override)

    def start_group(self, group: str, map_override: str = "") -> None:
        self._parallel(lambda n: self.start(n, map_override), [d.name for d in self.group_devices(group)])

    def stop_group(self, group: str) -> None:
        self._parallel(self.stop, [d.name for d in self.group_devices(group)])

    def start_all(self, map_override: str = "") -> None:
        self._parallel(lambda n: self.start(n, map_override), [d.name for d in self.devices() if d.config.enabled])

    def stop_all(self) -> None:
        self._parallel(self.stop, [d.name for d in self.devices()])

    def restart_all(self, map_override: str = "") -> None:
        self._parallel(lambda n: self.restart(n, map_override), [d.name for d in self.devices() if d.config.enabled])

    def running(self) -> List[DeviceRuntime]:
        return [d for d in self.devices() if d.state == RUNNING]

    def request_metrics(self) -> None:
        for rt in self.running():
            if rt.client and rt.program_id:
                try:
                    rt.client.request_sync_status(rt.program_id)
                except ConnectionError:
                    pass

    # ---- listener messages -----------------------------------------------------
    def _on_message(self, rt: DeviceRuntime, msg: Dict[str, Any]) -> None:
        cmd = str(msg.get("command", ""))
        if cmd == "state":
            rt.listener_version = str(msg.get("version", rt.listener_version))
            for proc in msg.get("runningProcesses", []) or []:
                if proc.get("name") == PROGRAM_NAME and rt.program_id is None:
                    rt.program_id = proc.get("uuid")
                    rt.pid = proc.get("pid")
                    rt.started_at = time.time()
                    self._set_state(rt, RUNNING)
        elif cmd == "start" and msg.get("bAck") is False:
            rt.pending_start_id = None
            self._set_state(rt, ERROR, f"Listener refused start: {msg.get('error', 'unknown error')}")
        elif cmd == "program started":
            if msg.get("name", PROGRAM_NAME) == PROGRAM_NAME:
                rt.program_id = msg.get("process_id") or msg.get("uuid") or msg.get("program_id")
                rt.pid = msg.get("pid")
                rt.started_at = time.time()
                rt.pending_start_id = None
                self._set_state(rt, RUNNING)
        elif cmd in ("program start failed", "program killed failed", "program kill failed"):
            self._set_state(rt, ERROR, str(msg.get("error", cmd)))
        elif cmd == "program ended":
            pid = msg.get("process_id") or msg.get("uuid")
            if pid is None or pid == rt.program_id:
                rt.program_id = None
                rt.pid = None
                code = msg.get("returncode")
                out = decode_stdout(msg)
                if out:
                    rt.log_tail.extend(out.splitlines()[-50:])
                rt.log_tail.append(f"< process ended (return code {code})")
                self._set_state(rt, CONNECTED if rt.client else DISCONNECTED)
        elif cmd == "programstdout":
            text = decode_stdout(msg)
            if text:
                rt.log_tail.extend(text.splitlines())
                self.bus.publish("device.stdout", name=rt.name, text=text)
        elif cmd == "get sync status" and msg.get("bAck", True):
            metrics = _find_metrics(msg)
            rt.last_metrics = metrics
            self.bus.publish("device.metrics", name=rt.name, metrics=metrics)

    def _on_disconnect(self, rt: DeviceRuntime, reason: str) -> None:
        rt.client = None
        rt.program_id = None
        if reason == "user disconnect":
            return
        self._set_state(rt, ERROR, f"Listener connection lost: {reason}")

    # ---- helpers ------------------------------------------------------------
    def _require(self, name: str) -> DeviceRuntime:
        rt = self._devices.get(name)
        if rt is None:
            raise KeyError(f"Unknown device '{name}'")
        return rt

    @staticmethod
    def _parallel(fn, names: List[str]) -> None:
        threads = [threading.Thread(target=fn, args=(n,), daemon=True) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

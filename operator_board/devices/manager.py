"""Tracks every Unreal instance (editor / nDisplay node) and drives them through their listeners."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

from operator_board.core.config import ROLE_NDISPLAY, AppConfig, DeviceConfig
from operator_board.devices import ndisplay
from operator_board.core.events import EventBus
from operator_board.devices.launcher import unreal_launch_spec
from operator_board.listener import protocol
from operator_board.listener.client import ListenerClient, client_for_device, decode_stdout, unreachable_hint
from operator_board.listener.quic import FingerprintChanged, ListenerAuthError, PasswordRejected

log = logging.getLogger(__name__)

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
    transport: str = ""  # "tcp" or "quic" once connected
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
        # Called with the DeviceConfig just before Unreal is launched (multi-user auto start).
        self.before_start: Optional[Callable[[DeviceConfig], None]] = None
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
            if state == ERROR:
                log.warning("%s: %s", rt.name, error)
        self.bus.publish("device.state", name=rt.name, state=state, error=error)

    # ---- connection -------------------------------------------------------
    def connect(self, name: str, password: Optional[str] = None, trust_new_fingerprint: bool = False) -> None:
        """Connect to the device's listener.

        UE 5.4+ listeners need a password. Without a saved one (or with a wrong one, or
        when the listener's security key changed) the device goes to ERROR and a
        "device.auth" event asks the UI to prompt; it then calls connect() again with
        the password or trust_new_fingerprint=True.
        """
        rt = self._require(name)
        if rt.client and rt.client.connected:
            return
        self._set_state(rt, CONNECTING)
        client = client_for_device(
            rt.config, self.config.project().ue_version,
            on_message=lambda msg, rt=rt: self._on_message(rt, msg),
            on_disconnect=lambda reason, rt=rt: self._on_disconnect(rt, reason),
        )
        try:
            client.connect(password=password, trust_new_fingerprint=trust_new_fingerprint)
        except ListenerAuthError as exc:
            kind = ("fingerprint" if isinstance(exc, FingerprintChanged)
                    else "rejected" if isinstance(exc, PasswordRejected) else "password")
            self._set_state(rt, ERROR, str(exc))
            self.bus.publish("device.auth", name=rt.name, address=exc.host, kind=kind, message=str(exc),
                             fingerprint=exc.fingerprint_display, subject=exc.subject)
            return
        except OSError as exc:
            self._set_state(rt, ERROR, f"Cannot reach listener at {rt.config.address}:{rt.config.listener_port} "
                                       f"({exc}). {unreachable_hint(rt.config.address, rt.config.listener_port)}")
            return
        rt.client = client
        rt.transport = client.transport
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
        if self.before_start is not None:
            self.before_start(rt.config)
        try:
            extra = self._prepare_ndisplay(rt) if rt.config.role == ROLE_NDISPLAY else {}
            spec = unreal_launch_spec(rt.config, self.config.project(), self.config.multiuser, map_override,
                                      **extra)
        except (ValueError, OSError) as exc:
            self._set_state(rt, ERROR, str(exc))
            return
        rt.log_tail.append(f"> {spec.as_command_line()}")
        # Mark it starting *before* sending: a local listener can report "program started"
        # before start_process() returns, and that RUNNING must not be overwritten.
        self._set_state(rt, STARTING)
        rt.pending_start_id = rt.client.start_process(
            exe=spec.exe, args=spec.args, name=PROGRAM_NAME, caller=rt.name, working_dir=spec.working_dir)

    def _prepare_ndisplay(self, rt: DeviceRuntime) -> Dict[str, Any]:
        """Write this launch's .ndisplay config on the node and return launch_spec extras.

        As Switchboard: the JSON comes out of the project's nDisplay Config asset (or a
        .ndisplay file), keeps only our nDisplay devices with each node's host set to the
        device's address, and is written through the listener to %TEMP%/ndisplay.
        """
        src = self.config.project().ndisplay_config
        if not src:
            raise ValueError("Set the nDisplay config (the nDisplay Config asset or a .ndisplay file) "
                             "on the project before starting nDisplay nodes")
        if not os.path.exists(src):
            raise ValueError(f"nDisplay config not found on this computer: {src}")
        text = ndisplay.extract_config_json(src)
        node = rt.config.ndisplay_node or rt.config.name
        known = ndisplay.node_names(text)
        if node not in known:
            raise ValueError(f"'{node}' is not a node in the nDisplay config (its nodes: {', '.join(known)}). "
                             "Name the device, or its nDisplay node, after a cluster node.")
        devices = [d for d in self.config.devices if d.role == ROLE_NDISPLAY and d.enabled]
        nodes = {(d.ndisplay_node or d.name): d.address for d in devices}
        text = ndisplay.apply_local_overrides(text, nodes)
        cohosted = sorted(n for n, address in nodes.items() if address == rt.config.address and n in known)
        path = rt.client.send_file_content(text.encode("utf-8"), ndisplay.CONFIG_DESTINATION)
        rt.log_tail.append(f"nDisplay config written to {path}")
        return {"ndisplay_cfg": path, "window": ndisplay.node_window(text, node),
                "multiprocess": bool(cohosted) and node != cohosted[0]}

    def stop(self, name: str) -> None:
        """Ask the listener to close Unreal (it is forced after a few seconds, as in Switchboard)."""
        rt = self._require(name)
        if rt.program_id and rt.client and rt.client.connected:
            self._set_state(rt, STOPPING)  # before sending, for the same reason as in start()
            rt.client.kill_process(rt.program_id)
        elif rt.state in (STARTING, RUNNING):
            if rt.client and rt.client.connected:
                self._set_state(rt, ERROR, "Cannot stop: the listener has not reported this program's id yet")
            else:
                self._set_state(rt, ERROR, "Cannot stop: not connected to this machine's listener")

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
        # Several devices can share one listener (an editor and a node on one PC) and every
        # connection hears about every program, so only programs whose caller is this
        # device are ours - the same rule Switchboard uses.
        if cmd == "state":
            rt.listener_version = str(msg.get("version", rt.listener_version))
            for proc in msg.get("runningProcesses", []) or []:
                if self._is_ours(rt, proc) and rt.program_id is None:
                    rt.program_id = proc.get("uuid")
                    rt.pid = proc.get("pid")
                    rt.started_at = time.time()
                    self._set_state(rt, RUNNING)
        elif cmd == "start" and msg.get("bAck") is False:
            if protocol.same_id(msg.get("id"), rt.pending_start_id):
                rt.pending_start_id = None
                self._set_state(rt, ERROR, f"Listener refused start: {msg.get('error', 'unknown error')}")
        elif cmd == "program started":
            proc = protocol.program_info(msg)
            if msg.get("bAck") is False:
                if protocol.same_id(proc.get("uuid"), rt.pending_start_id):
                    rt.pending_start_id = None
                    self._set_state(rt, ERROR, f"Could not start Unreal: {msg.get('error', 'unknown error')}")
            elif self._is_ours(rt, proc):
                rt.program_id = proc.get("uuid")
                rt.pid = proc.get("pid")
                rt.started_at = time.time()
                rt.pending_start_id = None
                self._set_state(rt, RUNNING)
        elif cmd == "program killed":
            proc = protocol.program_info(msg)
            if msg.get("bAck") is False and protocol.same_id(proc.get("uuid"), rt.program_id):
                self._set_state(rt, ERROR, f"Could not stop Unreal: {msg.get('error', 'unknown error')}")
        elif cmd == "program ended":
            proc = protocol.program_info(msg)
            if rt.program_id and protocol.same_id(proc.get("uuid"), rt.program_id):
                rt.program_id = None
                rt.pid = None
                code = msg.get("returncode")
                out = decode_stdout(msg)
                if out:
                    rt.log_tail.extend(out.splitlines()[-50:])
                rt.log_tail.append(f"< process ended (return code {code})")
                self._set_state(rt, CONNECTED if rt.client else DISCONNECTED)
        elif cmd == "programstdout":
            if not self._is_ours(rt, protocol.program_info(msg)):
                return
            text = decode_stdout(msg)
            if text:
                rt.log_tail.extend(text.splitlines())
                self.bus.publish("device.stdout", name=rt.name, text=text)
        elif cmd == "get sync status" and msg.get("bAck", True):
            metrics = _find_metrics(msg)
            rt.last_metrics = metrics
            self.bus.publish("device.metrics", name=rt.name, metrics=metrics)

    @staticmethod
    def _is_ours(rt: DeviceRuntime, proc: Dict[str, Any]) -> bool:
        if proc.get("name") not in (None, PROGRAM_NAME):
            return False
        caller = proc.get("caller")
        return caller is None or caller == rt.name or (rt.program_id is not None
                                                       and protocol.same_id(proc.get("uuid"), rt.program_id))

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

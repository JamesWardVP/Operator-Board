"""Multi-user (Concert) server control and connected-client tracking.

The server can run locally (spawned directly by Operator Board) or on any machine
running a SwitchboardListener. Connected clients are tracked by parsing the
server's log output with configurable patterns.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections import deque
from typing import Deque, List, Optional, Set

from operator_board.core.config import AppConfig
from operator_board.core.events import EventBus
from operator_board.devices.launcher import multiuser_server_spec, split_args
from operator_board.listener.client import ListenerClient, client_for_device, decode_stdout

log = logging.getLogger(__name__)

PROGRAM_NAME = "multiuser_server"


class ClientTracker:
    def __init__(self, join_pattern: str, leave_pattern: str) -> None:
        self.join_re = re.compile(join_pattern)
        self.leave_re = re.compile(leave_pattern)
        self.clients: Set[str] = set()
        self._anon = 0

    def _name(self, match: "re.Match[str]") -> str:
        name = (match.groupdict().get("name") or "").strip()
        if not name:
            self._anon += 1
            name = f"client-{self._anon}"
        return name

    def feed_line(self, line: str) -> bool:
        """Returns True if the client set changed."""
        m = self.leave_re.search(line)
        if m:
            name = self._name(m)
            if name in self.clients:
                self.clients.discard(name)
                return True
            if self.clients and name.startswith("client-"):
                self.clients.pop()
                return True
            return False
        m = self.join_re.search(line)
        if m:
            name = self._name(m)
            if name not in self.clients:
                self.clients.add(name)
                return True
        return False

    def reset(self) -> None:
        self.clients.clear()


class MultiUserServer:
    def __init__(self, config: AppConfig, bus: EventBus) -> None:
        self.config = config
        self.bus = bus
        self._proc: Optional[subprocess.Popen] = None
        self._remote: Optional[ListenerClient] = None
        self._remote_program: Optional[str] = None
        self.running = False
        self.started_at: Optional[float] = None
        self.log: Deque[str] = deque(maxlen=1000)
        self.last_error = ""
        self.tracker = self._new_tracker()

    def _new_tracker(self) -> ClientTracker:
        mu = self.config.multiuser
        return ClientTracker(mu.client_join_pattern, mu.client_leave_pattern)

    @property
    def client_count(self) -> int:
        return len(self.tracker.clients)

    @property
    def clients(self) -> List[str]:
        return sorted(self.tracker.clients)

    @property
    def server_name(self) -> str:
        return self.config.multiuser.server_name

    def _publish(self) -> None:
        self.bus.publish("multiuser.state", running=self.running, clients=self.clients,
                         server_name=self.server_name, error=self.last_error)

    def feed_output(self, text: str) -> None:
        changed = False
        for line in text.splitlines():
            if not line.strip():
                continue
            self.log.append(line)
            changed |= self.tracker.feed_line(line)
        if changed:
            self._publish()

    def start(self, server_name: Optional[str] = None, session_name: Optional[str] = None) -> None:
        if self.running:
            return
        mu = self.config.multiuser
        if server_name:
            mu.server_name = server_name
        if session_name:
            mu.session_name = session_name
        self.tracker = self._new_tracker()
        self.last_error = ""
        host = mu.host or "local"
        device = self.config.device(host) if host != "local" else None
        try:
            spec = multiuser_server_spec(self.config.project(), mu, device.platform if device else "Win64",
                                         device.engine_dir_override if device else "")
        except ValueError as exc:
            self.last_error = str(exc)
            self._publish()
            raise
        self.log.append(f"> {spec.as_command_line()}")
        if device is None:
            self._start_local(spec.exe, spec.args)
        else:
            self._start_remote(device, spec.exe, spec.args)
        self.running = True
        self.started_at = time.time()
        self._publish()

    def _start_local(self, exe: str, args: str) -> None:
        self._proc = subprocess.Popen([exe] + split_args(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, text=True, errors="replace")
        threading.Thread(target=self._pump_local, daemon=True, name="mu-server-log").start()

    def _pump_local(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            self.feed_output(line)
        code = proc.wait()
        self.log.append(f"< server exited ({code})")
        self._on_stopped()

    def _start_remote(self, device, exe: str, args: str) -> None:
        client = client_for_device(device, self.config.project().ue_version, on_message=self._on_remote_message,
                                   on_disconnect=self._on_remote_disconnect)
        client.connect()
        self._remote = client
        client.start_process(exe=exe, args=args, name=PROGRAM_NAME, caller="OperatorBoard",
                             update_clients_with_stdout=True)

    def _on_remote_disconnect(self, reason: str) -> None:
        if reason != "user disconnect":
            self._on_stopped(f"Listener connection lost: {reason}")

    def _on_remote_message(self, msg) -> None:
        cmd = msg.get("command")
        if cmd == "program started" and msg.get("name", PROGRAM_NAME) == PROGRAM_NAME:
            self._remote_program = msg.get("process_id") or msg.get("uuid")
        elif cmd == "programstdout":
            self.feed_output(decode_stdout(msg))
        elif cmd == "program ended":
            pid = msg.get("process_id") or msg.get("uuid")
            if pid is None or pid == self._remote_program:
                self.feed_output(decode_stdout(msg))
                self._on_stopped()
        elif cmd == "start" and msg.get("bAck") is False:
            self._on_stopped(f"Listener refused to start server: {msg.get('error', '')}")

    def _on_stopped(self, error: str = "") -> None:
        self.running = False
        self.started_at = None
        self.tracker.reset()
        if error:
            self.last_error = error
        self._publish()

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        if self._remote:
            if self._remote_program:
                try:
                    self._remote.kill_process(self._remote_program)
                except ConnectionError:
                    pass
            self._remote.disconnect("user disconnect")
            self._remote = None
            self._remote_program = None
        self._on_stopped()

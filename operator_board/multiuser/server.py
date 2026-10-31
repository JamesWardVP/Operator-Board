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
from operator_board.listener import protocol
from operator_board.listener.client import ListenerClient, client_for_device, decode_stdout

MU_CALLER = "OperatorBoard_MultiUserServer"

# "Refusing user james to join session X (..., Reason: Invalid project name (expected '', got 'P'))"
_PROJECT_REFUSAL = re.compile(r"Refusing user (?P<user>\S+) to join session .*Invalid project name "
                              r"\(expected '(?P<expected>[^']*)', got '(?P<got>[^']*)'\)")

# Logged by the server while it starts, before it reports itself ready (checked on 5.8): every live
# session it kept from last time (a forced stop keeps them) is recovered under its old name, so
# clients asking for that name would join the *old* session.
_RECOVERED = re.compile(r"Live session '(?P<name>.+?)' \([0-9A-Fa-f-]+\) was recovered")
_READY = "Multi-User Editing Server Initialized"
# A session the server now hosts: recovered, created by a client, or created at start-up.
_INITIALIZED = re.compile(r"Initialized Concert session '(?P<name>.+?)' \(Id")
_JOINED = re.compile(r"joined Concert session '(?P<name>.+?)' \(Id")
READY_TIMEOUT_S = 20.0

log = logging.getLogger(__name__)


def next_session_name(name: str, taken=()) -> str:
    """VP_Session -> VP_Session_2 -> VP_Session_3; Day1_07 -> Day1_08 (keeps zero padding); skips taken names."""
    m = re.match(r"^(.*?)(\d+)$", name)
    if m and m.group(1):
        stem, number, width = m.group(1), int(m.group(2)), len(m.group(2))
    else:
        stem, number, width = f"{name}_", 1, 1
    while True:
        number += 1
        candidate = f"{stem}{number:0{width}d}"
        if candidate not in taken:
            return candidate

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
        self.existing_sessions: Set[str] = set()  # sessions the server recovered when it started
        self.session_warning = ""  # set when this start moved to a new session because the name was taken
        self.clash: Optional[str] = None  # the session name found on the server, waiting for the operator
        self.session_live = False  # the configured session exists and is ours to use
        self._launch_session = ""  # the session the current server process was started with
        self._ready = threading.Event()
        self._retired: Set[str] = set()  # remote program ids replaced by a restart

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
                         server_name=self.server_name, error=self.last_error,
                         session=self.config.multiuser.session_name, session_warning=self.session_warning,
                         clash=self.clash, session_live=self.session_live)

    def feed_output(self, text: str) -> None:
        changed = False
        for line in text.splitlines():
            if not line.strip():
                continue
            self.log.append(line)
            changed |= self.tracker.feed_line(line)
            recovered = _RECOVERED.search(line)
            if recovered:
                self.existing_sessions.add(recovered.group("name"))
            if _READY in line:
                self._ready.set()
            name = self.config.multiuser.session_name
            made = _INITIALIZED.search(line) or _JOINED.search(line)
            # A session recovered while the server starts is the *old* one; it only counts once
            # the operator chose it (Start session) or a client creates or joins it afterwards.
            if made and made.group("name") == name and not self.session_live and (
                    self._ready.is_set() or self._launch_session == name):
                self.session_live = True
                changed = True
            refusal = _PROJECT_REFUSAL.search(line)
            if refusal:
                message = (f"The server refused {refusal.group('user')}: its session belongs to project "
                           f"'{refusal.group('expected')}', not '{refusal.group('got')}'. Stop the server, "
                           "tick 'Clean history' (Settings > Multi-user server) and start it again.")
                if message != self.last_error:
                    self.last_error = message
                    changed = True
        if changed:
            self._publish()

    def start(self, server_name: Optional[str] = None, session_name: Optional[str] = None,
              interactive: bool = False) -> None:
        """Start the server (no session yet) and check the session name against the sessions it kept.

        ``interactive``: an operator is at the UI, so a clash is handed to them (``clash`` and the
        ``multiuser.session_clash`` event, answered with ``resolve_clash``). Otherwise (auto start
        before an editor or node) a clash moves to the next name at once, with a warning.
        """
        if self.running:
            return
        mu = self.config.multiuser
        if server_name:
            mu.server_name = server_name
        if session_name:
            mu.session_name = session_name
        self.tracker = self._new_tracker()
        self.last_error = ""
        self.session_warning = ""
        self.existing_sessions = set()
        self.clash = None
        self.session_live = False
        self._launch("")
        self.started_at = time.time()
        # Clients are given the session name when they launch, so settle it before returning.
        if not self._ready.wait(READY_TIMEOUT_S):
            log.warning("The multi-user server did not report ready within %.0f s", READY_TIMEOUT_S)
        if mu.session_name in self.existing_sessions and interactive:
            self.clash = mu.session_name
            self.bus.publish("multiuser.session_clash", name=self.clash,
                             suggested=next_session_name(self.clash, self.existing_sessions))
            self._publish()
            return
        self._check_session_name()
        if mu.auto_start_session:
            self.start_session()

    def resolve_clash(self, increment: bool) -> None:
        """The operator's answer to a clash: move to the next free name, or keep (rejoin) the existing one."""
        if self.clash is None:
            return
        mu = self.config.multiuser
        old = self.clash
        self.clash = None
        if increment:
            mu.session_name = next_session_name(old, self.existing_sessions)
            self.session_warning = (f"New session '{mu.session_name}': it is NOT the previous session '{old}'.")
            self.bus.publish("multiuser.session_renamed", old=old, new=mu.session_name, reason="clash",
                             warning=self.session_warning)
        else:
            self.session_warning = f"Using the existing session '{old}' that the server kept from last time."
        self._publish()

    def start_session(self) -> None:
        """Make the configured session live now: restart the server (seconds, before anyone joins)
        so it creates the session for the project, or reuses it if it already exists."""
        if not self.running:
            raise RuntimeError("Start the multi-user server first")
        if self.clash is not None:
            raise RuntimeError(f"Choose whether to keep session '{self.clash}' or start a new one first")
        if self.session_live:
            return
        mu = self.config.multiuser
        self._terminate()
        self._launch(mu.session_name, clean=False)
        if not self._ready.wait(READY_TIMEOUT_S):
            log.warning("The multi-user server did not report ready within %.0f s", READY_TIMEOUT_S)
        self.session_live = True
        self._publish()

    def _launch(self, session: str, clean: Optional[bool] = None) -> None:
        mu = self.config.multiuser
        host = mu.host or "local"
        device = self.config.device(host) if host != "local" else None
        try:
            spec = multiuser_server_spec(self.config.project(), mu, device.platform if device else "Win64",
                                         device.engine_dir_override if device else "", session=session, clean=clean)
        except ValueError as exc:
            self.last_error = str(exc)
            self._publish()
            raise
        self._ready.clear()
        self._launch_session = session
        self.log.append(f"> {spec.as_command_line()}")
        if device is None:
            self._start_local(spec.exe, spec.args)
        else:
            self._start_remote(device, spec.exe, spec.args)
        self.running = True
        self._publish()

    def _check_session_name(self) -> None:
        mu = self.config.multiuser
        old = mu.session_name
        if old not in self.existing_sessions:
            return
        if not mu.auto_increment_session:
            self.session_warning = (f"Joining the existing session '{old}' that the server kept from last time "
                                    "(automatic session numbering is off).")
            self._publish()
            return
        new = next_session_name(old, self.existing_sessions)
        mu.session_name = new
        self.session_warning = (f"Session '{old}' already exists on the server, so this is a NEW session: '{new}'. "
                                f"It is NOT the previous session; work that was only in '{old}' is not in it.")
        log.warning(self.session_warning)
        self.bus.publish("multiuser.session_renamed", old=old, new=new, reason="exists",
                         warning=self.session_warning)
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
            if self._proc is not proc:
                break  # replaced by a restart
            self.feed_output(line)
        code = proc.wait()
        if self._proc is proc:  # not replaced by a restart
            self.log.append(f"< server exited ({code})")
            self._on_stopped()

    def _start_remote(self, device, exe: str, args: str) -> None:
        client = self._remote
        if client is None or not client.connected:
            client = client_for_device(device, self.config.project().ue_version,
                                       on_message=self._on_remote_message, on_disconnect=self._on_remote_disconnect)
            client.connect()
            self._remote = client
        client.start_process(exe=exe, args=args, name=PROGRAM_NAME, caller=MU_CALLER,
                             update_clients_with_stdout=True)

    def _on_remote_disconnect(self, reason: str) -> None:
        if reason != "user disconnect":
            self._on_stopped(f"Listener connection lost: {reason}")

    def _on_remote_message(self, msg) -> None:
        cmd = msg.get("command")
        proc = protocol.program_info(msg)
        if proc.get("caller") not in (None, MU_CALLER):
            return  # another program on the same listener
        if cmd == "program started" and msg.get("bAck") is not False:
            self._remote_program = proc.get("uuid")
        elif cmd == "program started":
            self._on_stopped(f"Listener could not start the server: {msg.get('error', '')}")
        elif cmd == "programstdout":
            self.feed_output(decode_stdout(msg))
        elif cmd == "program ended":
            if any(protocol.same_id(proc.get("uuid"), old) for old in self._retired):
                return  # the server process a restart replaced
            if self._remote_program is None or protocol.same_id(proc.get("uuid"), self._remote_program):
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

    def _terminate(self) -> None:
        """End the server process, leaving the remote listener connection open (for a restart)."""
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._remote and self._remote_program:
            self._retired.add(self._remote_program)
            try:
                self._remote.kill_process(self._remote_program)
            except ConnectionError:
                pass
        self._remote_program = None

    def stop(self) -> None:
        was_running = self.running
        self._terminate()
        if self._remote:
            self._remote.disconnect("user disconnect")
            self._remote = None
        self.clash = None
        self.session_live = False
        self._on_stopped()
        mu = self.config.multiuser
        if was_running and mu.auto_increment_session:
            # Every server run gets its own session; the next start (and every editor or node
            # launched after this) uses the next name.
            old = mu.session_name
            mu.session_name = next_session_name(old, self.existing_sessions)
            self.session_warning = ""
            self.bus.publish("multiuser.session_renamed", old=old, new=mu.session_name, reason="stopped",
                             warning="")
            self._publish()

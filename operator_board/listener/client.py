"""Client for one SwitchboardListener: plain TCP (UE 5.2 - 5.3) or QUIC (UE 5.4+)."""
from __future__ import annotations

import base64
import logging
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from operator_board.listener import protocol
from operator_board.listener.credentials import CredentialStore, default_store
from operator_board.listener.quic import ListenerAuthError, PasswordRejected, QuicListenerConnection, password_candidates

log = logging.getLogger(__name__)

MessageHandler = Callable[[Dict[str, Any]], None]

PROTOCOL_AUTO = "auto"
PROTOCOL_TCP = "tcp"
PROTOCOL_QUIC = "quic"
PROTOCOLS = (PROTOCOL_AUTO, PROTOCOL_QUIC, PROTOCOL_TCP)
QUIC_SINCE = (5, 4)  # first engine whose listener speaks QUIC + authentication

# Which protocol last worked for an address, so auto mode tries it first next time.
_last_protocol: Dict[Tuple[str, int], str] = {}


def protocol_order(choice: str, engine_version: Optional[str], address: str = "", port: int = 0) -> List[str]:
    if choice in (PROTOCOL_TCP, PROTOCOL_QUIC):
        return [choice]
    remembered = _last_protocol.get((address, port))
    if remembered:
        return [remembered] + [p for p in (PROTOCOL_QUIC, PROTOCOL_TCP) if p != remembered]
    try:
        newer = tuple(int(x) for x in str(engine_version).split(".")[:2]) >= QUIC_SINCE
    except ValueError:
        newer = True
    return [PROTOCOL_QUIC, PROTOCOL_TCP] if newer else [PROTOCOL_TCP, PROTOCOL_QUIC]


class ListenerClient:
    KEEPALIVE_S = 1.0
    CONNECT_TIMEOUT_S = 3.0

    def __init__(self, address: str, port: int = protocol.DEFAULT_PORT,
                 on_message: Optional[MessageHandler] = None,
                 on_disconnect: Optional[Callable[[str], None]] = None,
                 protocol_choice: str = PROTOCOL_AUTO, engine_version: Optional[str] = None,
                 store: Optional[CredentialStore] = None) -> None:
        self.address = address
        self.port = port
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.protocol_choice = protocol_choice
        self.engine_version = engine_version
        self.store = store
        self.transport = ""  # "tcp" or "quic" once connected
        self._sock: Optional[socket.socket] = None
        self._quic: Optional[QuicListenerConnection] = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list = []
        self.listener_version: Optional[str] = None

    @property
    def connected(self) -> bool:
        if self._quic is not None:
            return self._quic.connected
        return self._sock is not None

    def connect(self, password: Optional[str] = None, trust_new_fingerprint: bool = False) -> None:
        """Connect with the protocol for this engine, falling back to the other one.

        ListenerAuthError (password needed / rejected, security key changed) is raised
        as is: it means the listener was found, so trying the other protocol is pointless.
        """
        if self.connected:
            return
        errors = []
        for proto in protocol_order(self.protocol_choice, self.engine_version, self.address, self.port):
            try:
                if proto == PROTOCOL_QUIC:
                    self._connect_quic(password, trust_new_fingerprint)
                else:
                    self._connect_tcp()
            except ListenerAuthError:
                _last_protocol[(self.address, self.port)] = proto
                raise
            except (OSError, ConnectionError) as exc:
                errors.append(f"{proto.upper()}: {exc}")
                continue
            self.transport = proto
            _last_protocol[(self.address, self.port)] = proto
            return
        raise ConnectionError("; ".join(errors))

    def _connect_quic(self, password: Optional[str], trust_new_fingerprint: bool) -> None:
        candidates: List[Optional[str]] = list(password_candidates(password)) if password is not None else [None]
        for i, candidate in enumerate(candidates):
            conn = QuicListenerConnection(
                self.address, self.port, self.store or default_store(),
                on_message=self._dispatch, on_closed=self._quic_closed,
                password=candidate, trust_new_fingerprint=trust_new_fingerprint)
            try:
                conn.open()
            except PasswordRejected:
                if i == len(candidates) - 1:
                    raise
                continue
            self._quic = conn
            return

    def _quic_closed(self, reason: str) -> None:
        if self._quic is not None:
            self._quic = None
            if self.on_disconnect:
                self.on_disconnect(reason)

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        if msg.get("command") == "state" and "version" in msg:
            self.listener_version = str(msg.get("version"))
        if self.on_message:
            try:
                self.on_message(msg)
            except Exception:
                log.exception("Listener message handler failed")

    def _connect_tcp(self) -> None:
        sock = socket.create_connection((self.address, self.port), timeout=self.CONNECT_TIMEOUT_S)
        sock.settimeout(0.5)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._recv_loop, name=f"listener-rx-{self.address}", daemon=True),
            threading.Thread(target=self._keepalive_loop, name=f"listener-ka-{self.address}", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def disconnect(self, reason: str = "closed") -> None:
        quic, self._quic = self._quic, None
        if quic is not None:
            quic.close()
            if self.on_disconnect:
                self.on_disconnect(reason)
            return
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
            if self.on_disconnect:
                self.on_disconnect(reason)

    def send(self, payload: bytes) -> None:
        if self._quic is not None:
            self._quic.send(payload)
            return
        sock = self._sock
        if not sock:
            raise ConnectionError(f"Not connected to listener at {self.address}:{self.port}")
        with self._send_lock:
            try:
                sock.sendall(payload)
            except OSError as exc:
                self.disconnect(f"send failed: {exc}")
                raise ConnectionError(str(exc)) from exc

    # -- convenience wrappers ------------------------------------------------
    def start_process(self, **kwargs: Any) -> str:
        msg_id, payload = protocol.start_process(**kwargs)
        self.send(payload)
        return msg_id

    def kill_process(self, program_id: str) -> str:
        msg_id, payload = protocol.kill_process(program_id)
        self.send(payload)
        return msg_id

    def request_sync_status(self, program_id: str) -> str:
        msg_id, payload = protocol.get_sync_status(program_id)
        self.send(payload)
        return msg_id

    # -- loops ---------------------------------------------------------------
    def _recv_loop(self) -> None:
        buf = protocol.MessageBuffer()
        while not self._stop.is_set():
            sock = self._sock
            if not sock:
                return
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self.disconnect(f"receive failed: {exc}")
                return
            if not data:
                if not self._stop.is_set():
                    self.disconnect("listener closed the connection")
                return
            for msg in buf.feed(data):
                self._dispatch(msg)

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(self.KEEPALIVE_S):
            try:
                self.send(protocol.keepalive()[1])
            except ConnectionError:
                return


def client_for_device(device: Any, engine_version: Optional[str], **kwargs: Any) -> ListenerClient:
    """A ListenerClient set up from a DeviceConfig (address, port, protocol choice)."""
    return ListenerClient(device.address, device.listener_port,
                          protocol_choice=getattr(device, "listener_protocol", PROTOCOL_AUTO),
                          engine_version=engine_version, **kwargs)


def unreachable_hint(address: str, port: int) -> str:
    return (f"Is SwitchboardListener running on {address}, and is port {port} allowed through its firewall "
            f"(UDP for UE 5.4+, TCP for 5.2-5.3)?")


def decode_stdout(msg: Dict[str, Any]) -> str:
    """Extract stdout text from a 'programstdout' message."""
    raw = msg.get("partialStdoutB64") or msg.get("output") or ""
    if not raw:
        return ""
    try:
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return str(raw)


def wait_until(predicate: Callable[[], bool], timeout: float, interval: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()

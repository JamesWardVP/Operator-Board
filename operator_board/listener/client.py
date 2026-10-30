"""Threaded TCP client for one SwitchboardListener."""
from __future__ import annotations

import base64
import logging
import socket
import threading
import time
from typing import Any, Callable, Dict, Optional

from operator_board.listener import protocol

log = logging.getLogger(__name__)

MessageHandler = Callable[[Dict[str, Any]], None]


class ListenerClient:
    KEEPALIVE_S = 1.0
    CONNECT_TIMEOUT_S = 3.0

    def __init__(self, address: str, port: int = protocol.DEFAULT_PORT,
                 on_message: Optional[MessageHandler] = None,
                 on_disconnect: Optional[Callable[[str], None]] = None) -> None:
        self.address = address
        self.port = port
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list = []
        self.listener_version: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self._sock:
            return
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
                if msg.get("command") == "state" and "version" in msg:
                    self.listener_version = str(msg.get("version"))
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception:
                        log.exception("Listener message handler failed")

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(self.KEEPALIVE_S):
            try:
                self.send(protocol.keepalive()[1])
            except ConnectionError:
                return


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

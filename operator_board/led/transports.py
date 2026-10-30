"""Network transports used by LED processor profiles (HTTP, TCP, UDP, OSC).

These mirror the connection types Bitfocus Companion modules use: the operator
enters a host/IP and port, and commands go over the transport the processor
expects.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class TransportError(Exception):
    pass


class Transport:
    kind = "base"

    def __init__(self, host: str, port: int, timeout: float = 2.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def send(self, command: Dict[str, Any]) -> Any:
        raise NotImplementedError


class HttpTransport(Transport):
    kind = "http"

    def __init__(self, host: str, port: int, timeout: float = 2.0, scheme: str = "http",
                 username: str = "", password: str = "") -> None:
        super().__init__(host, port, timeout)
        self.scheme = scheme
        self.username = username
        self.password = password

    def base_url(self) -> str:
        default = 443 if self.scheme == "https" else 80
        port = "" if self.port in (0, default) else f":{self.port}"
        return f"{self.scheme}://{self.host}{port}"

    def send(self, command: Dict[str, Any]) -> Any:
        method = command.get("method", "GET").upper()
        url = self.base_url() + command.get("path", "/")
        headers = dict(command.get("headers") or {})
        body: Optional[bytes] = None
        if "json" in command:
            body = json.dumps(command["json"]).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif "body" in command:
            body = str(command["body"]).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        if self.username:
            mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, self.base_url(), self.username, self.password)
            opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(mgr),
                                                 urllib.request.HTTPDigestAuthHandler(mgr))
        else:
            opener = urllib.request.build_opener()
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise TransportError(f"HTTP {exc.code} from {url}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError(f"Cannot reach {url}: {exc}") from exc
        try:
            return json.loads(text) if text else None
        except ValueError:
            return text


def command_bytes(command: Dict[str, Any]) -> bytes:
    if "hex" in command:
        cleaned = command["hex"].replace(",", " ").replace("0x", "").split()
        return bytes(int(b, 16) for b in cleaned)
    if "text" in command:
        return str(command["text"]).encode("utf-8")
    if "bytes" in command:
        return bytes(command["bytes"])
    raise TransportError("Command has no payload (expected 'hex', 'text' or 'bytes')")


class TcpTransport(Transport):
    kind = "tcp"

    def __init__(self, host: str, port: int, timeout: float = 2.0, read_reply: bool = False) -> None:
        super().__init__(host, port, timeout)
        self.read_reply = read_reply
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def open(self) -> None:
        if self._sock is None:
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            except OSError as exc:
                raise TransportError(f"Cannot connect to {self.host}:{self.port}: {exc}") from exc

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def send(self, command: Dict[str, Any]) -> Any:
        data = command_bytes(command)
        with self._lock:
            for attempt in range(2):  # one transparent reconnect
                try:
                    self.open()
                    assert self._sock is not None
                    self._sock.sendall(data)
                    if self.read_reply or command.get("read_reply"):
                        self._sock.settimeout(self.timeout)
                        try:
                            return self._sock.recv(4096)
                        except socket.timeout:
                            return b""
                    return None
                except OSError as exc:
                    self.close()
                    if attempt:
                        raise TransportError(f"TCP send to {self.host}:{self.port} failed: {exc}") from exc
        return None


class UdpTransport(Transport):
    kind = "udp"

    def send(self, command: Dict[str, Any]) -> Any:
        data = command_bytes(command)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                sock.sendto(data, (self.host, self.port))
            except OSError as exc:
                raise TransportError(f"UDP send to {self.host}:{self.port} failed: {exc}") from exc
        return None


def _osc_string(s: str) -> bytes:
    b = s.encode("utf-8") + b"\x00"
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def encode_osc(address: str, args: List[Any]) -> bytes:
    tags = ","
    payload = b""
    for a in args:
        if isinstance(a, bool):
            tags += "T" if a else "F"
        elif isinstance(a, int):
            tags += "i"
            payload += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            payload += struct.pack(">f", a)
        else:
            tags += "s"
            payload += _osc_string(str(a))
    return _osc_string(address) + _osc_string(tags) + payload


class OscTransport(UdpTransport):
    kind = "osc"

    def send(self, command: Dict[str, Any]) -> Any:
        return super().send({"bytes": list(encode_osc(command["address"], command.get("args", [])))})


def make_transport(kind: str, host: str, port: int, options: Dict[str, Any]) -> Transport:
    timeout = float(options.get("timeout", 2.0))
    if kind in ("http", "https"):
        return HttpTransport(host, port, timeout, scheme=options.get("scheme", kind),
                             username=options.get("username", ""), password=options.get("password", ""))
    if kind == "tcp":
        return TcpTransport(host, port, timeout)
    if kind == "udp":
        return UdpTransport(host, port, timeout)
    if kind == "osc":
        return OscTransport(host, port, timeout)
    raise TransportError(f"Unknown transport '{kind}'")

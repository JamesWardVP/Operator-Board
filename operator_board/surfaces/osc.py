"""OSC control (TouchOSC, Open Stage Control, QLab, a lighting desk...) over UDP.

  /operator/key <int>            press key n of the current page (0 = top-left)
  /operator/key/<n> [value]      the same; a value of 0 (a button being released) is ignored
  /operator/action/<id> [args]   run any action; string args "name=value" become its parameters
  /operator/page next|prev|<n>   change surface page

Pure Python (no extra package): only the OSC 1.0 parts these messages need are decoded.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import Any, List, Optional, Tuple

from operator_board.surfaces.controller import SurfaceController
from operator_board.surfaces.layout import NAV_NEXT, NAV_PREV

log = logging.getLogger(__name__)
PREFIX = "/operator"


def _read_string(data: bytes, pos: int) -> Tuple[str, int]:
    end = data.index(b"\0", pos)
    text = data[pos:end].decode("utf-8", errors="replace")
    return text, (end + 4) & ~3  # strings are padded to a multiple of 4 bytes


def parse_message(data: bytes) -> Optional[Tuple[str, List[Any]]]:
    """(address, args) of one OSC message, or None for a bundle or anything malformed."""
    try:
        if data.startswith(b"#bundle"):
            return None
        address, pos = _read_string(data, 0)
        if not address.startswith("/"):
            return None
        args: List[Any] = []
        if pos < len(data) and data[pos:pos + 1] == b",":
            tags, pos = _read_string(data, pos)
            for tag in tags[1:]:
                if tag == "i":
                    args.append(struct.unpack(">i", data[pos:pos + 4])[0])
                    pos += 4
                elif tag == "f":
                    args.append(struct.unpack(">f", data[pos:pos + 4])[0])
                    pos += 4
                elif tag == "s":
                    value, pos = _read_string(data, pos)
                    args.append(value)
                elif tag in "TF":
                    args.append(tag == "T")
                elif tag in "NI":
                    args.append(None)
                else:
                    return address, args  # an argument type we do not need
        return address, args
    except (ValueError, struct.error, IndexError):
        return None


class OscServer:
    def __init__(self, controller: SurfaceController, port: int = 8000, bind: str = "0.0.0.0") -> None:
        self.controller = controller
        self.port = port
        self.bind = bind
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((self.bind, self.port))
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True, name="osc-server").start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
            self._sock = None

    def _loop(self) -> None:
        while not self._stop.is_set() and self._sock:
            try:
                data, _addr = self._sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            message = parse_message(data)
            if message:
                try:
                    self.handle(*message)
                except Exception:  # noqa: BLE001 - one bad message must not stop the server
                    log.exception("OSC message %s failed", message[0])

    def handle(self, address: str, args: List[Any]):
        """Act on one message; returns the action result (or None if it was not for us)."""
        if not address.startswith(PREFIX + "/"):
            return None
        parts = address[len(PREFIX) + 1:].split("/")
        if parts[0] == "key":
            if len(parts) > 1:  # /operator/key/<n> [value]
                if args and isinstance(args[0], (int, float)) and not args[0]:
                    return None  # button released
                return self.controller.press(int(parts[1]))
            if args:
                return self.controller.press(int(args[0]))
        elif parts[0] == "action" and len(parts) > 1:
            params = {}
            for a in args:
                if isinstance(a, str) and "=" in a:
                    k, v = a.split("=", 1)
                    params[k] = v
            return self.controller.actions.invoke(parts[1], **params)
        elif parts[0] == "page" and args:
            target = str(args[0]).lower()
            n = max(1, len(self.controller.layout.pages))
            if target in ("next", NAV_NEXT):
                self.controller.page = (self.controller.page + 1) % n
            elif target in ("prev", "previous", NAV_PREV):
                self.controller.page = (self.controller.page - 1) % n
            elif target.isdigit():
                self.controller.page = int(target) % n
            self.controller.bus.publish("surface.changed")
        return None

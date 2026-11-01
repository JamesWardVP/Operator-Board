"""OSC control (TouchOSC, Open Stage Control, QLab, a lighting desk...) over UDP.

  /operator/key <int>            press key n of the current page (0 = top-left)
  /operator/key/<n> [value]      the same; a value of 0 (a button being released) is ignored
  /operator/action/<id> [args]   run any action; string args "name=value" become its parameters
  /operator/page next|prev|<n>   change surface page
  /operator/surface/<name>/...   the same for one surface (its name, id or number, 1 = first);
                                 without it, messages go to the first OSC (else Custom) surface

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
    def __init__(self, surfaces, port: int = 8000, bind: str = "0.0.0.0") -> None:
        # A SurfaceManager (several surfaces) or a single SurfaceController.
        self.surfaces = surfaces
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

    def _target(self, ref: Optional[str] = None) -> Optional[SurfaceController]:
        if isinstance(self.surfaces, SurfaceController):
            return self.surfaces
        if ref is not None:
            return self.surfaces.find(ref)
        return self.surfaces.first_of(("osc", "custom"))

    def handle(self, address: str, args: List[Any]):
        """Act on one message; returns the action result (or None if it was not for us)."""
        if not address.startswith(PREFIX + "/"):
            return None
        parts = address[len(PREFIX) + 1:].split("/")
        if parts[0] == "surface" and len(parts) > 2:
            controller = self._target(parts[1])
            parts = parts[2:]
        else:
            controller = self._target()
        if controller is None:
            return None
        return self._handle(controller, parts, args)

    def _handle(self, controller: SurfaceController, parts: List[str], args: List[Any]):
        if parts[0] == "key":
            if len(parts) > 1:  # /operator/key/<n> [value]
                if args and isinstance(args[0], (int, float)) and not args[0]:
                    return None  # button released
                return controller.press(int(parts[1]))
            if args:
                return controller.press(int(args[0]))
        elif parts[0] == "action" and len(parts) > 1:
            params = {}
            for a in args:
                if isinstance(a, str) and "=" in a:
                    k, v = a.split("=", 1)
                    params[k] = v
            return controller.actions.invoke(parts[1], **params)
        elif parts[0] == "page" and args:
            target = str(args[0]).lower()
            n = max(1, len(controller.layout.pages))
            if target in ("next", NAV_NEXT):
                controller.page = (controller.page + 1) % n
            elif target in ("prev", "previous", NAV_PREV):
                controller.page = (controller.page - 1) % n
            elif target.isdigit():
                controller.page = int(target) % n
            controller.bus.publish("surface.changed")
        return None

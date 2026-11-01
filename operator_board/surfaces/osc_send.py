"""Sending OSC messages from a button (to a lighting desk, QLab, TouchDesigner, another Operator Board...).

A message is an address ("/cue/1/go") and arguments of one type, chosen as in Bitfocus Companion's
OSC module: none, an integer, a float, a string, true/false, or several arguments typed on one line
(``1 2.5 "two words" true``, each one's type worked out from how it is written).
"""
from __future__ import annotations

import shlex
import socket
from typing import Any, Dict, List

from operator_board.led.transports import encode_osc

OSC_TYPES: Dict[str, str] = {
    "none": "No arguments",
    "int": "Integer",
    "float": "Float",
    "string": "String",
    "bool": "True / False",
    "multi": "Several arguments",
}


def _auto(token: str) -> Any:
    low = token.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def osc_args(kind: str, value: Any) -> List[Any]:
    """The arguments of a message of ``kind`` with ``value`` (raises ValueError if it does not fit)."""
    if kind in ("", "none"):
        return []
    if kind == "int":
        return [int(str(value).strip())]
    if kind == "float":
        return [float(str(value).strip())]
    if kind == "string":
        return [str(value)]
    if kind == "bool":
        return [value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes", "on")]
    if kind == "multi":
        try:
            return [_auto(t) for t in shlex.split(str(value))]
        except ValueError as exc:  # an unclosed quote
            raise ValueError(f"arguments: {exc}") from exc
    raise ValueError(f"unknown OSC argument type '{kind}'")


def send_osc(host: str, port: int, address: str, kind: str = "none", value: Any = "") -> str:
    """Send one OSC message over UDP; returns what was sent, for the status line."""
    host, address = str(host).strip(), str(address).strip()
    if not host:
        raise ValueError("no IP address to send the OSC message to")
    if not address.startswith("/"):
        raise ValueError("the OSC address must start with / (for example /cue/1/go)")
    port = int(port)
    if not 0 < port < 65536:
        raise ValueError(f"port {port} is not between 1 and 65535")
    args = osc_args(kind, value)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(encode_osc(address, args), (host, port))
    shown = " ".join(repr(a) if isinstance(a, str) else str(a).lower() if isinstance(a, bool) else str(a)
                     for a in args)
    return f"OSC {address}{' ' + shown if shown else ''} sent to {host}:{port}"

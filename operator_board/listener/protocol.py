"""SwitchboardListener wire protocol.

The listener (SwitchboardListener.exe, TCP port 2980 by default) speaks
null-terminated UTF-8 JSON messages. These builders mirror the messages sent by
Switchboard's own message_protocol.py so the listener cannot tell Operator Board
apart from Switchboard itself.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Tuple

DELIMITER = b"\x00"
DEFAULT_PORT = 2980


def _encode(msg: Dict[str, Any]) -> bytes:
    return json.dumps(msg).encode("utf-8") + DELIMITER


def _new(command: str, **fields: Any) -> Tuple[str, bytes]:
    msg_id = str(uuid.uuid4())
    msg = {"command": command, "id": msg_id}
    msg.update(fields)
    return msg_id, _encode(msg)


def keepalive() -> Tuple[str, bytes]:
    return _new("keepalive")


def start_process(
    exe: str,
    args: str,
    name: str,
    caller: str,
    working_dir: str = "",
    update_clients_with_stdout: bool = False,
    force_window_focus: bool = False,
    lock_gpu_clock: bool = False,
    priority_modifier: int = 0,
    hide: bool = False,
) -> Tuple[str, bytes]:
    return _new(
        "start",
        exe=exe,
        args=args,
        name=name,
        caller=caller,
        working_dir=working_dir,
        bUpdateClientsWithStdout=update_clients_with_stdout,
        bForceWindowFocus=force_window_focus,
        bLockGpuClock=lock_gpu_clock,
        priority_modifier=priority_modifier,
        bHide=hide,
    )


def kill_process(program_id: str) -> Tuple[str, bytes]:
    return _new("kill", uuid=program_id)


def get_sync_status(program_id: str) -> Tuple[str, bytes]:
    return _new("get sync status", uuid=program_id)


def refresh_mosaics() -> Tuple[str, bytes]:
    return _new("refresh mosaics")


class MessageBuffer:
    """Accumulates raw socket bytes and yields complete decoded messages."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> List[Dict[str, Any]]:
        self._buf.extend(data)
        out: List[Dict[str, Any]] = []
        while True:
            idx = self._buf.find(DELIMITER)
            if idx < 0:
                break
            raw = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(msg, dict):
                out.append(msg)
        return out

"""SwitchboardListener wire protocol.

The listener (SwitchboardListener.exe, port 2980 by default) speaks null-terminated
UTF-8 JSON messages. Up to UE 5.3 they travel over plain TCP; from UE 5.4 over one
QUIC stream (UDP, ALPN "ue-switchboard") after an "authenticate" message (see
listener/quic.py). These builders mirror Switchboard's own message_protocol.py so
the listener cannot tell Operator Board apart from Switchboard itself.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

DELIMITER = b"\x00"
DEFAULT_PORT = 2980


def _encode(msg: Dict[str, Any]) -> bytes:
    return json.dumps(msg).encode("utf-8") + DELIMITER


def _new(command: str, **fields: Any) -> Tuple[str, bytes]:
    msg_id = str(uuid.uuid4())
    msg = {"command": command, "id": msg_id}
    msg.update(fields)
    return msg_id, _encode(msg)


def same_id(a: Any, b: Any) -> bool:
    """Message ids match regardless of format: the listener echoes them as Unreal
    FGuid strings ("12E1FCF989DC45DB8E998901B8B6313A"), not as sent ("12e1fcf9-89dc-...")."""
    try:
        return uuid.UUID(str(a)) == uuid.UUID(str(b))
    except ValueError:
        return False


def keepalive() -> Tuple[str, bytes]:
    return _new("keep alive")  # with a space, as in Switchboard's message_protocol.py (5.2 - 5.8)


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


# SyncStatusRequestFlags (UE 5.4+, SwitchboardTasks.h): the cheap per-machine metrics
# Operator Board graphs. Older listeners ignore the field and send everything.
SYNC_CPU_UTILIZATION = 1 << 7
SYNC_AVAILABLE_PHYSICAL_MEMORY = 1 << 8
SYNC_GPU_UTILIZATION = 1 << 9
SYNC_GPU_CORE_CLOCK = 1 << 10
SYNC_GPU_TEMPERATURE = 1 << 11
METRIC_REQUEST_FLAGS = (SYNC_CPU_UTILIZATION | SYNC_AVAILABLE_PHYSICAL_MEMORY | SYNC_GPU_UTILIZATION
                        | SYNC_GPU_CORE_CLOCK | SYNC_GPU_TEMPERATURE)


def get_sync_status(program_id: str, request_flags: int = METRIC_REQUEST_FLAGS) -> Tuple[str, bytes]:
    return _new("get sync status", uuid=program_id, request_flags=int(request_flags), bEcho=False)


def authenticate(jwt: Optional[str] = None, password: Optional[str] = None) -> Tuple[str, bytes]:
    """First message on a UE 5.4+ (QUIC) connection."""
    if not (jwt or password):
        raise ValueError("authenticate needs a token or a password")
    fields: Dict[str, Any] = {}
    if jwt:
        fields["jwt"] = jwt
    if password:
        fields["password"] = password
        fields["token"] = password  # pre-release 5.4 listeners read "token"
    return _new("authenticate", **fields)


def disconnect() -> Tuple[str, bytes]:
    return _new("disconnect")


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

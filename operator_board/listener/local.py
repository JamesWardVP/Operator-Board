"""The SwitchboardListener running on this PC: which engine it comes from, and stopping it.

Each engine ships its own listener (``<engine>/Engine/Binaries/Win64/SwitchboardListener.exe``) and
only one can use port 2980, so a 5.6 listener left running blocks a 5.8 stage (5.4+ listeners also
talk QUIC, older ones TCP). Operator Board compares the running listener's engine with the open
Switchboard config's and offers to swap it.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

from operator_board.core.ue_versions import detect_engine_version

log = logging.getLogger(__name__)

EXE = "SwitchboardListener.exe"
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


@dataclass
class RunningListener:
    pid: int
    path: str  # the program file ("" if Windows would not say)

    @property
    def engine_dir(self) -> str:
        """The engine install the listener belongs to (the folder holding ``Engine``)."""
        parts = os.path.normpath(self.path).split(os.sep)
        lowered = [p.lower() for p in parts]
        if "engine" in lowered and "binaries" in lowered:
            return os.sep.join(parts[:lowered.index("engine")])
        return ""

    @property
    def version(self) -> Optional[str]:
        return detect_engine_version(self.engine_dir) if self.engine_dir else None


def _image_path(pid: int) -> str:
    """The full path of a process's program (Windows), or ""."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return ""


def running_listeners() -> List[RunningListener]:
    """Every SwitchboardListener running on this PC (Windows only; empty elsewhere)."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, creationflags=_NO_WINDOW, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for row in csv.reader(io.StringIO(out)):
        if len(row) >= 2 and row[0].lower() == EXE.lower() and row[1].isdigit():
            pid = int(row[1])
            found.append(RunningListener(pid, _image_path(pid)))
    return found


def stop_listener(pid: int, wait_s: float = 8.0) -> bool:
    """Ask the listener to close, and end it if it has not after ``wait_s``. True once it is gone."""
    def alive() -> bool:
        return any(l.pid == pid for l in running_listeners())
    for args in (["taskkill", "/PID", str(pid)], ["taskkill", "/PID", str(pid), "/F"]):
        try:
            subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Could not stop SwitchboardListener %s: %s", pid, exc)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if not alive():
                return True
            time.sleep(0.5)
    return not alive()

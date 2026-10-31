"""Timecode (from the primary editor, over Remote Control) and genlock (from the listeners' NVIDIA sync data).

Both read as ``None`` when there is nothing trustworthy to show, so the UI can say "No data"
instead of showing zeros.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from operator_board.remote import scripts
from operator_board.remote.rc_client import python_result_wrapper

log = logging.getLogger(__name__)

STALE_S = 3.0  # an editor sample older than this is "No data"


@dataclass
class EditorTiming:
    """One sample of the editor's engine timecode."""
    hours: int
    minutes: int
    seconds: int
    frames: int
    rate_num: int
    rate_den: int
    drop_frame: bool = False
    time_step: str = ""      # the engine's custom time step (genlock source in the editor), "" if none
    time_step_fps: float = 0.0
    sampled: float = 0.0     # time.monotonic() when it was read

    @property
    def fps(self) -> float:
        return self.rate_num / self.rate_den if self.rate_den else 0.0


def parse_editor_timing(result: Any, now: Optional[float] = None) -> Optional[EditorTiming]:
    """Turn the ``editor_timing`` script result into an EditorTiming, or None when it has no usable timecode."""
    if not isinstance(result, dict):
        return None
    try:
        h, m, s, f = (int(v) for v in result["timecode"])
        num, den = (int(v) for v in result["rate"])
    except (KeyError, TypeError, ValueError):
        return None
    if num <= 0 or den <= 0 or (h, m, s, f) == (0, 0, 0, 0):
        return None  # no timecode provider running: the engine reports zeros
    return EditorTiming(h, m, s, f, num, den, bool(result.get("drop_frame")),
                        str(result.get("time_step") or ""), float(result.get("time_step_fps") or 0.0),
                        time.monotonic() if now is None else now)


def format_timecode(t: EditorTiming, now: Optional[float] = None) -> str:
    """The sample's timecode moved on by the time since it was read (so it ticks between polls)."""
    now = time.monotonic() if now is None else now
    fps = t.fps
    nominal = max(1, int(round(fps)))
    total = ((t.hours * 60 + t.minutes) * 60 + t.seconds) * nominal + t.frames
    total += int(max(0.0, now - t.sampled) * fps)
    total %= 24 * 3600 * nominal
    frames = total % nominal
    secs = total // nominal
    sep = ";" if t.drop_frame else ":"
    return f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}{sep}{frames:02d}"


def genlock_from_metrics(metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Genlock of one machine from its listener sync status (``syncTopos``, as Switchboard reads it).

    None when the machine reports no sync topology (no NVIDIA sync board, or an old listener).
    """
    topos = metrics.get("syncTopos") if isinstance(metrics, dict) else None
    if not isinstance(topos, list) or not topos:
        return None
    gpus = [bool(g.get("bIsSynced")) for t in topos for g in (t.get("syncGpus") or []) if isinstance(g, dict)]
    if not gpus:
        return None
    params = topos[0].get("syncStatusParams") or {}
    source = {0: "Vsync", 1: "House"}.get((topos[0].get("syncControlParams") or {}).get("source"), "")
    house = float(params.get("houseSyncIncoming") or 0) * 1e-4 if params.get("bHouseSync") else 0.0
    return {"locked": all(gpus), "source": source, "house_fps": house,
            "refresh": float(params.get("refreshRate") or 0) * 1e-4}


def genlock_summary(per_device: Iterable[Tuple[str, Dict[str, Any]]],
                    editor: Optional[EditorTiming] = None) -> Tuple[Optional[str], Optional[bool]]:
    """(text, ok) for the Operate page; (None, None) when no machine or editor reports genlock."""
    locked, free = [], []
    detail = ""
    for name, metrics in per_device:
        g = genlock_from_metrics(metrics)
        if g is None:
            continue
        (locked if g["locked"] else free).append(name)
        if not detail and g["locked"]:
            detail = f"house {g['house_fps']:.3f}" if g["house_fps"] else g["source"].lower()
    if free:
        return f"NOT LOCKED: {', '.join(free)}", False
    if locked:
        count = "all machines" if len(locked) > 1 else locked[0]
        return f"Locked ({count}{', ' + detail if detail else ''})", True
    if editor is not None and editor.time_step:
        rate = f" {editor.time_step_fps:g} fps" if editor.time_step_fps else ""
        # Unreal does not expose the time step's lock state to Python, so this is the setting, not proof.
        return f"Editor time step: {editor.time_step}{rate}", None
    return None, None


class TimingMonitor:
    """Polls the primary editor's timecode in the background."""

    POLL_S = 1.0
    IDLE_POLL_S = 2.0  # when no instance is running, try less often (still inside STALE_S)

    def __init__(self, targets, devices) -> None:
        self.targets = targets
        self.devices = devices
        self.editor: Optional[EditorTiming] = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="timing-poll").start()

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> Optional[EditorTiming]:
        try:
            result = self.targets.primary().run_python(python_result_wrapper(scripts.editor_timing()))
            self.editor = parse_editor_timing(result)
        except Exception as exc:  # noqa: BLE001 - no editor running is normal
            log.debug("timecode poll: %s", exc)
            self.editor = None
        return self.editor

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self.POLL_S if self.devices.running() else self.IDLE_POLL_S)

    def timecode(self, now: Optional[float] = None) -> Optional[str]:
        t = self.editor
        now = time.monotonic() if now is None else now
        if t is None or now - t.sampled > STALE_S:
            return None
        return format_timecode(t, now)

    def rate_text(self) -> str:
        t = self.editor
        return f"{t.fps:.3f}".rstrip("0").rstrip(".") + " fps" if t else ""

    def genlock(self) -> Tuple[Optional[str], Optional[bool]]:
        editor = self.editor if self.timecode() is not None else None
        return genlock_summary(((rt.name, rt.last_metrics) for rt in self.devices.running()), editor)

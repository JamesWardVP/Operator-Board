"""Slate / take management with a persistent take log, optionally driving Take Recorder."""
from __future__ import annotations

import csv
import json
import logging
import threading
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

from operator_board.core.config import AppConfig, app_home, atomic_write
from operator_board.core.events import EventBus
from operator_board.core.serde import from_dict, to_dict
from operator_board.remote import scripts
from operator_board.remote.rc_client import python_result_wrapper
from operator_board.remote.targets import TargetResolver

log = logging.getLogger(__name__)

_NO_SOURCES = "no currently enabled or valid sources"
NO_SOURCES_HELP = ("Take Recorder has nothing to record. In the editor's Take Recorder panel, add sources "
                   "(+ Source: for example your camera or actors), or save them as a Take Preset and enter its "
                   "path under Operator Board > Takes > Take preset, so every recording uses it.")


@dataclass
class TakeRecord:
    slate: str
    take: int
    description: str = ""
    level: str = ""
    started: float = 0.0
    stopped: float = 0.0
    rating: str = ""  # "", "good", "ng", "circle"
    notes: str = ""
    sequence: str = ""  # the Take Recorder level sequence, once known
    saved: str = ""     # "", "saved" or "persisted" (written into the project from a multi-user session)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.stopped - self.started) if self.stopped else 0.0


@dataclass
class TakeLog:
    takes: List[TakeRecord] = field(default_factory=list)


class TakeManager:
    SAVE_TIMEOUT_S = 180.0
    def __init__(self, config: AppConfig, bus: EventBus, targets: Optional[TargetResolver] = None) -> None:
        self.config = config
        self.bus = bus
        self.targets = targets
        self.log = TakeLog()
        self.notice = ""  # what the last start did besides recording, e.g. which camera it added
        self.warning = ""  # a problem Unreal is likely to hit with the take being recorded (long paths)
        self._sync_lock = threading.Lock()
        self._save_lock = threading.Lock()  # the log is also saved from background threads
        self._sync_wanted = False
        self._sync_running = False
        self.current: Optional[TakeRecord] = None
        # None, "countdown" (Take Recorder is counting down) or "recording"
        self.phase: Optional[str] = None
        self._countdown_end = 0.0
        self._run = 0  # bumped on every start/stop so stale watcher threads give up
        self.load()

    @property
    def settings(self):
        return self.config.takes

    @property
    def log_path(self) -> str:
        return self.settings.take_log_path or os.path.join(app_home(), "take_log.json")

    @property
    def recording(self) -> bool:
        """A take is under way (counting down or recording)."""
        return self.current is not None

    @property
    def counting_down(self) -> bool:
        return self.phase == "countdown"

    def countdown_remaining(self) -> float:
        return max(0.0, self._countdown_end - time.monotonic()) if self.phase == "countdown" else 0.0

    def load(self) -> None:
        if os.path.exists(self.log_path):
            with open(self.log_path, "r", encoding="utf-8") as fh:
                self.log = from_dict(TakeLog, json.load(fh))

    def save(self) -> None:
        with self._save_lock:
            atomic_write(self.log_path, json.dumps(to_dict(self.log), indent=2))

    def export_csv(self, path: str) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["slate", "take", "description", "level", "start", "duration_s", "rating", "notes"])
            for t in self.log.takes:
                w.writerow([t.slate, t.take, t.description, t.level,
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.started)) if t.started else "",
                            f"{t.duration_s:.1f}", t.rating, t.notes])

    def set_slate(self, slate: str, take: Optional[int] = None, description: Optional[str] = None) -> None:
        self.settings.slate = slate
        if take is not None:
            self.settings.take = max(1, int(take))
        elif self.settings.auto_increment:
            self.settings.take = self.next_take_for(slate)
        if description is not None:
            self.settings.description = description
        self._sync_editor()
        self._publish()

    def next_take_for(self, slate: str) -> int:
        nums = [t.take for t in self.log.takes if t.slate == slate]
        return max(nums) + 1 if nums else 1

    def set_take(self, take: int) -> None:
        self.settings.take = max(1, int(take))
        self._sync_editor()
        self._publish()

    def increment(self, delta: int = 1) -> None:
        self.set_take(self.settings.take + delta)

    def start(self, level: str = "") -> List[str]:
        if self.recording:
            return []
        s = self.settings
        errors = []
        self.notice = self.warning = ""
        countdown = 0.0
        if s.record_with_take_recorder and self.targets:
            try:
                try:
                    countdown = self._start_take_recorder(s.slate, s.take, s.description)
                except Exception as exc:
                    if _NO_SOURCES not in str(exc) or not s.auto_add_camera:
                        raise
                    # The take is empty: add the stage camera, then try once more.
                    added = self.targets.primary().run_python(
                        python_result_wrapper(scripts.take_recorder_add_camera(s.take_camera)))
                    self.notice = f"added {added.get('added')} to Take Recorder ({added.get('why')})"
                    countdown = self._start_take_recorder(s.slate, s.take, s.description)
            except Exception as exc:
                # Unreal is not recording, so neither is Operator Board: no take is logged.
                errors.append(f"Take Recorder: {NO_SOURCES_HELP if _NO_SOURCES in str(exc) else exc}")
                self._publish()
                return errors
        self.current = TakeRecord(slate=s.slate, take=s.take, description=s.description, level=level, started=time.time())
        self._run += 1
        if countdown > 0:
            # Take Recorder counts down before it records: follow it, so the operator sees the
            # countdown and "recording" only once Unreal really is.
            self.phase = "countdown"
            self._countdown_end = time.monotonic() + countdown
            threading.Thread(target=self._watch_start, args=(self._run, countdown), daemon=True,
                             name="take-countdown").start()
        else:
            self.phase = "recording"
        self._publish()
        return errors

    def _watch_start(self, run: int, countdown: float) -> None:
        deadline = time.monotonic() + countdown + 15.0
        idle = 0
        while run == self._run and time.monotonic() < deadline:
            time.sleep(0.2)
            try:
                st = self.targets.primary().run_python(python_result_wrapper(scripts.take_recorder_status()))
            except Exception as exc:  # noqa: BLE001 - keep following on the local clock
                log.debug("Take Recorder status: %s", exc)
                continue
            if run != self._run or not isinstance(st, dict):
                return
            phase = st.get("phase")
            if phase == "counting_down":
                self._countdown_end = time.monotonic() + float(st.get("countdown") or 0.0)
            elif phase == "recording":
                break
            elif phase == "cancelled" or (phase == "idle" and not st.get("recording")):
                idle += 1
                if idle >= 2:  # cancelled in the editor: there is no take
                    self.current, self.phase = None, None
                    self.notice = "cancelled in the editor"
                    self._publish()
                    return
        if run == self._run and self.current is not None:
            self.phase = "recording"
            self.current.started = time.time()
            self._publish()

    def _sync_editor(self) -> None:
        """Show the slate / take in the editor's Take Recorder panel as soon as it changes (best effort)."""
        if not (self.settings.record_with_take_recorder and self.targets) or self.recording:
            return
        # One background sender (the editor may be slow or away, and "Apply slate" runs on the UI
        # thread). It sends the *latest* values until nothing changed, so quick Take +/- presses
        # cannot arrive out of order.
        with self._sync_lock:
            self._sync_wanted = True
            if self._sync_running:
                return
            self._sync_running = True
        threading.Thread(target=self._sync_worker, daemon=True, name="take-recorder-sync").start()

    def _sync_worker(self) -> None:
        while True:
            with self._sync_lock:
                if not self._sync_wanted:
                    self._sync_running = False
                    return
                self._sync_wanted = False
            s = self.settings
            if self.recording:
                continue
            try:
                self.targets.primary().run_python(
                    python_result_wrapper(scripts.take_recorder_set_metadata(s.slate, s.take, s.description)))
            except Exception as exc:  # noqa: BLE001 - no editor running is normal
                log.debug("Take Recorder sync skipped: %s", exc)

    def _start_take_recorder(self, slate: str, take: int, description: str, attempts: int = 6) -> float:
        """Start Take Recorder; returns its countdown in seconds."""
        editor = self.targets.primary()
        if self.settings.take_preset:
            editor.run_python(python_result_wrapper(scripts.take_recorder_apply_preset(self.settings.take_preset)))
        script = python_result_wrapper(scripts.take_recorder_start(slate, take, description))
        for _ in range(attempts):
            result = editor.run_python(script)
            if not (isinstance(result, dict) and result.get("retry")):
                result = result if isinstance(result, dict) else {}
                self.warning = str(result.get("warning") or "")
                return float(result.get("countdown") or 0.0)
            time.sleep(0.5)  # the Take Recorder panel was just opened; give the editor a tick
        raise RuntimeError("the Take Recorder panel did not become ready in the editor")

    def cancel(self) -> List[str]:
        """Abandon a take still counting down: nothing was recorded, so nothing is logged."""
        errors = []
        self._run += 1
        if self.settings.record_with_take_recorder and self.targets:
            try:
                self.targets.primary().run_python(python_result_wrapper(scripts.take_recorder_cancel()))
            except Exception as exc:
                errors.append(f"Take Recorder: {exc}")
        self.current, self.phase = None, None
        self._publish()
        return errors

    def stop(self) -> List[str]:
        if not self.recording:
            return []
        if self.counting_down:
            return self.cancel()
        self._run += 1
        self.phase = None
        errors = []
        if self.settings.record_with_take_recorder and self.targets:
            try:
                self.targets.primary().run_python(python_result_wrapper(scripts.take_recorder_stop()))
            except Exception as exc:
                errors.append(f"Take Recorder: {exc}")
        assert self.current is not None
        self.current.stopped = time.time()
        self.log.takes.append(self.current)
        if self.settings.record_with_take_recorder and self.targets and not errors:
            previous = next((t.sequence for t in reversed(self.log.takes[:-1]) if t.sequence), "")
            threading.Thread(target=self._find_sequence, args=(self.current, previous), daemon=True,
                             name="take-sequence").start()
        self.current = None
        if self.settings.auto_increment:
            self.settings.take += 1
        self.save()
        self._publish()
        return errors

    def _find_sequence(self, record: TakeRecord, previous: str, wait_s: float = 10.0) -> None:
        """Remember which level sequence Take Recorder wrote for the take (it finishes a moment after stop)."""
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                found = self.targets.primary().run_python(python_result_wrapper(scripts.take_recorder_last_sequence()))
            except Exception as exc:  # noqa: BLE001
                log.debug("last recorded sequence: %s", exc)
                found = None
            if (isinstance(found, dict) and found.get("sequence") and found["sequence"] != previous
                    and not found.get("recording")):
                record.sequence = found["sequence"]
                self.save()
                self._publish()
                return
            time.sleep(0.5)

    def save_take(self, index: Optional[int] = None) -> str:
        """Save a recorded take in the editor (and persist it out of a multi-user session). Returns a summary."""
        if not (self.settings.record_with_take_recorder and self.targets):
            raise RuntimeError("Takes are only saved in Unreal when Operator Board drives Take Recorder")
        if not self.log.takes:
            raise RuntimeError("No recorded take to save yet")
        record = self.log.takes[-1 if index is None else index]
        editor = self.targets.primary()
        old_timeout = getattr(editor, "timeout", None)
        try:
            if old_timeout is not None:
                editor.timeout = max(old_timeout, self.SAVE_TIMEOUT_S)  # big takes take a while to write
            result = editor.run_python(python_result_wrapper(
                scripts.take_recorder_save(record.sequence, record.slate, record.take)))
        finally:
            if old_timeout is not None:
                editor.timeout = old_timeout
        result = result if isinstance(result, dict) else {}
        record.sequence = str(result.get("sequence") or record.sequence)
        if result.get("unsaved"):
            raise RuntimeError("Unreal could not save " + ", ".join(result["unsaved"]))
        record.saved = "persisted" if result.get("persisted") else "saved"
        self.save()
        self._publish()
        name = f"{record.slate} T{record.take:03d}"
        if result.get("multi_user"):
            if result.get("persisted"):
                return f"Saved {name} and wrote it into the project from the multi-user session"
            return f"{name} is saved in the multi-user session (nothing new to write into the project)"
        return f"Saved {name}"

    def toggle(self) -> List[str]:
        return self.stop() if self.recording else self.start()

    def rate(self, index: int, rating: str, notes: Optional[str] = None) -> None:
        take = self.log.takes[index]
        take.rating = rating
        if notes is not None:
            take.notes = notes
        self.save()
        self._publish()

    def rate_last(self, rating: str) -> None:
        if self.log.takes:
            self.rate(len(self.log.takes) - 1, rating)

    def _publish(self) -> None:
        s = self.settings
        self.bus.publish("takes.state", slate=s.slate, take=s.take, recording=self.recording, phase=self.phase)

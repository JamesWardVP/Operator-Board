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

    @property
    def duration_s(self) -> float:
        return max(0.0, self.stopped - self.started) if self.stopped else 0.0


@dataclass
class TakeLog:
    takes: List[TakeRecord] = field(default_factory=list)


class TakeManager:
    def __init__(self, config: AppConfig, bus: EventBus, targets: Optional[TargetResolver] = None) -> None:
        self.config = config
        self.bus = bus
        self.targets = targets
        self.log = TakeLog()
        self.notice = ""  # what the last start did besides recording, e.g. which camera it added
        self._sync_lock = threading.Lock()
        self._sync_wanted = False
        self._sync_running = False
        self.current: Optional[TakeRecord] = None
        self.load()

    @property
    def settings(self):
        return self.config.takes

    @property
    def log_path(self) -> str:
        return self.settings.take_log_path or os.path.join(app_home(), "take_log.json")

    @property
    def recording(self) -> bool:
        return self.current is not None

    def load(self) -> None:
        if os.path.exists(self.log_path):
            with open(self.log_path, "r", encoding="utf-8") as fh:
                self.log = from_dict(TakeLog, json.load(fh))

    def save(self) -> None:
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
        self.notice = ""
        if s.record_with_take_recorder and self.targets:
            try:
                try:
                    self._start_take_recorder(s.slate, s.take, s.description)
                except Exception as exc:
                    if _NO_SOURCES not in str(exc) or not s.auto_add_camera:
                        raise
                    # The take is empty: add the stage camera, then try once more.
                    added = self.targets.primary().run_python(
                        python_result_wrapper(scripts.take_recorder_add_camera(s.take_camera)))
                    self.notice = f"added {added.get('added')} to Take Recorder ({added.get('why')})"
                    self._start_take_recorder(s.slate, s.take, s.description)
            except Exception as exc:
                # Unreal is not recording, so neither is Operator Board: no take is logged.
                errors.append(f"Take Recorder: {NO_SOURCES_HELP if _NO_SOURCES in str(exc) else exc}")
                self._publish()
                return errors
        self.current = TakeRecord(slate=s.slate, take=s.take, description=s.description, level=level, started=time.time())
        self._publish()
        return errors

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

    def _start_take_recorder(self, slate: str, take: int, description: str, attempts: int = 6) -> None:
        editor = self.targets.primary()
        if self.settings.take_preset:
            editor.run_python(python_result_wrapper(scripts.take_recorder_apply_preset(self.settings.take_preset)))
        script = python_result_wrapper(scripts.take_recorder_start(slate, take, description))
        for _ in range(attempts):
            result = editor.run_python(script)
            if not (isinstance(result, dict) and result.get("retry")):
                return
            time.sleep(0.5)  # the Take Recorder panel was just opened; give the editor a tick
        raise RuntimeError("the Take Recorder panel did not become ready in the editor")

    def stop(self) -> List[str]:
        if not self.recording:
            return []
        errors = []
        if self.settings.record_with_take_recorder and self.targets:
            try:
                self.targets.primary().run_python(python_result_wrapper(scripts.take_recorder_stop()))
            except Exception as exc:
                errors.append(f"Take Recorder: {exc}")
        assert self.current is not None
        self.current.stopped = time.time()
        self.log.takes.append(self.current)
        self.current = None
        if self.settings.auto_increment:
            self.settings.take += 1
        self.save()
        self._publish()
        return errors

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
        self.bus.publish("takes.state", slate=s.slate, take=s.take, recording=self.recording)

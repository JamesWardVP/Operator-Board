"""Colour grading: the shared target selection, live sending while a control is dragged, delivery to
the editor and the nDisplay nodes, bypass, reset, looks and nudges.

Delivery (spec "Delivery during a drag"): every change goes to the primary editor (plain set, no
transaction) and straight to every running nDisplay node, so the wall follows a drag even when
multi-user is slow or absent. When the gesture ends the editor records the final values once
inside an editor transaction. With no editor, changes go to every node. A node that misses a
change is named in a ``grading.problem`` event and is sent the latest values again until it answers.

Events: ``grading.targets``, ``grading.selected`` (key), ``grading.changed`` (key, grade),
``grading.bypass`` (key, on), ``grading.problem`` (message).
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from operator_board.core.grading import ALL_KEYS, CONTROLS, Grade, nudge as nudge_grade
from operator_board.core.looks import BypassState, Look, LookStore, target_key
from operator_board.remote import scripts
from operator_board.remote.rc_client import RemoteControlError, python_result_wrapper
from operator_board.remote.targets import TARGET_NDISPLAY

log = logging.getLogger(__name__)
CATEGORY = "Colour Grading"
GONE = "Target no longer in the level"


class GradingProblem(Exception):
    """A grading change or read that could not be done (the message is for the operator)."""


def _unreachable(exc: Exception) -> bool:
    return isinstance(exc, RemoteControlError) and "unreachable" in str(exc)


def _client_name(client) -> str:
    return f"{getattr(client, 'host', '?')}:{getattr(client, 'port', '')}".rstrip(":")


class GradingService:
    def __init__(self, config, targets, bus, looks: LookStore, bypass: BypassState,
                 record: Optional[Callable[..., Any]] = None, retry_s: float = 2.0, min_interval_s: float = 0.05) -> None:
        self.config = config
        self.targets = targets
        self.bus = bus
        self.looks = looks
        self.bypass = bypass
        self.record = record
        self.retry_s = retry_s
        self.min_interval_s = min_interval_s
        self.targets_list: List[Dict[str, Any]] = []
        self.missing: Dict[str, str] = {}
        self.current: Dict[str, Grade] = {}
        self.enabled: Dict[str, bool] = {}
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._pending: Dict[str, Dict[str, Any]] = {}   # key -> values waiting for the sender
        self._busy = set()                               # keys the sender is sending now
        self._sender: Optional[threading.Thread] = None
        self._gesture_start: Dict[str, Grade] = {}
        self._gesture_values: Dict[str, Dict[str, Any]] = {}
        self._missed: Dict[Tuple[str, str], Tuple[Any, Dict[str, Any]]] = {}  # (client, key) -> (client, values)
        self._retrier: Optional[threading.Thread] = None

    # ---- targets and selection ---------------------------------------------------------------
    @property
    def selected(self) -> str:
        chosen = self.config.grading.selected
        keys = [t["key"] for t in self.targets_list]
        if chosen in keys or not keys:
            return chosen
        return keys[0]

    def select(self, key: str) -> None:
        self.config.grading.selected = key
        self.bus.publish("grading.selected", key=key)

    def refresh_targets(self) -> List[Dict[str, Any]]:
        script = python_result_wrapper(scripts.list_grading_targets())
        result = self.targets.editor_or_nodes(lambda c: c.run_python(script))[0]
        found = []
        for t in (result or {}).get("targets", []):
            t = dict(t)
            t["nth"] = int(t.get("nth") or 0)
            t["key"] = target_key(t["kind"], t["actor"], t.get("applies_to") or []) + (f"#{t['nth'] + 1}" if t["nth"] else "")
            found.append(t)
        with self._lock:
            self.targets_list = found
            self.missing = dict((result or {}).get("missing") or {})
        self.bus.publish("grading.targets", targets=found, missing=self.missing)
        return found

    def target(self, key: str) -> Dict[str, Any]:
        found = next((t for t in self.targets_list if t["key"] == key), None)
        if found is None:
            raise GradingProblem(f"{GONE}: {key.split('|')[1] if '|' in key else key}")
        return found

    def _key(self, key: str) -> str:
        key = key or self.selected
        if not key:
            if not self.targets_list:
                self.refresh_targets()
            key = self.selected
        if not key:
            raise GradingProblem("There is nothing to grade in the open level")
        return key

    # ---- reading -------------------------------------------------------------------------------
    def read(self, key: str) -> Grade:
        t = self.target(key)
        script = python_result_wrapper(scripts.read_grade(t["kind"], t["actor"], t["applies_to"], t["nth"]))
        try:
            result = self.targets.editor_or_nodes(lambda c: c.run_python(script))[0]
        except RemoteControlError as exc:
            raise GradingProblem(str(exc)) from exc
        grade = Grade.from_dict((result or {}).get("grade") or {})
        with self._lock:
            self.current[key] = grade
            self.enabled[key] = bool((result or {}).get("enabled", True))
        return grade

    # ---- delivery -----------------------------------------------------------------------------
    def _deliver(self, key: str, script_for: Callable[[bool], str], transact: bool, nodes: bool = True) -> None:
        """Run a change on the editor (``transact``) and on every running node. Raises
        GradingProblem when nobody took it or the target is gone; a node that misses it is retried."""
        editor = None
        try:
            editor = self.targets.primary()
        except LookupError:
            editor = None
        took = 0
        if editor is not None:
            try:
                editor.run_python(script_for(transact))
                took += 1
            except RemoteControlError as exc:
                if not _unreachable(exc):
                    raise GradingProblem(str(exc)) from exc
                editor = None
        if not nodes and editor is not None:
            return
        missed = []
        for client in self.targets.resolve(TARGET_NDISPLAY):
            try:
                client.run_python(script_for(transact and editor is None))
                took += 1
                with self._lock:
                    self._missed.pop((_client_name(client), key), None)
            except RemoteControlError as exc:
                if not _unreachable(exc) and editor is None:
                    raise GradingProblem(str(exc)) from exc
                missed.append(client)
        if not took:
            raise GradingProblem("No editor or nDisplay node answered, so the grade was not changed")
        if missed:
            self._remember_missed(key, missed)

    def _write_script(self, key: str, values: Dict[str, Any]) -> Callable[[bool], str]:
        t = self.target(key)
        vals = copy.deepcopy(values)
        return lambda transact: python_result_wrapper(
            scripts.write_grade(t["kind"], t["actor"], t["applies_to"], vals, transact, nth=t["nth"]))

    def _remember_missed(self, key: str, clients) -> None:
        names = ", ".join(_client_name(c) for c in clients)
        with self._lock:
            for c in clients:
                old = self._missed.get((_client_name(c), key))
                values = dict(old[1]) if old else {}
                values.update(self._latest_values(key))
                self._missed[(_client_name(c), key)] = (c, values)
            if self._retrier is None or not self._retrier.is_alive():
                self._retrier = threading.Thread(target=self._retry_worker, daemon=True, name="grading-retry")
                self._retrier.start()
        self.bus.publish("grading.problem", message=f"{names} did not get the grade change; "
                                                    f"it will be sent again when they answer")

    def _latest_values(self, key: str) -> Dict[str, Any]:
        """Every value of the target as Operator Board last set it (overrides; others reset)."""
        grade = self.current.get(key)
        if grade is None:
            return {}
        return {k: (grade.get(k) if k in grade.overrides else None) for k in ALL_KEYS}

    def _retry_worker(self) -> None:
        while True:
            time.sleep(self.retry_s)
            with self._lock:
                items = list(self._missed.items())
                if not items:
                    self._retrier = None
                    return
            for (name, key), (client, values) in items:
                try:
                    client.run_python(self._write_script(key, values)(False))
                except Exception:  # noqa: BLE001 - still away; try again next round
                    continue
                with self._lock:
                    self._missed.pop((name, key), None)
                self.bus.publish("grading.problem", message="")

    # ---- live sending during a gesture ----------------------------------------------------------
    def begin_gesture(self, key: str) -> Grade:
        """Re-read the target before a drag so it starts from Unreal's real value."""
        grade = self.read(key)
        self._end_bypass(key)
        with self._lock:
            self._gesture_start[key] = grade
            self._gesture_values[key] = {}
        return grade

    def send(self, key: str, values: Dict[str, Any]) -> None:
        """Queue values for the target; the sender sends the latest ones (never blocks)."""
        with self._lock:
            grade = self.current.get(key) or Grade.neutral()
            for k, v in values.items():
                grade = grade.reset(k) if v is None else grade.set(k, v)
            self.current[key] = grade
            self._pending.setdefault(key, {}).update(values)
            self._gesture_values.setdefault(key, {}).update(values)
            if self._sender is None or not self._sender.is_alive():
                self._sender = threading.Thread(target=self._send_worker, daemon=True, name="grading-sender")
                self._sender.start()
            self._wake.notify_all()

    def _send_worker(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._sender = None
                    self._wake.notify_all()
                    return
                key, values = next(iter(self._pending.items()))
                del self._pending[key]
                self._busy.add(key)
            started = time.time()
            try:
                self._deliver(key, self._write_script(key, values), transact=False)
            except Exception as exc:  # noqa: BLE001 - reported, the drag goes on
                self.bus.publish("grading.problem", message=str(exc))
            finally:
                with self._lock:
                    self._busy.discard(key)
                    self._wake.notify_all()
            rest = self.min_interval_s - (time.time() - started)
            if rest > 0:
                time.sleep(rest)

    def flush(self, key: str, timeout: float = 10.0) -> None:
        end = time.time() + timeout
        with self._lock:
            while key in self._pending or key in self._busy:
                left = end - time.time()
                if left <= 0:
                    break
                self._wake.wait(left)

    def end_gesture(self, key: str, label: str) -> None:
        """Finish a drag: the editor records the final values once (transaction), one change log entry."""
        self.flush(key)
        with self._lock:
            values = self._gesture_values.pop(key, {})
            previous = self._gesture_start.pop(key, None)
        if not values:
            return
        result = "ok"
        try:
            self._deliver(key, self._write_script(key, values), transact=True, nodes=False)
        except GradingProblem as exc:
            result = f"failed: {exc}"
            self.bus.publish("grading.problem", message=str(exc))
        self._log(key, label, previous, result)
        self.bus.publish("grading.changed", key=key, grade=self.current.get(key))

    # ---- one-shot changes ---------------------------------------------------------------------
    def _change(self, key: str, label: str, values: Dict[str, Any], previous: Optional[Grade] = None) -> None:
        if previous is None:
            previous = self.read(key)
        self._end_bypass(key)
        grade = previous
        for k, v in values.items():
            grade = grade.reset(k) if v is None else grade.set(k, v)
        with self._lock:
            self.current[key] = grade
        self._deliver(key, self._write_script(key, values), transact=True)
        self._log(key, label, previous, "ok")
        self.bus.publish("grading.changed", key=key, grade=grade)

    def _log(self, key: str, label: str, previous: Optional[Grade], result: str) -> None:
        if self.record is None:
            return
        name = next((t["name"] for t in self.targets_list if t["key"] == key), key)
        grade = self.current.get(key)
        self.record(CATEGORY, name, label, value=grade.to_dict() if grade else None,
                    previous=previous.to_dict() if previous else None, result=result, kind="grade",
                    ref={"key": key})

    def _guard(self, fn: Callable[[], Any]) -> Tuple[bool, str]:
        try:
            msg = fn()
        except GradingProblem as exc:
            self.bus.publish("grading.problem", message=str(exc))
            return False, str(exc)
        return True, msg or "ok"

    def nudge(self, control_id: str, amount: float, key: str = "", channel: str = "y") -> Tuple[bool, str]:
        def run():
            k = self._key(key)
            control = CONTROLS[control_id]
            before = self.read(k)
            after = nudge_grade(before, control_id, float(amount), channel)
            self._change(k, f"Nudge {control.title} {amount:+g}", {control.key: after.get(control.key)}, before)
            return f"{control.title} {amount:+g}"
        return self._guard(run)

    def reset(self, key: str = "", control_id: Optional[str] = None) -> Tuple[bool, str]:
        def run():
            k = self._key(key)
            if control_id:
                keys = [CONTROLS[control_id].key]
                label = f"Reset {CONTROLS[control_id].title}"
            else:
                keys, label = list(ALL_KEYS), "Reset grade"
            self._change(k, label, {kk: None for kk in keys})
            return label
        return self._guard(run)

    def reset_all(self) -> Tuple[bool, str]:
        problems = [msg for t in list(self.targets_list) for ok, msg in [self.reset(t["key"])] if not ok]
        return (not problems), ("; ".join(problems) or "All grades reset")

    def apply(self, key: str, grade_dict: Dict[str, Any]) -> None:
        """Set a whole grade (Undo, presets). Raises RuntimeError when it could not be set."""
        grade = Grade.from_dict(grade_dict)
        try:
            self.target(key)
            self._change(key, "Set grade", {k: (grade.get(k) if k in grade.overrides else None) for k in ALL_KEYS},
                         previous=self.current.get(key) or Grade.neutral())
        except GradingProblem as exc:
            raise RuntimeError(str(exc)) from exc

    # ---- bypass ---------------------------------------------------------------------------------
    def is_bypassed(self, key: str) -> bool:
        return key in self.bypass.bypassed()

    def set_bypass(self, key: str = "", on: Optional[bool] = None) -> bool:
        """Bypass (switch off the target's own grading switch) or end it; ``on`` None toggles.
        Returns whether the target is bypassed afterwards. Raises GradingProblem."""
        key = self._key(key)
        on = (not self.is_bypassed(key)) if on is None else bool(on)
        t = self.target(key)
        self._deliver(key, lambda transact: python_result_wrapper(
            scripts.set_grading_enabled(t["kind"], t["actor"], t["applies_to"], not on, transact, nth=t["nth"])),
            transact=True)
        self.bypass.set(key, on)
        with self._lock:
            self.enabled[key] = not on
        self.bus.publish("grading.bypass", key=key, on=on)
        return on

    def _end_bypass(self, key: str) -> None:
        if self.is_bypassed(key):
            self.set_bypass(key, False)

    def enable(self, key: str = "") -> Tuple[bool, str]:
        """"Enable grading on this target": switch on its own (and its master) grading switch."""
        def run():
            k = self._key(key)
            t = self.target(k)
            self._deliver(k, lambda transact: python_result_wrapper(
                scripts.set_grading_enabled(t["kind"], t["actor"], t["applies_to"], True, transact, master=True,
                                            nth=t["nth"])), transact=True)
            self.bypass.set(k, False)
            with self._lock:
                self.enabled[k] = True
            self.bus.publish("grading.bypass", key=k, on=False)
            return "Grading enabled"
        return self._guard(run)

    # ---- looks ------------------------------------------------------------------------------------
    def save_look(self, name: str, keys: List[str]) -> Look:
        look = Look(name.strip() or "Look", {k: self.read(k) for k in keys})
        self.looks.save(look)
        return look

    def recall_look(self, name: str) -> List[str]:
        """Apply a look to every target it holds that is in the level; returns warnings for the rest.
        Raises GradingProblem for an unknown look."""
        look = self.looks.get(name)
        if look is None:
            raise GradingProblem(f"There is no look called '{name}'")
        warnings = []
        keys = {t["key"] for t in self.targets_list}
        for key, grade in look.grades.items():
            if key not in keys:
                warnings.append(f"{GONE}: {key.split('|')[1] if '|' in key else key} (skipped)")
                continue
            try:
                self._change(key, f"Look {look.name}",
                             {k: (grade.get(k) if k in grade.overrides else None) for k in ALL_KEYS})
            except GradingProblem as exc:
                warnings.append(str(exc))
        if warnings:
            self.bus.publish("grading.problem", message="; ".join(warnings))
        self.active_look = look.name
        return warnings

    active_look = ""

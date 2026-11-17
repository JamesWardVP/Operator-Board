"""Colour grading: the shared target selection, live sending while a control is dragged, delivery to
the editor and the nDisplay nodes, bypass, reset, looks and nudges.

Delivery (spec "Delivery during a drag"): every change goes to the primary editor (plain set, no
transaction) and straight to every running nDisplay node, so the wall follows a drag even when
multi-user is slow or absent. When the gesture ends the editor records the final values once
inside an editor transaction. With no editor, changes go to every node. A node that cannot be
reached is named in a ``grading.problem`` event and is sent the latest change again until it answers;
during a drag it is skipped meanwhile, so one switched-off machine does not slow the wall. Any other
error (a script error, a blocked call) is reported once and not retried. An editor that answers with
an error does not stop a drag reaching the nodes.

``begin_gesture`` never raises (a problem is published instead); ``end_gesture`` and ``flush`` wait
for Unreal, so call them off the UI thread.

Events: ``grading.targets``, ``grading.selected`` (key), ``grading.changed`` (key, grade),
``grading.bypass`` (key, on), ``grading.problem`` (message).
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


class GradingPartial(GradingProblem):
    """The change reached some instances (the nDisplay nodes) but the editor answered with an error:
    the wall has it, so Operator Board keeps its own state as if it was made, then reports this."""


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
        # Sends to the editor and every node at once (up to 16 at a time).
        self._pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="grading-send")
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
        # (client name, key, "grade" | "switch") -> (client, script_for, stamp): changes to send again
        self._missed: Dict[Tuple[str, str, str], Tuple[Any, Callable[[bool], str], int]] = {}
        self._stamp = 0
        self.stopped = False
        self._retrier: Optional[threading.Thread] = None

    # ---- lifecycle ----------------------------------------------------------------------------
    def stop(self) -> None:
        """Stop sending (the app is closing or switching project): nothing queued is sent again."""
        with self._lock:
            self.stopped = True
            self._missed.clear()
            self._pending.clear()
            self._wake.notify_all()
        self._pool.shutdown(wait=False)

    @property
    def active_look(self) -> str:
        """The look last recalled, until the grade is changed by hand ("" then). Kept in the config."""
        return self.config.grading.active_look

    @active_look.setter
    def active_look(self, name: str) -> None:
        if name != self.config.grading.active_look:
            self.config.grading.active_look = name
            self.bus.publish("grading.look", name=name)

    # ---- targets and selection ---------------------------------------------------------------
    @property
    def selected(self) -> str:
        chosen = self.config.grading.selected
        if chosen:
            return chosen  # even when it has gone: grading some other target instead would be worse
        return self.targets_list[0]["key"] if self.targets_list else ""

    def select(self, key: str) -> None:
        self.config.grading.selected = key
        self.bus.publish("grading.selected", key=key)

    def refresh_targets(self) -> List[Dict[str, Any]]:
        script = python_result_wrapper(scripts.list_grading_targets())
        try:
            result = self.targets.editor_or_nodes(lambda c: c.run_python(script))[0]
        except RemoteControlError as exc:
            raise GradingProblem(str(exc)) from exc
        found = []
        for t in (result or {}).get("targets", []):
            t = dict(t)
            t["nth"] = int(t.get("nth") or 0)
            t["key"] = target_key(t["kind"], t["actor"], t.get("applies_to") or []) + (f"#{t['nth'] + 1}" if t["nth"] else "")
            found.append(t)
        with self._lock:
            self.targets_list = found
            self.missing = dict((result or {}).get("missing") or {})
            keys = {t["key"] for t in found}
            for entry in [e for e in self._missed if e[1] not in keys]:
                del self._missed[entry]
        self.bus.publish("grading.targets", targets=found, missing=self.missing)
        return found

    def target(self, key: str) -> Dict[str, Any]:
        found = next((t for t in self.targets_list if t["key"] == key), None)
        if found is None:
            raise GradingProblem(f"{GONE}: {key.split('|')[1] if '|' in key else key}")
        return found

    def _key(self, key: str) -> str:
        if not self.targets_list:
            self.refresh_targets()  # nothing loaded yet (a Stream Deck press straight after start-up)
        key = key or self.selected
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
    def _deliver(self, key: str, script_for: Callable[[bool], str], transact: bool, nodes: bool = True,
                 what: str = "grade", live: bool = False) -> None:
        """Run a change on the editor (``transact``) and on every running node, all at the same time,
        and wait for every answer. Raises GradingProblem when nobody took it or the target is gone,
        and GradingPartial when the editor answered with an error but nodes took it. ``what`` is
        "grade" or "switch" (bypass / enable), so a node that could not be reached is later sent the
        same kind of change. Nodes already known to be unreachable are left to the retrier instead
        of costing a timeout on every change. ``nodes`` False (a release): the editor only, with the
        nodes as a fallback when the editor can't take it. ``live`` is kept for callers; every change
        now skips known-down nodes."""
        if self.stopped:
            raise GradingProblem("Operator Board is closing, so the grade was not changed")
        try:
            editor = self.targets.primary()
        except LookupError:
            editor = None
        took = 0
        editor_error = ""
        if editor is not None and not nodes:
            try:
                editor.run_python(script_for(transact))
                return
            except RemoteControlError as exc:
                if not _unreachable(exc):
                    editor_error = str(exc)
                editor = None
        with self._lock:
            down = {name for name, _k, _w in self._missed}
            waiting = {e: v for e, v in self._missed.items() if e[1] == key and e[2] == what}
        node_list, skipped = [], []
        for client in self.targets.resolve(TARGET_NDISPLAY):
            (skipped if _client_name(client) in down else node_list).append(client)
        node_tx = transact and editor is None and not editor_error

        def run(client, tx):
            client.run_python(script_for(tx))

        jobs = []
        if editor is not None:
            jobs.append((None, self._pool.submit(run, editor, transact)))
        for client in node_list:
            jobs.append((client, self._pool.submit(run, client, node_tx)))
        missed, errors = [], []
        for client, job in jobs:
            try:
                job.result()
                took += 1
                if client is not None:
                    with self._lock:
                        self._missed.pop((_client_name(client), key, what), None)
            except RemoteControlError as exc:
                if client is None:
                    if not _unreachable(exc):
                        editor_error = str(exc)
                elif _unreachable(exc):
                    missed.append(client)
                else:
                    errors.append(f"{_client_name(client)}: {exc}")
        if not took:
            # Nothing took it, so it failed: no node may get it later. Earlier changes still waiting
            # for a node (a partial success before this) are kept as they were.
            with self._lock:
                self._missed = {e: v for e, v in self._missed.items() if not (e[1] == key and e[2] == what)}
                self._missed.update(waiting)
        elif missed or skipped:
            self._remember_missed(key, missed + skipped, what, script_for, quiet=not missed)
        if editor_error:
            if took:
                raise GradingPartial(editor_error + " (the nDisplay nodes got the change)")
            raise GradingProblem(editor_error)
        if not took:
            raise GradingProblem("; ".join(errors) if errors else
                                 "No editor or nDisplay node answered, so the grade was not changed")
        if errors:
            self.bus.publish("grading.problem", message="Not changed on " + "; ".join(errors))

    def _write_script(self, key: str, values: Dict[str, Any]) -> Callable[[bool], str]:
        t = self.target(key)
        vals = copy.deepcopy(values)
        return lambda transact: python_result_wrapper(
            scripts.write_grade(t["kind"], t["actor"], t["applies_to"], vals, transact, nth=t["nth"]))

    def _remember_missed(self, key: str, clients, what: str, script_for: Callable[[bool], str],
                         quiet: bool = False) -> None:
        """Send these clients the change again until they answer. A grade is re-sent as every value
        Operator Board last set on the target, so the newest values win."""
        if what == "grade" and self.current.get(key) is not None:
            try:
                script_for = self._write_script(key, self._latest_values(key))
            except GradingProblem:
                pass
        with self._lock:
            for c in clients:
                self._stamp += 1
                self._missed[(_client_name(c), key, what)] = (c, script_for, self._stamp)
            if self._retrier is None or not self._retrier.is_alive():
                self._retrier = threading.Thread(target=self._retry_worker, daemon=True, name="grading-retry")
                self._retrier.start()
        if not quiet:
            names = ", ".join(_client_name(c) for c in clients)
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
                if not items or self.stopped:
                    self._retrier = None
                    return
            for entry, (client, script_for, stamp) in items:
                try:
                    client.run_python(script_for(False))
                except RemoteControlError as exc:
                    if _unreachable(exc):
                        continue  # still away; try again next round
                    problem = f"{entry[0]}: {exc}"
                except Exception as exc:  # noqa: BLE001 - never retry what cannot work
                    problem = f"{entry[0]}: {exc}"
                else:
                    problem = ""
                with self._lock:
                    if self._missed.get(entry, (None, None, None))[2] == stamp:  # not replaced meanwhile
                        del self._missed[entry]
                    left = bool(self._missed)
                if problem:
                    self.bus.publish("grading.problem", message="Not changed on " + problem)
                elif not left:
                    self.bus.publish("grading.problem", message="")

    # ---- live sending during a gesture ----------------------------------------------------------
    def begin_gesture(self, key: str) -> Grade:
        """Re-read the target before a drag so it starts from Unreal's real value, and end a bypass.
        Never raises: a problem is published and the drag goes on from the last known grade."""
        try:
            grade = self.read(key)
        except GradingProblem as exc:
            self.bus.publish("grading.problem", message=str(exc))
            grade = self.current.get(key) or Grade.neutral()
        try:
            self._end_bypass(key)
        except GradingProblem as exc:
            self.bus.publish("grading.problem", message=str(exc))
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
                if not self._pending or self.stopped:
                    self._pending.clear()
                    self._sender = None
                    self._wake.notify_all()
                    return
                key, values = next(iter(self._pending.items()))
                del self._pending[key]
                self._busy.add(key)
            started = time.time()
            try:
                self._deliver(key, self._write_script(key, values), transact=False, live=True)
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
            if previous is not None:
                # Live sends already left the final values in the editor outside any transaction, so a
                # transaction now would hold no change (nothing for multi-user or the editor's Undo).
                # Put the start values back first (editor only, no transaction), then write the final ones.
                start = {k: (previous.get(k) if k in previous.overrides else None) for k in values}
                try:
                    self.targets.primary().run_python(self._write_script(key, start)(False))
                except (LookupError, RemoteControlError):
                    pass  # no editor, or it is not answering: the transacted write below says so
            self._deliver(key, self._write_script(key, values), transact=True, nodes=False)
        except GradingProblem as exc:
            result = f"failed: {exc}"
            self.bus.publish("grading.problem", message=str(exc))
        self.active_look = ""
        self._log(key, label, previous, result)
        self.bus.publish("grading.changed", key=key, grade=self.current.get(key))

    # ---- one-shot changes ---------------------------------------------------------------------
    def _change(self, key: str, label: str, values: Dict[str, Any], previous: Optional[Grade] = None,
                look: bool = False) -> None:
        if previous is None:
            previous = self.read(key)
        self._end_bypass(key)
        grade = previous
        for k, v in values.items():
            grade = grade.reset(k) if v is None else grade.set(k, v)
        with self._lock:
            self.current[key] = grade
        partial = None
        try:
            self._deliver(key, self._write_script(key, values), transact=True)
        except GradingPartial as exc:
            partial = exc
        if not look:
            self.active_look = ""
        self._log(key, label, previous, f"partial: {partial}" if partial else "ok")
        self.bus.publish("grading.changed", key=key, grade=grade)
        if partial:
            raise partial

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
        if on and not self.is_bypassed(key) and not self.enabled.get(key, True):
            raise GradingProblem(f"Grading is switched off on {t['name']}, so there is nothing to bypass")
        partial = None
        try:
            self._deliver(key, lambda transact: python_result_wrapper(
                scripts.set_grading_enabled(t["kind"], t["actor"], t["applies_to"], not on, transact,
                                            nth=t["nth"])), transact=True, what="switch")
        except GradingPartial as exc:
            partial = exc
        self.bypass.set(key, on)
        with self._lock:
            self.enabled[key] = not on
        self.bus.publish("grading.bypass", key=key, on=on)
        if partial:
            raise partial
        return on

    def _end_bypass(self, key: str) -> None:
        if self.is_bypassed(key):
            self.set_bypass(key, False)

    def enable(self, key: str = "") -> Tuple[bool, str]:
        """"Enable grading on this target": switch on its own (and its master) grading switch."""
        def run():
            k = self._key(key)
            t = self.target(k)
            partial = None
            try:
                self._deliver(k, lambda transact: python_result_wrapper(
                    scripts.set_grading_enabled(t["kind"], t["actor"], t["applies_to"], True, transact, master=True,
                                                nth=t["nth"])), transact=True, what="switch")
            except GradingPartial as exc:
                partial = exc
            self.bypass.set(k, False)
            with self._lock:
                self.enabled[k] = True
            self.bus.publish("grading.bypass", key=k, on=False)
            if partial:
                raise partial
            return "Grading enabled"
        return self._guard(run)

    def restore_bypassed(self) -> Tuple[bool, str]:
        """End every bypass Operator Board left on when it last closed (the start-up prompt's
        "Restore"). Targets no longer in the level are forgotten and named."""
        def run():
            keys = self.bypass.bypassed()
            if not keys:
                return "Nothing was bypassed"
            self.refresh_targets()
            present = {t["key"] for t in self.targets_list}
            gone, done = [], 0
            for key in keys:
                if key in present:
                    self.set_bypass(key, False)
                    done += 1
                else:
                    self.bypass.set(key, False)
                    gone.append(key.split("|")[1] if "|" in key else key)
            if gone:
                raise GradingProblem(f"Restored the grade on {done} target{'s' if done != 1 else ''}; "
                                     f"{GONE}: {', '.join(gone)}")
            return f"Restored the grade on {done} target{'s' if done != 1 else ''}"
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
        return self.apply_look(look)

    def apply_look(self, look: Look) -> List[str]:
        """Apply a look (from the store, or embedded in a settings preset); returns warnings for the
        targets it could not apply."""
        warnings = []
        if not self.targets_list and look.grades:
            try:
                self.refresh_targets()
            except GradingProblem as exc:
                warnings.append(str(exc))
        keys = {t["key"] for t in self.targets_list}
        applied = 0
        for key, grade in look.grades.items():
            if key not in keys:
                warnings.append(f"{GONE}: {key.split('|')[1] if '|' in key else key} (skipped)")
                continue
            try:
                self._change(key, f"Look {look.name}",
                             {k: (grade.get(k) if k in grade.overrides else None) for k in ALL_KEYS}, look=True)
                applied += 1
            except GradingProblem as exc:
                warnings.append(str(exc))
        if warnings:
            self.bus.publish("grading.problem", message="; ".join(warnings))
        if applied or not look.grades:
            self.active_look = look.name
        return warnings


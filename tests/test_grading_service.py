"""GradingService: shared selection, the live sender, delivery to editor + nodes, bypass, looks, nudges."""
import threading
import time

import pytest

from operator_board.core.config import AppConfig
from operator_board.core.events import EventBus
from operator_board.core.grading import Grade
from operator_board.core.looks import BypassState, Look, LookStore, target_key
from operator_board.remote.grading import GradingProblem, GradingService
from operator_board.remote.rc_client import RemoteControlError
from operator_board.remote.targets import TargetResolver

TARGETS = {"targets": [
    {"kind": "ndisplay_cluster", "actor": "Root", "applies_to": [], "name": "Root · Entire cluster", "enabled": True},
    {"kind": "icvfx_all_nodes", "actor": "Root/Cam", "applies_to": [], "name": "Cam · All nodes", "enabled": True},
    {"kind": "ppv", "actor": "Stage PPV", "applies_to": [], "name": "Stage PPV (global)", "enabled": True},
], "missing": {"ndisplay": "", "ccr": ""}}
CLUSTER = target_key("ndisplay_cluster", "Root")
PPV = target_key("ppv", "Stage PPV")


class FakeClient:
    def __init__(self, host, grade=None):
        self.host, self.port = host, 30010
        self.scripts = []
        self.down = False
        self.gone = set()  # actors this instance no longer has
        self.grade = (grade or Grade.neutral()).to_dict()
        self.enabled = True
        self.lock = threading.Lock()
        self.delay = 0.0        # seconds an unreachable call takes (a connect timeout)
        self.fail_with = None   # an error other than "unreachable" (a script error, a blocked call)
        self.on_call = None     # called with the script before it runs

    def run_python(self, script):
        if self.on_call:
            self.on_call(script)
        if self.down:
            if self.delay:
                time.sleep(self.delay)
            raise RemoteControlError(f"Remote Control unreachable at http://{self.host}:30010: refused")
        if self.fail_with:
            raise RemoteControlError(self.fail_with)
        with self.lock:
            self.scripts.append(script)
        for actor in self.gone:
            if repr(actor)[1:-1] in script or f'"{actor}"' in script:
                raise RemoteControlError("Target no longer in the level: " + actor)
        if "targets = []" in script:
            return TARGETS
        if "result = {'grade': _g_read(t)" in script:
            return {"grade": self.grade, "enabled": self.enabled}
        if "_g_run(lambda: t.set_enabled(" in script:
            self.enabled = "t.set_enabled(True" in script
            return {"ok": True, "enabled": self.enabled}
        return {"ok": True}

    def writes(self):
        return [s for s in self.scripts if "_g_run(lambda: _g_write(t" in s]


class FakeResolver(TargetResolver):
    def __init__(self, editor, nodes):
        super().__init__(AppConfig())
        self.editor, self.nodes = editor, nodes

    def primary(self):
        if self.editor is None:
            raise LookupError("No editor with Remote Control enabled is configured")
        return self.editor

    def resolve(self, target):
        return list(self.nodes) if target == "ndisplay" else [self.primary()]


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def make(editor=True, nodes=2, tmp_path=None):
    bus = EventBus()
    events = []
    bus.subscribe("*", lambda topic, payload: events.append((topic, payload)))
    ed = FakeClient("editor") if editor else None
    ns = [FakeClient(f"node{i}") for i in range(nodes)]
    rec = Recorder()
    looks = LookStore(None, bus)
    bypass = BypassState(str(tmp_path / "bypass.json") if tmp_path else None)
    svc = GradingService(AppConfig(), FakeResolver(ed, ns), bus, looks, bypass, record=rec, retry_s=0.05)
    svc.refresh_targets()
    return svc, ed, ns, rec, events


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_targets_get_keys_in_order():
    svc, *_ = make()
    assert [t["key"] for t in svc.targets_list] == [CLUSTER, target_key("icvfx_all_nodes", "Root/Cam"), PPV]


def test_nudge_without_selection_uses_first_target():
    svc, ed, *_ = make()
    assert svc.selected == CLUSTER
    ok, _msg = svc.nudge("gain", 0.02)
    assert ok and ed.writes()


def test_select_is_shared_and_saved():
    svc, _, _, _, events = make()
    svc.select(PPV)
    assert svc.selected == PPV and svc.config.grading.selected == PPV
    assert ("grading.selected", {"key": PPV}) in events


def test_send_coalesces_latest_value():
    svc, ed, *_ = make()
    svc.begin_gesture(PPV)
    for i in range(100):
        svc.send(PPV, {"global.gain": [1 + i / 100, 1, 1, 1]})
    svc.end_gesture(PPV, "Gain")
    writes = ed.writes()
    assert 1 <= len(writes) < 40
    assert "1.99" in writes[-1]


def test_drag_goes_to_editor_and_nodes_transaction_only_at_release():
    svc, ed, ns, *_ = make()
    svc.begin_gesture(PPV)
    svc.send(PPV, {"tint": 0.1})
    assert wait_for(lambda: all(n.writes() for n in ns))
    svc.end_gesture(PPV, "Tint")
    assert all(n.writes() for n in ns)
    transacted = [w for w in ed.writes() if "_g_write(t, {\"tint\": 0.1}), True)" in w]
    plain = [w for w in ed.writes() if "_g_write(t, {\"tint\": 0.1}), False)" in w]
    assert len(transacted) == 1 and plain
    assert all("), True)" not in w for n in ns for w in n.writes())


def test_no_editor_goes_to_every_node():
    svc, _, ns, *_ = make(editor=False, nodes=3)
    ok, _ = svc.nudge("tint", 0.05, PPV)
    assert ok and all(n.writes() for n in ns)


def test_one_gesture_one_change_log_entry():
    svc, ed, _, rec, _ = make()
    ed.grade = Grade.neutral().set("global.gain", (1.1, 1, 1, 1)).to_dict()
    svc.begin_gesture(PPV)
    for i in range(30):
        svc.send(PPV, {"global.gain": [1.2, 1, 1, 1]})
    svc.end_gesture(PPV, "Gain")
    assert len(rec.calls) == 1
    args, kwargs = rec.calls[0]
    assert kwargs["kind"] == "grade" and kwargs["ref"] == {"key": PPV}
    assert kwargs["previous"]["values"]["global.gain"] == [1.1, 1, 1, 1]
    assert kwargs["value"]["values"]["global.gain"] == [1.2, 1, 1, 1]


def test_unreachable_node_warns_and_resends():
    svc, ed, ns, _, events = make()
    ns[1].down = True
    ok, msg = svc.nudge("tint", 0.05, PPV)
    assert ok
    problems = [p for t, p in events if t == "grading.problem"]
    assert problems and "node1" in problems[-1]["message"]
    ns[1].down = False
    assert wait_for(lambda: ns[1].writes())
    assert "0.05" in ns[1].writes()[-1]


def test_bypass_toggle_saved_and_touch_ends_it(tmp_path):
    svc, ed, *_ = make(tmp_path=tmp_path)
    assert svc.set_bypass(PPV, None) is True
    assert svc.is_bypassed(PPV) and BypassState(str(tmp_path / "bypass.json")).bypassed() == [PPV]
    assert "t.set_enabled(False" in ed.scripts[-1]
    ed.scripts.clear()
    svc.nudge("gain", 0.02, PPV)
    enable = next(i for i, s in enumerate(ed.scripts) if "t.set_enabled(True" in s)
    write = next(i for i, s in enumerate(ed.scripts) if "_g_run(lambda: _g_write(t" in s)
    assert enable < write and not svc.is_bypassed(PPV)


def test_recall_look_skips_missing_target_with_warning():
    svc, ed, *_ = make()
    svc.looks.save(Look("Warm", {PPV: Grade.neutral().set("temp", 5600),
                                 target_key("ppv", "Gone PPV"): Grade.neutral()}))
    warnings = svc.recall_look("Warm")
    assert len(warnings) == 1 and "Gone PPV" in warnings[0]
    assert any("5600" in w for w in ed.writes())


def test_recall_look_while_bypassed_ends_bypass_first(tmp_path):
    svc, ed, *_ = make(tmp_path=tmp_path)
    svc.looks.save(Look("Warm", {PPV: Grade.neutral().set("temp", 5600)}))
    svc.set_bypass(PPV, True)
    ed.scripts.clear()
    svc.recall_look("Warm")
    enable = next(i for i, s in enumerate(ed.scripts) if "t.set_enabled(True" in s)
    write = next(i for i, s in enumerate(ed.scripts) if "_g_run(lambda: _g_write(t" in s)
    assert enable < write
    assert not svc.is_bypassed(PPV)


def test_recall_writes_overrides_and_resets_the_rest():
    svc, ed, *_ = make()
    svc.looks.save(Look("Warm", {PPV: Grade.neutral().set("temp", 5600)}))
    svc.recall_look("Warm")
    w = ed.writes()[-1]
    assert '"temp": 5600.0' in w and '"global.gain": None' in w


def test_selected_target_deleted():
    svc, ed, ns, _, events = make()
    for c in [ed] + ns:
        c.gone.add("Stage PPV")
    ok, msg = svc.nudge("gain", 0.02, PPV)
    assert not ok and "no longer in the level" in msg
    assert any(t == "grading.problem" for t, _ in events)


def test_unknown_target_key():
    svc, *_ = make()
    ok, msg = svc.nudge("gain", 0.02, "ppv|Nope")
    assert not ok and "no longer in the level" in msg


def test_reset_one_control_and_whole_grade():
    svc, ed, _, rec, _ = make()
    svc.reset(PPV, "gain")
    assert '"global.gain": None' in ed.writes()[-1] and '"global.gamma"' not in ed.writes()[-1]
    svc.reset(PPV)
    assert '"global.gamma": None' in ed.writes()[-1] and '"temp": None' in ed.writes()[-1]
    assert len(rec.calls) == 2


def test_save_look_reads_chosen_targets():
    svc, ed, *_ = make()
    ed.grade = Grade.neutral().set("tint", 0.2).to_dict()
    svc.save_look("Night", [PPV])
    assert svc.looks.get("Night").grades[PPV].tint == 0.2


def test_apply_used_by_undo_raises_when_nothing_answers():
    svc, ed, ns, *_ = make()
    ed.down = True
    for n in ns:
        n.down = True
    with pytest.raises(RuntimeError):
        svc.apply(PPV, Grade.neutral().to_dict())


def test_enable_switches_master_bypass_does_not():
    svc, ed, *_ = make()
    svc.set_bypass(PPV, True)
    assert "t.set_enabled(False, False)" in ed.scripts[-1]
    svc.set_bypass(PPV, False)
    assert "t.set_enabled(True, False)" in ed.scripts[-1]
    svc.enable(PPV)
    assert "t.set_enabled(True, True)" in ed.scripts[-1]


def test_duplicate_entries_get_distinct_keys():
    svc, ed, *_ = make()
    TARGETS["targets"].extend([
        {"kind": "ndisplay_viewport", "actor": "Root", "applies_to": [], "nth": 0, "name": "a", "enabled": True},
        {"kind": "ndisplay_viewport", "actor": "Root", "applies_to": [], "nth": 1, "name": "b", "enabled": True}])
    try:
        keys = [t["key"] for t in svc.refresh_targets()]
    finally:
        del TARGETS["targets"][-2:]
    assert keys[-2] != keys[-1] and keys[-1].endswith("#2")
    svc.nudge("tint", 0.05, keys[-1])
    assert "}, False, nth=" not in ed.writes()[-1] and ", 1)" in ed.writes()[-1]


def test_restore_bypassed_ends_bypass_and_forgets_gone_targets(tmp_path):
    svc, ed, *_ = make(tmp_path=tmp_path)
    svc.bypass.set(PPV, True)
    svc.bypass.set("ppv|Deleted PPV", True)
    ok, msg = svc.restore_bypassed()
    assert "t.set_enabled(True, False)" in ed.scripts[-1]
    assert svc.bypass.bypassed() == []
    assert not ok and "Deleted PPV" in msg


# ---- review fixes ------------------------------------------------------------------------------------
def test_stale_selection_is_a_problem_not_another_target():
    svc, ed, ns, _, events = make()
    svc.select(PPV)
    svc.targets_list = [t for t in svc.targets_list if t["key"] != PPV]  # PPV deleted, list refreshed
    ok, msg = svc.nudge("gain", 0.02)
    assert not ok and "Stage PPV" in msg
    assert not ed.writes() and not any(n.writes() for n in ns)


def test_retry_keeps_newer_values_missed_while_it_was_sending():
    svc, ed, ns, *_ = make()
    ns[1].down = True
    svc.nudge("tint", 0.05, PPV)
    fired = []

    def flap(script):
        # While the retrier sends the old value, a drag step misses node1 again with a newer one.
        if not fired and "_g_write" in script:
            fired.append(True)
            svc.current[PPV] = svc.current[PPV].set("tint", 0.3)
            svc._remember_missed(PPV, [ns[1]], "grade", svc._write_script(PPV, {"tint": 0.3}))
    ns[1].on_call = flap
    ns[1].down = False
    assert wait_for(lambda: any("0.3" in w for w in ns[1].writes()))


def test_dead_node_does_not_slow_the_drag():
    svc, ed, ns, *_ = make()
    ns[1].down, ns[1].delay = True, 0.3
    svc.begin_gesture(PPV)
    for i in range(10):
        svc.send(PPV, {"tint": i / 100})
        time.sleep(0.06)
    svc.end_gesture(PPV, "Tint")
    plain = [w for w in ed.writes() if "), False)" in w]
    assert len(plain) >= 6
    ns[1].down = False
    assert wait_for(lambda: any("0.09" in w for w in ns[1].writes()))


def test_begin_gesture_never_raises():
    svc, ed, ns, _, events = make()
    for c in [ed] + ns:
        c.gone.add("Stage PPV")
    grade = svc.begin_gesture(PPV)
    assert isinstance(grade, Grade)
    assert any(t == "grading.problem" and "Stage PPV" in p["message"] for t, p in events)


def test_node_script_error_is_reported_not_retried():
    svc, ed, ns, _, events = make()
    ns[1].fail_with = "Python: something broke"
    ok, _ = svc.nudge("tint", 0.05, PPV)
    assert ok
    assert any(t == "grading.problem" and "node1" in p["message"] for t, p in events)
    ns[1].fail_with = None
    time.sleep(0.3)
    assert not ns[1].writes() and not svc._missed


def test_missed_entries_for_a_removed_target_are_dropped():
    svc, ed, ns, *_ = make()
    ns[1].down = True
    svc.nudge("tint", 0.05, PPV)
    assert svc._missed
    saved = list(TARGETS["targets"])
    TARGETS["targets"][:] = [t for t in saved if t["actor"] != "Stage PPV"]
    try:
        svc.refresh_targets()
    finally:
        TARGETS["targets"][:] = saved
    assert not svc._missed


def test_editor_script_error_during_drag_still_reaches_nodes():
    svc, ed, ns, _, events = make()
    ed.fail_with = "Python Editor Script Plugin is off"
    svc.begin_gesture(PPV)
    svc.send(PPV, {"tint": 0.1})
    assert wait_for(lambda: all(n.writes() for n in ns))
    svc.end_gesture(PPV, "Tint")
    assert any(t == "grading.problem" and "Plugin is off" in p["message"] for t, p in events)


def test_missed_bypass_is_resent_as_a_bypass():
    svc, _, ns, *_ = make(editor=False)
    ns[1].down = True
    svc.set_bypass(PPV, True)
    ns[1].down = False
    assert wait_for(lambda: any("t.set_enabled(False" in s_ for s_ in ns[1].scripts))
    assert not ns[1].writes()


def test_nothing_loaded_and_unreal_down_is_a_plain_failure():
    svc, ed, ns, *_ = make()
    svc.targets_list = []
    for c in [ed] + ns:
        c.down = True
    ok, msg = svc.nudge("gain", 0.02)
    assert not ok and msg
    with pytest.raises(GradingProblem):
        svc.set_bypass("", True)


def test_recall_look_loads_targets_first():
    svc, ed, *_ = make()
    svc.targets_list = []
    svc.looks.save(Look("Warm", {PPV: Grade.neutral().set("temp", 5600)}))
    assert svc.recall_look("Warm") == []
    assert any("5600" in w for w in ed.writes()) and svc.active_look == "Warm"


def test_recall_look_with_nothing_applied_is_not_active():
    svc, *_ = make()
    svc.looks.save(Look("Old", {target_key("ppv", "Gone PPV"): Grade.neutral()}))
    assert svc.recall_look("Old")
    assert svc.active_look == ""


def test_editor_lost_mid_drag_release_goes_to_nodes_in_a_transaction():
    svc, ed, ns, *_ = make()
    svc.begin_gesture(PPV)
    svc.send(PPV, {"tint": 0.1})
    svc.flush(PPV)
    ed.down = True
    svc.end_gesture(PPV, "Tint")
    assert all(any("), True)" in w for w in n.writes()) for n in ns)


def test_saved_selection_loads_targets_when_none_are_loaded():
    svc, ed, *_ = make()
    svc.select(PPV)
    svc.targets_list = []  # fresh start: nothing loaded yet
    ok, _ = svc.nudge("gain", 0.02)
    assert ok and ed.writes()


def test_hand_change_clears_active_look_and_look_is_saved():
    svc, ed, *_ = make()
    svc.looks.save(Look("Warm", {PPV: Grade.neutral().set("temp", 5600)}))
    svc.recall_look("Warm")
    assert svc.active_look == "Warm" and svc.config.grading.active_look == "Warm"
    svc.nudge("gain", 0.02, PPV)
    assert svc.active_look == ""


def test_stop_ends_retries():
    svc, ed, ns, *_ = make()
    ns[1].down = True
    svc.nudge("tint", 0.05, PPV)
    svc.stop()
    ns[1].down = False
    time.sleep(0.3)
    assert not ns[1].writes() and svc.stopped

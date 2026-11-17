"""GradingService: shared selection, the live sender, delivery to editor + nodes, bypass, looks, nudges."""
import threading
import time

import pytest

from operator_board.core.config import AppConfig
from operator_board.core.events import EventBus
from operator_board.core.grading import Grade
from operator_board.core.looks import BypassState, Look, LookStore, target_key
from operator_board.remote.grading import GradingService
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

    def run_python(self, script):
        if self.down:
            raise RemoteControlError(f"Remote Control unreachable at http://{self.host}:30010: refused")
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

"""Colour grading wired into the app: actions, Undo, presets, takes and the default Grading page."""
import pytest

from operator_board.context import AppContext
from operator_board.core import presets
from operator_board.core.config import AppConfig
from operator_board.core.grading import Grade
from operator_board.core.looks import Look
from operator_board.surfaces.layout import default_layout


class FakeGrading:
    """Stands in for GradingService; records what the app asks of it."""

    def __init__(self):
        self.calls = []
        self.targets_list = [{"key": "ppv|Stage PPV", "name": "Stage PPV (global)"}]
        self.active_look = ""
        self.bypassed = False
        self.missing_on_apply = []

    def nudge(self, control, amount, key="", channel="y"):
        self.calls.append(("nudge", control, amount, key, channel))
        return True, f"{control} {amount:+g}"

    def reset(self, key="", control_id=None):
        self.calls.append(("reset", key, control_id))
        return True, "Reset grade"

    def reset_all(self):
        self.calls.append(("reset_all",))
        return True, "All grades reset"

    def set_bypass(self, key="", on=None):
        self.calls.append(("bypass", key, on))
        self.bypassed = (not self.bypassed) if on is None else on
        return self.bypassed

    def is_bypassed(self, key):
        return self.bypassed

    @property
    def selected(self):
        return "ppv|Stage PPV"

    def recall_look(self, name):
        self.calls.append(("look", name))
        self.active_look = name
        return []

    def apply_look(self, look):
        self.calls.append(("apply_look", look.name))
        return list(self.missing_on_apply)

    def apply(self, key, grade_dict):
        self.calls.append(("apply", key, grade_dict))

    def stop(self):
        self.calls.append(("stop",))


@pytest.fixture
def ctx(tmp_path):
    cfg = AppConfig()
    cfg.surfaces.api_port = 0
    cfg.surfaces.streamdeck_enabled = False
    c = AppContext(cfg, project_dir=str(tmp_path))
    c.grading_real = c.grading
    c.grading = FakeGrading()
    yield c
    c.shutdown()


def test_context_builds_grading_with_project_files(tmp_path):
    cfg = AppConfig()
    cfg.surfaces.api_port = 0
    c = AppContext(cfg, project_dir=str(tmp_path))
    try:
        c.looks.save(Look("Night", {}))
        assert (tmp_path / "looks.json").exists()
        assert c.grading.looks is c.looks
        assert c.grading.bypass.path == str(tmp_path / "grading_bypass.json")
    finally:
        c.shutdown()


def test_actions_exist_with_params(ctx):
    for aid in ("grading.look", "grading.bypass", "grading.reset", "grading.nudge"):
        assert ctx.actions.get(aid), aid
    names = [p.name for p in ctx.actions.get("grading.nudge").params]
    assert names == ["control", "amount", "channel", "target"]


def test_nudge_action_defaults(ctx):
    result = ctx.actions.invoke("grading.nudge", control="gain")
    assert result.ok
    assert ctx.grading.calls[-1] == ("nudge", "gain", 0.02, "", "y")


def test_nudge_action_default_amount_follows_control(ctx):
    ctx.actions.invoke("grading.nudge", control="temperature")
    assert ctx.grading.calls[-1] == ("nudge", "temperature", 100.0, "", "y")
    ctx.actions.invoke("grading.nudge", control="temperature", amount=-100)
    assert ctx.grading.calls[-1][2] == -100.0


def test_bypass_reset_look_actions(ctx):
    assert ctx.actions.invoke("grading.bypass").ok
    assert ctx.grading.calls[-1] == ("bypass", "", None)
    assert ctx.actions.state_of("grading.bypass") is True
    ctx.actions.invoke("grading.reset", target="all")
    assert ctx.grading.calls[-1] == ("reset_all",)
    ctx.actions.invoke("grading.reset")
    assert ctx.grading.calls[-1] == ("reset", "", None)
    ctx.actions.invoke("grading.look", look="Night")
    assert ctx.grading.calls[-1] == ("look", "Night")


def test_grading_actions_are_logged_through_service_not_twice(ctx):
    before = len(ctx.changes.records)
    ctx.actions.invoke("grading.nudge", control="gain")
    assert len(ctx.changes.records) == before  # the service records the change itself


def test_undo_routes_grade_to_service(ctx):
    ctx.apply_setting("grade", {"key": "ppv|Stage PPV"}, {"temp": 5600})
    assert ctx.grading.calls[-1] == ("apply", "ppv|Stage PPV", {"temp": 5600})


def test_preset_item_runs_after_object_settings_and_embeds_look(ctx):
    assert presets.ITEM_ORDER.index("grading_look") == presets.ITEM_ORDER.index("object_settings") + 1
    ctx.looks.save(Look("Night", {"ppv|Stage PPV": Grade.neutral().set("tint", 0.2)}))
    ctx.grading.active_look = "Night"
    item = presets.capture(ctx, "grading_look")
    assert item["data"]["name"] == "Night" and "ppv|Stage PPV" in item["data"]["grades"]
    assert presets.item_summary(item) == "Night"
    steps = presets.steps(ctx, {"items": [item]})
    assert steps[0][1]() == "Night"
    assert ctx.grading.calls[-1] == ("apply_look", "Night")


def test_preset_item_missing_target_is_a_problem(ctx):
    ctx.grading.missing_on_apply = ["Target no longer in the level: Gone (skipped)"]
    item = {"type": "grading_look", "data": Look("Night", {}).to_dict()}
    with pytest.raises(presets.PresetProblem):
        presets.steps(ctx, {"items": [item]})[0][1]()


def test_capture_without_a_look_is_a_problem(ctx):
    with pytest.raises(presets.PresetProblem):
        presets.capture(ctx, "grading_look")


def test_take_records_active_look(ctx):
    ctx.grading.active_look = "Night"
    assert ctx.takes.look_provider() == "Night"
    ctx.grading.active_look = ""
    assert ctx.takes.look_provider() == "unsaved grade"


def test_default_layout_has_grading_page(ctx):
    layout = default_layout()
    page = next(p for p in layout.pages if p.name == "Grading")
    actions = {b.action for b in page.buttons.values() if b.action}
    assert {"grading.bypass", "grading.reset", "grading.nudge"} <= actions
    for b in page.buttons.values():
        assert ctx.actions.get(b.action), b.action


def test_nudge_editor_round_trips_params():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from operator_board import qt
    from operator_board.ui.action_editors import NUDGE_ACTION, NudgeEditor
    qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    assert NUDGE_ACTION == "grading.nudge"
    ed = NudgeEditor(targets=lambda: ["ppv|Stage PPV"])
    assert ed.params() == {"control": "gain", "amount": 0.02, "channel": "y", "target": ""}
    ed.set_params({"control": "temperature", "amount": -100, "target": "ppv|Stage PPV"})
    assert ed.params() == {"control": "temperature", "amount": -100.0, "channel": "y", "target": "ppv|Stage PPV"}
    ed.set_params({"control": "tint"})  # amount follows the control when not given
    assert ed.params()["amount"] == 0.05
    ed.amount.setText("abc")
    with pytest.raises(ValueError):
        ed.params()


def test_grade_changes_are_not_remote_objects_settings(ctx):
    ctx.changes.record("Colour Grading", "Stage PPV", "Nudge Gain +0.02", value={"temp": 1}, previous=None,
                       result="ok", kind="grade", ref={"key": "ppv|Stage PPV"})
    assert ctx.changed_settings() == []


def test_editor_ready_loads_grading_targets(tmp_path):
    cfg = AppConfig()
    cfg.surfaces.api_port = 0
    c = AppContext(cfg, project_dir=str(tmp_path))
    try:
        calls = []
        c.grading.refresh_targets = lambda: calls.append(1) or []
        c.bus.publish("editor.ready")
        c.bus.publish("editor.level_changed")
        import time as _t
        end = _t.time() + 2
        while len(calls) < 2 and _t.time() < end:
            _t.sleep(0.01)
        assert len(calls) == 2
    finally:
        c.shutdown()


def test_shutdown_stops_grading(tmp_path):
    cfg = AppConfig()
    cfg.surfaces.api_port = 0
    c = AppContext(cfg, project_dir=str(tmp_path))
    c.shutdown()
    assert c.grading.stopped


def test_nudge_editor_typed_target_survives_enter():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from operator_board import qt
    from operator_board.ui.action_editors import NudgeEditor
    qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    ed = NudgeEditor(targets=lambda: [])
    ed.target.setEditText("ccr|Typed CCR")
    ed.target.lineEdit().returnPressed.emit()
    assert ed.params()["target"] == "ccr|Typed CCR"


def test_bypass_entries_are_not_remote_objects_settings(ctx):
    ctx.changes.record("Colour Grading", "Stage PPV", "Bypass", value="on", previous="off",
                       result="ok", kind="grade_switch", ref={"key": "ppv|Stage PPV"})
    assert ctx.changed_settings() == []


def test_nudge_action_with_text_amount_is_a_plain_failure(ctx):
    result = ctx.actions.invoke("grading.nudge", control="gain", amount="abc")
    assert not result.ok
    assert result.message == "The nudge amount must be a number"


def test_broken_look_in_preset_is_a_preset_problem(ctx):
    item = {"type": "grading_look", "data": ["not", "a", "look"]}
    with pytest.raises(presets.PresetProblem):
        presets.steps(ctx, {"items": [item]})[0][1]()

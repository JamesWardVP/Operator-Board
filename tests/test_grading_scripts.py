"""The colour grading scripts that run inside Unreal, run against a stand-in ``unreal`` module."""
import pytest

from operator_board.remote import scripts
from operator_board.remote.rc_client import python_result_wrapper as wrap
from tests import fake_unreal as fu


@pytest.fixture
def ue():
    mod = fu.build_module()
    level = fu.stage()
    return mod, level


def run(mod, body):
    return fu.run(wrap(body), mod)


def test_list_targets_in_priority_order(ue):
    mod, _ = ue
    out = run(mod, scripts.list_grading_targets())
    kinds = [t["kind"] for t in out["targets"]]
    assert kinds == ["ndisplay_cluster", "ndisplay_viewport", "icvfx_all_nodes", "icvfx_node", "ppv", "ppv", "ccr"]
    vp = out["targets"][1]
    assert vp["actor"] == "nDisplay Root" and vp["applies_to"] == ["VP_0", "VP_1"]
    cam = out["targets"][2]
    assert cam["actor"] == "nDisplay Root/ICVFXCamera" and cam["applies_to"] == []
    assert out["targets"][3]["applies_to"] == ["Node_0"]
    assert all(t["name"] and t["enabled"] is True for t in out["targets"])
    assert out["missing"] == {"ndisplay": "", "ccr": ""}


def test_missing_plugin_and_root_are_explained():
    mod = fu.build_module(with_ccr=False)
    level = fu.stage(with_ccr=False)
    level.actors.remove(level.root)
    out = run(mod, scripts.list_grading_targets())
    assert [t["kind"] for t in out["targets"]] == ["ppv", "ppv"]
    assert out["missing"]["ccr"] and out["missing"]["ndisplay"]


@pytest.mark.parametrize("kind,actor,applies", [
    ("ndisplay_cluster", "nDisplay Root", []),
    ("ndisplay_viewport", "nDisplay Root", ["VP_0", "VP_1"]),
    ("icvfx_all_nodes", "nDisplay Root/ICVFXCamera", []),
    ("icvfx_node", "nDisplay Root/ICVFXCamera", ["Node_0"]),
    ("ppv", "Stage PPV", []),
    ("ccr", "Face Sphere", []),
])
def test_write_then_read_every_kind(ue, kind, actor, applies):
    mod, _ = ue
    values = {"global.gain": [1.2, 1.0, 0.8, 1.1], "shadows.offset": [0.01, 0, -0.01, 0], "temp": 5600.0}
    run(mod, scripts.write_grade(kind, actor, applies, values, transact=True))
    out = run(mod, scripts.read_grade(kind, actor, applies))
    g = out["grade"]
    assert g["values"]["global.gain"] == [1.2, 1.0, 0.8, 1.1]
    assert g["values"]["shadows.offset"] == [0.01, 0, -0.01, 0]
    assert g["temp"] == 5600.0
    assert {"global.gain", "shadows.offset", "temp"} <= set(g["overrides"])
    assert out["enabled"] is True


def test_reset_unticks_override(ue):
    mod, level = ue
    run(mod, scripts.write_grade("ppv", "Stage PPV", [], {"global.gain": [2, 2, 2, 2]}, transact=False))
    run(mod, scripts.write_grade("ppv", "Stage PPV", [], {"global.gain": None}, transact=False))
    s = level.actors[0].settings
    assert s.override_color_gain is False and s.color_gain == fu.Vector4(1, 1, 1, 1)


def test_ccr_reset_is_neutral(ue):
    mod, level = ue
    run(mod, scripts.write_grade("ccr", "Face Sphere", [], {"global.offset": [0.1, 0, 0, 0], "tint": 0.3}, False))
    run(mod, scripts.write_grade("ccr", "Face Sphere", [], {"global.offset": None, "tint": None}, False))
    ccr = level.actors[-1]
    assert ccr.color_grading_settings.global_.offset == fu.Vector4() and ccr.tint == 0.0


def test_transaction_only_when_asked(ue):
    mod, _ = ue
    run(mod, scripts.write_grade("ppv", "Stage PPV", [], {"tint": 0.1}, transact=False))
    assert fu._State.transactions == []
    run(mod, scripts.write_grade("ppv", "Stage PPV", [], {"tint": 0.2}, transact=True))
    assert len(fu._State.transactions) == 1


def test_no_transaction_in_game_world():
    mod = fu.build_module()
    level = fu.stage(editor=False)
    run(mod, scripts.write_grade("ppv", "Stage PPV", [], {"tint": 0.2}, transact=True))
    assert fu._State.transactions == [] and level.actors[0].settings.white_tint == 0.2


def test_bypass_switches_per_kind(ue):
    mod, level = ue
    for kind, actor, applies in (("ndisplay_cluster", "nDisplay Root", []),
                                 ("ndisplay_viewport", "nDisplay Root", ["VP_1", "VP_0"]),
                                 ("icvfx_all_nodes", "nDisplay Root/ICVFXCamera", []),
                                 ("icvfx_node", "nDisplay Root/ICVFXCamera", ["Node_0"]),
                                 ("ppv", "Stage PPV", []), ("ccr", "Face Sphere", [])):
        run(mod, scripts.set_grading_enabled(kind, actor, applies, False, transact=False))
        assert run(mod, scripts.read_grade(kind, actor, applies))["enabled"] is False, kind
        run(mod, scripts.set_grading_enabled(kind, actor, applies, True, transact=False))
        assert run(mod, scripts.read_grade(kind, actor, applies))["enabled"] is True, kind
    stage_settings = level.root.current_config_data.stage_settings
    assert stage_settings.entire_cluster_color_grading.enable_entire_cluster_color_grading is True


def test_deleted_target_reports_plainly(ue):
    mod, level = ue
    level.actors.pop(0)
    out = run(mod, scripts.read_grade("ppv", "Stage PPV", []))
    assert out == {"error": "Target no longer in the level: Stage PPV"}


def test_unknown_viewport_entry(ue):
    mod, _ = ue
    out = run(mod, scripts.read_grade("ndisplay_viewport", "nDisplay Root", ["VP_9"]))
    assert "no longer" in out["error"]


def test_scripts_compile_with_awkward_names():
    for body in (scripts.list_grading_targets(),
                 scripts.read_grade("ppv", "Cam's \"PPV\"", []),
                 scripts.write_grade("ndisplay_viewport", "Root's", ["VP'1"], {"global.gain": [1, 1, 1, 1]}, True),
                 scripts.set_grading_enabled("ccr", "It's", [], False, True)):
        compile(wrap(body), "<unreal>", "exec")


def test_write_only_mentions_given_keys():
    body = scripts.write_grade("ppv", "PPV", [], {"global.gain": [1, 1, 1, 1]}, True)
    assert "global.gamma" not in body and "midtones.gain" not in body


def test_bypass_leaves_master_switch_alone(ue):
    mod, level = ue
    stage = level.root.current_config_data.stage_settings
    stage.enable_color_grading = False
    run(mod, scripts.set_grading_enabled("ndisplay_viewport", "nDisplay Root", ["VP_0", "VP_1"], False, False))
    run(mod, scripts.set_grading_enabled("ndisplay_viewport", "nDisplay Root", ["VP_0", "VP_1"], True, False))
    stage = level.root.current_config_data.stage_settings
    assert stage.enable_color_grading is False
    assert stage.per_viewport_color_grading[0].is_enabled is True


def test_enable_with_master_switches_master_on(ue):
    mod, level = ue
    level.cam.camera_settings.enable_inner_frustum_color_grading = False
    level.cam.camera_settings.all_nodes_color_grading.enable_inner_frustum_all_nodes_color_grading = False
    out = run(mod, scripts.set_grading_enabled("icvfx_all_nodes", "nDisplay Root/ICVFXCamera", [], True, False,
                                               master=True))
    assert out["enabled"] is True
    assert level.cam.camera_settings.enable_inner_frustum_color_grading is True


def test_read_reports_entry_and_master_switches(ue):
    mod, level = ue
    level.root.current_config_data.stage_settings.enable_color_grading = False
    out = run(mod, scripts.read_grade("ndisplay_cluster", "nDisplay Root", []))
    assert out["enabled"] is False and out["entry_enabled"] is True and out["master_enabled"] is False


def test_duplicate_entries_are_told_apart(ue):
    mod, level = ue
    stage = level.root.current_config_data.stage_settings
    stage.per_viewport_color_grading = [fu.PerViewport(), fu.PerViewport()]
    out = run(mod, scripts.list_grading_targets())
    vps = [t for t in out["targets"] if t["kind"] == "ndisplay_viewport"]
    assert [t["nth"] for t in vps] == [0, 1] and vps[0]["name"] != vps[1]["name"]
    run(mod, scripts.write_grade("ndisplay_viewport", "nDisplay Root", [], {"tint": 0.4}, False, nth=1))
    entries = level.root.current_config_data.stage_settings.per_viewport_color_grading
    assert entries[0].color_grading_settings.white_balance.white_tint == 0.0
    assert entries[1].color_grading_settings.white_balance.white_tint == 0.4


def test_window_shape_comes_from_window_type():
    mod = fu.build_module()
    level = fu.stage()
    level.actors.append(fu.ColorCorrectionWindow("CCW_1", "Window"))
    out = run(mod, scripts.list_grading_targets())
    assert out["targets"][-1]["name"] == "Window (square window)"


@pytest.mark.parametrize("kind,actor,applies", [
    ("ndisplay_cluster", "nDisplay Root", []),
    ("icvfx_node", "nDisplay Root/ICVFXCamera", ["Node_0"]),
    ("ppv", "Stage PPV", []),
])
def test_reset_unticks_ranges_and_white_balance(ue, kind, actor, applies):
    mod, _ = ue
    run(mod, scripts.write_grade(kind, actor, applies, {"midtones.gamma": [0.8, 1, 1, 1], "temp": 5000.0,
                                                      "tint": 0.2}, False))
    run(mod, scripts.write_grade(kind, actor, applies, {"midtones.gamma": None, "temp": None, "tint": None}, False))
    g = run(mod, scripts.read_grade(kind, actor, applies))["grade"]
    assert g["overrides"] == [] and g["values"]["midtones.gamma"] == [1, 1, 1, 1]
    assert (g["temp"], g["tint"]) == (6500.0, 0.0)


def test_icvfx_reference_without_camera_is_plain_error(ue):
    mod, _ = ue
    out = run(mod, scripts.read_grade("icvfx_all_nodes", "nDisplay Root", []))
    assert out["error"].startswith("Target no longer in the level")


def test_scripts_are_ascii():
    assert scripts.list_grading_targets().isascii()

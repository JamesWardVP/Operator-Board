"""LED modes and groups, the split status bar, the change log and its exports, and saved settings presets."""
import csv
import json
import os
import time

import pytest

from operator_board.context import AppContext
from operator_board.core.config import AppConfig, CameraPreset, LEDGroupConfig, LEDProcessorConfig
from operator_board.core.presets import PresetProblem, capture, export_preset, ordered, read_file, steps
from operator_board.ui.led_status import status_groups


@pytest.fixture
def ctx():
    cfg = AppConfig(led_processors=[LEDProcessorConfig(name=n, profile_id="simulator") for n in ("A", "B", "C")],
                    led_groups=[LEDGroupConfig(name="Walls", processors=["A", "B"])],
                    cameras=[CameraPreset(name="Wide", target="viewport", location=[1, 2, 3], rotation=[0, 90, 0])])
    cfg.surfaces.streamdeck_enabled = False
    c = AppContext(cfg)
    yield c
    c.shutdown()


def test_each_processor_or_group_has_one_mode(ctx):
    led = ctx.led
    assert "locate" in led.get("A").capabilities()
    assert led.set_mode("Walls", "frozen") == [] and led.modes() == {"A": "frozen", "B": "frozen", "C": "normal"}
    assert led.set_mode("A", "blackout") == [] and led.get("A").mode == "blackout" and not led.get("A").state.frozen
    assert led.set_mode("C", "locate") == [] and led.locate is False  # only C
    assert led.set_mode("all", "pattern", "red") == [] and led.test_pattern == "red"
    assert not any(p.state.locate or p.state.frozen for p in led.active())  # one mode at a time
    assert led.set_mode("all", "pattern", "red") == [] and set(led.modes().values()) == {"normal"}  # pressed again
    assert ctx.actions.invoke("led.locate").ok and led.locate
    assert ctx.actions.invoke("led.restore").ok and ctx.actions.state_of("led.restore")
    with pytest.raises(KeyError):
        led.resolve("Nope")


def test_status_bar_groups_processors_by_state(ctx):
    ctx.led.set_mode("Walls", "frozen")
    ctx.led.set_mode("C", "pattern", "green")
    assert status_groups(ctx.led) == [("frozen", "", "", ["A", "B"]), ("pattern", "green", "Green", ["C"])]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    from operator_board.ui.widgets import WallStatusBar
    bar = WallStatusBar()
    bar.show_processors(status_groups(ctx.led))
    assert bar.text() == "A, B: FROZEN  |  C: PATTERN GREEN" and len(bar.segments) == 2
    ctx.led.restore()
    bar.show_processors(status_groups(ctx.led))
    assert bar.text() == "ALL PROCESSORS NORMAL"
    bar.show_processors([("locate", "", "", ["Only"])])
    assert bar.text() == "WALL LOCATING"  # one processor is the wall
    app.processEvents()


def test_changes_are_logged_with_slate_take_and_values(ctx, tmp_path):
    ctx.config.takes.slate, ctx.config.takes.take = "Sc_12", 4
    before = len(ctx.changes.records)
    for action, params in (("led.set_mode", {"target": "Walls", "mode": "frozen"}), ("led.freeze", {}),
                           ("led.locate", {}), ("led.restore", {}), ("devices.connect_all", {}),
                           ("devices.stop_all", {})):
        ctx.actions.invoke(action, **params)
    assert len(ctx.changes.records) == before  # wall states and device control are not settings
    ctx.actions.invoke("takes.set_description", description="Wide")
    ctx.actions.invoke("led.brightness", percent=80)
    rec = ctx.changes.records[-2]
    assert (rec.category, rec.setting, rec.slate, rec.take, rec.result) == (
        "Takes", "Set take description", "Sc_12", 4, "ok")
    assert rec.value == {"description": "Wide"} and rec.date and rec.time
    assert ctx.changes.records[-1].setting == "Set wall brightness"
    ctx.changes.record("Remote Objects", "RectLight", "Intensity", 6.0, 3.0, kind="quick",
                       ref={"path": "/Game/M.M:PersistentLevel.RectLight", "control": "intensity"})
    ctx.changes.record("Remote Objects", "RectLight", "Intensity", 8.0, 6.0, kind="quick",
                       ref={"path": "/Game/M.M:PersistentLevel.RectLight", "control": "intensity"})
    assert ctx.changed_settings() == [{"kind": "quick", "ref": {"path": "/Game/M.M:PersistentLevel.RectLight",
                                                                "control": "intensity"},
                                       "item": "RectLight", "setting": "Intensity", "value": 8.0}]
    path = str(tmp_path / "log.csv")
    assert ctx.changes.export_csv(path) == len(ctx.changes.records)
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    assert rows[-1]["Value"] == "8" and rows[-1]["Previous value"] == "6" and rows[-1]["Slate"] == "Sc_12"
    reloaded = AppContext(AppConfig())  # the log is kept on disk
    assert len(reloaded.changes.records) == len(ctx.changes.records) and reloaded.changed_settings() == []
    reloaded.shutdown()


def test_production_spreadsheet(ctx, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    ctx.led.set_mode("C", "locate")
    path = str(tmp_path / "production.xlsx")
    ctx.production_export(path)
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Summary", "Change log", "Takes", "Camera positions", "LED processors",
                             "Unreal instances"]
    leds = list(wb["LED processors"].values)
    assert leds[0][:5] == ("Name", "Type", "Address", "Enabled", "Mode") and ("C" in [r[0] for r in leds])
    assert [r for r in leds if r[0] == "C"][0][4] == "locate"
    assert list(wb["Camera positions"].values)[1][0] == "Wide"


def test_presets_capture_and_apply_in_order(ctx, tmp_path):
    ctx.led.set_mode("Walls", "frozen")
    preset = {"name": "Show", "items": [capture(ctx, "take"), capture(ctx, "led"), capture(ctx, "cameras")]}
    assert [i["type"] for i in ordered(preset["items"])] == ["cameras", "led", "take"]  # fixed order
    ctx.led.restore()
    ctx.config.cameras[0].location = [9, 9, 9]
    ctx.config.takes.slate = "Changed"
    for label, fn in steps(ctx, preset):
        assert fn()
    assert ctx.led.modes() == {"A": "frozen", "B": "frozen", "C": "normal"}
    assert ctx.config.cameras[0].location == [1, 2, 3] and ctx.config.takes.slate == "Scene_01"
    assert any(r.source == "Preset" for r in ctx.changes.records)
    path = str(tmp_path / "show.json")
    export_preset(path, preset)
    assert read_file(path)["name"] == "Show" and len(read_file(path)["items"]) == 3
    with pytest.raises(PresetProblem, match="No LED processor called Gone"):
        steps(ctx, {"items": [{"type": "led", "data": {"Gone": {"mode": "frozen"}}}]})[0][1]()
    with pytest.raises(PresetProblem, match="no camera positions"):
        steps(ctx, {"items": [{"type": "cameras", "data": []}]})[0][1]()
    with pytest.raises(PresetProblem):
        capture(ctx, "object_settings")  # nothing changed yet


def test_preset_from_other_exports(ctx, tmp_path):
    cams = str(tmp_path / "cams.csv")
    ctx.cameras.export_csv(cams)
    assert read_file(cams)["items"][0]["type"] == "cameras"
    surfaces = str(tmp_path / "surfaces.json")
    ctx.surfaces.export(surfaces)
    item = read_file(surfaces)["items"][0]
    assert item["type"] == "surfaces" and len(item["data"]) == 1
    (tmp_path / "junk.json").write_text(json.dumps({"x": 1}))
    with pytest.raises(ValueError):
        read_file(str(tmp_path / "junk.json"))


def test_saved_settings_page_applies_a_preset(ctx, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)
    from operator_board.ui.main_window import MainWindow
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    monkeypatch.setattr(qt.QtWidgets.QMessageBox, "question", lambda *a, **k: qt.QtWidgets.QMessageBox.Yes)
    ctx.led.set_mode("C", "blackout")
    ctx.config.setting_presets = [{"name": "Blackout C", "items": [capture(ctx, "led")]}]
    ctx.led.restore()
    window = MainWindow(ctx)
    window.show_page("Saved Settings")
    page = window.current_page()
    page.refresh()
    assert page.preset_list.count() == 1 and page.items.rowCount() == 1
    page._apply()
    deadline = time.time() + 5
    while page._run is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert ctx.led.get("C").mode == "blackout" and page.progress.item(0).text().startswith("✓")
    page._fill_log()
    assert page.log_table.rowCount() >= 1
    window.skip_save_on_close = True
    window.close()


def test_remote_objects_undo_puts_the_value_back(ctx, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)
    from operator_board.ui.main_window import MainWindow
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    applied = []
    monkeypatch.setattr(ctx, "apply_setting", lambda kind, ref, value: applied.append((kind, ref["control"], value)))
    window = MainWindow(ctx)
    window.show_page("Remote Objects")
    page = window.current_page()
    ref = {"path": "/Game/M.M:PersistentLevel.RectLight", "control": "intensity"}
    page._known[page._key("quick", ref, "Intensity")] = 3.0  # as read from the editor
    page._changed("quick", ref, "RectLight", "Intensity", 6.0)
    page._changed("quick", ref, "RectLight", "Intensity", 9.0)
    assert page.undo_btn.isEnabled()
    for _ in range(2):
        page._undo()
        deadline = time.time() + 5
        while page.busy.active and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
    assert applied == [("quick", "intensity", 6.0), ("quick", "intensity", 3.0)] and not page.undo_btn.isEnabled()
    assert [r.source for r in ctx.changes.records[-2:]] == ["Undo", "Undo"]
    window.skip_save_on_close = True
    window.close()

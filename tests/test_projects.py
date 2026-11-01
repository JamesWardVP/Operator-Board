"""Operator Board projects: everything shoot-specific kept per project, with duplicate, rename,
import/export and templates."""
import json
import os
import zipfile

import pytest

from operator_board.context import AppContext
from operator_board.core import projects
from operator_board.core.config import AppConfig, CameraPreset, DeviceConfig, app_home, default_config_path, save_config
from operator_board.core.projects import ProjectInfo


def _open(project_id):
    ctx = AppContext(project_dir=projects.project_dir(project_id))
    ctx.config.takes.record_with_take_recorder = False  # no editor here: log takes locally
    return ctx


def test_projects_keep_their_own_settings_takes_and_change_log():
    a = projects.create(ProjectInfo(name="Ad shoot", production="Brand X"))
    b = projects.create(ProjectInfo(name="Feature"))
    ctx = _open(a.id)
    ctx.config.cameras.append(CameraPreset(name="Hero"))
    ctx.config.devices.append(DeviceConfig(name="Node_0", role="ndisplay"))
    ctx.takes.start()
    ctx.takes.stop()
    ctx.actions.invoke("takes.set_description", description="Wide")
    ctx.save()
    ctx.shutdown()
    other = _open(b.id)
    assert other.config.cameras == [] and other.config.devices == [] and other.takes.log.takes == []
    assert other.changes.records == []
    other.shutdown()
    again = _open(a.id)
    assert [c.name for c in again.config.cameras] == ["Hero"] and len(again.takes.log.takes) == 1
    assert again.changes.records and again.changes.records[-1].project == "Ad shoot"
    assert os.path.exists(os.path.join(projects.project_dir(a.id), "take_log.json"))
    again.shutdown()


def test_two_projects_on_one_switchboard_config(tmp_path):
    a = projects.create(ProjectInfo(name="A"))
    b = projects.create(ProjectInfo(name="B"))
    sb = str(tmp_path / "configs" / "Stage.json")
    ctx = _open(a.id)
    ctx.new_switchboard_config(sb, "Stage", "D:/P/P.uproject", "C:/UE_5.6")
    ctx.config.cameras.append(CameraPreset(name="A cam"))
    ctx.save()
    ctx.shutdown()
    ctx = _open(b.id)
    ctx.open_switchboard_config(sb)
    assert ctx.config.cameras == []  # the same Switchboard config, B's own settings
    ctx.config.cameras.append(CameraPreset(name="B cam"))
    ctx.save()
    ctx.shutdown()
    assert not os.path.exists(str(tmp_path / "configs" / "Stage.opboard"))  # kept in the projects instead
    reopened = _open(a.id)
    assert reopened.switchboard_config == os.path.abspath(sb) and [c.name for c in reopened.config.cameras] == ["A cam"]
    reopened.shutdown()


def test_duplicate_rename_and_details():
    a = projects.create(ProjectInfo(name="Day 1"))
    ctx = _open(a.id)
    ctx.config.cameras.append(CameraPreset(name="Wide"))
    ctx.save()
    ctx.shutdown()
    copy = projects.duplicate(a.id, "Day 1 checkpoint")
    assert copy.id != a.id and [p.name for p in projects.list_projects()][0] in ("Day 1", "Day 1 checkpoint")
    ctx = _open(copy.id)
    assert [c.name for c in ctx.config.cameras] == ["Wide"]
    ctx.shutdown()
    assert projects.rename(copy.id, "Day 1").name == "Day 1 2"  # names stay unique
    info = projects.read_info(a.id)
    info.director = "Sam"
    projects.update_details(info)
    assert projects.read_info(a.id).director == "Sam"


def test_export_import_and_templates(tmp_path):
    a = projects.create(ProjectInfo(name="Studio template", client="Studio"))
    ctx = _open(a.id)
    ctx.config.devices.append(DeviceConfig(name="Editor_1"))
    ctx.config.cameras.append(CameraPreset(name="Wide"))
    ctx.takes.start()
    ctx.takes.stop()
    ctx.save()
    ctx.shutdown()
    full = str(tmp_path / "full.opproject")
    template = str(tmp_path / "template.opproject")
    projects.export_project(a.id, full)
    projects.export_project(a.id, template, include_shoot_data=False)
    assert "take_log.json" in zipfile.ZipFile(full).namelist()
    assert "take_log.json" not in zipfile.ZipFile(template).namelist()
    shared = projects.import_project(full)
    assert shared.name == "Studio template 2" and shared.client == "Studio"
    new = projects.import_project(template, name="Next shoot")
    projects.clear_shoot_data(new.id)
    ctx = _open(new.id)
    assert [d.name for d in ctx.config.devices] == ["Editor_1"] and ctx.config.cameras == []
    assert ctx.takes.log.takes == []
    ctx.shutdown()
    bad = tmp_path / "bad.opproject"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("project.json", json.dumps({"name": "x"}))
        zf.writestr("../escape.txt", "no")
    with pytest.raises(ValueError, match="unsafe"):
        projects.import_project(str(bad))


def test_new_project_from_another_projects_stage_setup():
    a = projects.create(ProjectInfo(name="Volume A"))
    ctx = _open(a.id)
    ctx.config.devices.append(DeviceConfig(name="Node_0"))
    ctx.config.cameras.append(CameraPreset(name="Hero"))
    ctx.config.takes.slate, ctx.config.takes.take = "Sc_40", 12
    ctx.config.setting_presets.append({"name": "Night", "items": []})
    ctx.save()
    ctx.shutdown()
    b = projects.create(ProjectInfo(name="New show"), setup_from=a.id)
    ctx = _open(b.id)
    assert [d.name for d in ctx.config.devices] == ["Node_0"]
    assert ctx.config.cameras == [] and ctx.config.setting_presets == [] and ctx.config.takes.take == 1
    ctx.shutdown()


def test_existing_settings_become_a_project():
    cfg = AppConfig(cameras=[CameraPreset(name="Old cam")])
    save_config(cfg, default_config_path())
    open(os.path.join(app_home(), "take_log.json"), "w").write(json.dumps({"takes": []}))
    info = projects.migrate_existing_setup()
    assert info.name == "My stage" and projects.migrate_existing_setup() is None  # only once
    ctx = _open(info.id)
    assert [c.name for c in ctx.config.cameras] == ["Old cam"]
    ctx.shutdown()
    projects.remember(info.id)
    assert projects.last_project() == info.id


def test_project_details_in_the_production_spreadsheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    a = projects.create(ProjectInfo(name="Ad shoot", production="Brand X", director="Sam"))
    ctx = _open(a.id)
    path = str(tmp_path / "p.xlsx")
    ctx.production_export(path)
    summary = dict(list(openpyxl.load_workbook(path)["Summary"].values)[1:])
    assert summary["Project"] == "Ad shoot" and summary["Production"] == "Brand X" and summary["Director"] == "Sam"
    ctx.shutdown()


def test_start_screen_opens_a_project():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)
    from operator_board.ui.start_screen import StartScreen
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    projects.create(ProjectInfo(name="First"))
    second = projects.create(ProjectInfo(name="Second", production="Show"))
    screen = StartScreen(current=second.id)
    assert screen.list.topLevelItemCount() == 2 and "Show" in screen.details.text()
    screen._open()
    assert screen.chosen == second.id
    app.processEvents()

"""Builds the full window offscreen and visits every page."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)


def test_main_window_pages(tmp_path):
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig, DeviceConfig, GroupConfig, LEDProcessorConfig
    from operator_board.ui.main_window import MainWindow

    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    cfg = AppConfig(devices=[DeviceConfig(name="Ed")], groups=[GroupConfig(name="G", devices=["Ed"])],
                    led_processors=[LEDProcessorConfig(name="Sim", profile_id="simulator")])
    ctx = AppContext(cfg)
    window = MainWindow(ctx)
    window.show()
    for i in range(window.nav.count()):
        window.nav.setCurrentRow(i)
        app.processEvents()
        window.current_page().refresh()
    ctx.actions.invoke("led.freeze")
    window.pages[0].refresh()
    assert window.pages[0].freeze_btn.text() == "FREEZE"
    assert window.pages[0].banner.text() == "WALL FROZEN"
    assert window.pages[0].tiles["Ed"].link.text() == "Connect"
    # switch to a new Switchboard config and back while the window is open
    stage_page = next(p for p in window.pages if p.title == "Stage")
    sb_path = str(tmp_path / "configs" / "Stage_A.json")
    ctx.new_switchboard_config(sb_path, "Stage_A", "D:/P/P.uproject", "C:/UE_5.6")
    ctx.config.switchboard.config_dir = str(tmp_path / "configs")
    app.processEvents()
    stage_page.refresh()
    assert stage_page.table.rowCount() == 1
    assert "Stage_A" in window.windowTitle()
    ctx.close_switchboard_config()
    app.processEvents()
    assert "Switchboard" not in window.windowTitle()
    window.skip_save_on_close = True
    window.close()


def test_operate_scene_and_take_can_be_typed():
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig
    from operator_board.listener.client import wait_until
    from operator_board.ui.main_window import MainWindow

    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    cfg = AppConfig()
    cfg.takes.record_with_take_recorder = False
    ctx = AppContext(cfg)
    window = MainWindow(ctx)
    page = window.pages[0]
    page.refresh()
    assert page.scene_edit.text() == cfg.takes.slate and page.take_edit.text() == str(cfg.takes.take)

    page.scene_edit.setText("Car_Chase")
    page.scene_edit.editingFinished.emit()
    assert wait_until(lambda: (app.processEvents() or cfg.takes.slate == "Car_Chase"), 3)
    assert cfg.takes.take == 1  # a new scene starts at take 1

    page.take_edit.setText("57")  # typing 57 and pressing Enter
    page.take_edit.editingFinished.emit()
    assert wait_until(lambda: (app.processEvents() or cfg.takes.take == 57), 3)
    page.refresh()
    assert page.take_label.text() == "Car_Chase  ·  T057"

    ctx.takes.start()
    page.refresh()
    assert not page.scene_edit.isEnabled() and not page.take_edit.isEnabled()
    ctx.takes.stop()
    page.refresh()
    assert page.take_edit.isEnabled() and page.take_edit.text() == "58"  # auto-increment after the take
    window.skip_save_on_close = True
    window.close()


def test_led_wall_overlay_when_unavailable():
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig, LEDProcessorConfig
    from operator_board.ui.main_window import MainWindow

    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    # no processor set up
    ctx = AppContext(AppConfig())
    window = MainWindow(ctx)
    window.show()
    page = window.pages[0]
    page.refresh()
    assert page.wall_overlay.isVisible() and "UNAVAILABLE" in page.wall_overlay.title.text()
    assert not page.freeze_btn.isEnabled()
    window.skip_save_on_close = True
    window.close()

    # a processor that cannot be reached, then "Use anyway", then it comes back
    ctx = AppContext(AppConfig(led_processors=[LEDProcessorConfig(name="Wall A", profile_id="simulator")]))
    window = MainWindow(ctx)
    window.show()
    page = window.pages[0]
    proc = ctx.led.processors[0]
    proc.state.online = False
    page.refresh()
    assert page.wall_overlay.isVisible() and "NOT CONNECTED" in page.wall_overlay.title.text()
    assert "Wall A" in page.wall_overlay.text.text()
    page._use_wall_anyway()
    page.refresh()
    assert not page.wall_overlay.isVisible() and page.freeze_btn.isEnabled()
    proc.state.online = True
    page.refresh()
    assert not page.wall_overlay.isVisible() and page.freeze_btn.isEnabled()
    window.skip_save_on_close = True
    window.close()
    app.processEvents()


def test_record_button_plays_the_countdown():
    import time
    from operator_board.qt import QtWidgets
    from operator_board.ui.widgets import RecordButton
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    left = [2.4]
    btn = RecordButton(lambda: left[0])
    btn.resize(300, 60)
    btn.show_state(True, True)
    assert btn.text() == "" and btn._timer.isActive()
    btn.grab()  # paints the number
    btn.show_state(True, False)  # Unreal started recording: "GO!" then STOP
    assert btn.text() == ""
    btn.grab()
    btn._go_until = time.monotonic() - 1
    btn._tick()
    assert btn.text() == "■ STOP" and not btn._timer.isActive()
    btn.show_state(False, False)
    assert btn.text() == "● RECORD"


def test_sidebar_groups_and_page_links():
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig
    from operator_board.qt import QtWidgets
    from operator_board.ui.main_window import MainWindow
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ctx = AppContext(AppConfig())
    window = MainWindow(ctx)
    assert window.nav.count() == len(window.pages)
    assert window.nav.group_names() == ["ADVANCED", "SETUP"]
    window.nav.set_group_expanded("Setup", False)
    window.show_page("Devices")  # opens its collapsed group
    assert window.current_page().title == "Devices"
    window.show_page("Cameras & Levels", "Camera Positions")
    assert window.current_page().tabs.tabText(window.current_page().tabs.currentIndex()) == "Camera Positions"
    window.skip_save_on_close = True
    window.close()
    ctx.shutdown()


def test_group_dialog_builds_a_group():
    from operator_board.core.config import GroupConfig
    from operator_board.qt import QtWidgets, Qt
    from operator_board.ui.group_dialog import GroupDialog
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = GroupDialog(None, GroupConfig(name="Wall", devices=["Node_A"], color="#e03131"),
                         ["Editor", "Node_A", "Node_B"], ["Editors"])
    dialog.members.item(2).setCheckState(Qt.Checked)
    dialog.name.setText("Wall nodes")
    dialog._set_color("#2f9e44")
    group = dialog.result_group
    assert group.name == "Wall nodes" and group.devices == ["Node_A", "Node_B"] and group.color == "#2f9e44"

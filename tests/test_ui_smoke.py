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

"""Builds the full window offscreen and visits every page."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)


def test_main_window_pages():
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
        window.stack.currentWidget().refresh()
    ctx.actions.invoke("led.freeze")
    window.pages[0].refresh()
    assert window.pages[0].freeze_btn.text() == "FREEZE"
    assert window.pages[0].banner.text() == "WALL FROZEN"
    window.skip_save_on_close = True
    window.close()

"""Keyboard focus must be visible under the app's stylesheet (WCAG 2.4.7)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from operator_board.qt import QtCore, QtWidgets  # noqa: E402

QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from operator_board.ui.theme import STYLESHEET  # noqa: E402


@pytest.fixture(autouse=True)
def delete_widgets():
    yield
    for w in QtWidgets.QApplication.topLevelWidgets():
        w.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def _changed_pixels(make):
    win = QtWidgets.QWidget()
    win.setStyleSheet(STYLESHEET)
    lay = QtWidgets.QVBoxLayout(win)
    widget, other = make(), QtWidgets.QPushButton("Other")
    lay.addWidget(widget)
    lay.addWidget(other)
    win.show()
    win.activateWindow()
    other.setFocus()
    QtWidgets.QApplication.processEvents()
    before = widget.grab().toImage()
    widget.setFocus()
    QtWidgets.QApplication.processEvents()
    assert widget.hasFocus()
    after = widget.grab().toImage()
    return sum(1 for x in range(before.width()) for y in range(before.height())
               if before.pixel(x, y) != after.pixel(x, y))


def _combo():
    c = QtWidgets.QComboBox()
    c.addItems(["Entire cluster"])
    return c


@pytest.mark.parametrize("make", [lambda: QtWidgets.QPushButton("Reset grade"), _combo,
                                  lambda: QtWidgets.QLineEdit("1.00")],
                         ids=["button", "drop-down", "field"])
def test_focused_control_shows_a_focus_ring(make):
    # A caret alone is not a focus indicator: the whole outline must change, a few hundred pixels.
    assert _changed_pixels(make) > 200

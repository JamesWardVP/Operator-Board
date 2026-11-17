"""The colour wheel, its wheel group (title, slider, typed values) and the Temperature / Tint slider."""
import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from operator_board.qt import QtCore, QtWidgets, Qt  # noqa: E402
from operator_board.core.grading import CONTROLS, tint_hex  # noqa: E402

QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from operator_board.ui.color_wheel import ColorWheel, GradeSlider, WheelControl, channel_direction  # noqa: E402


@pytest.fixture(autouse=True)
def delete_widgets():
    """Delete each test's widgets while Qt is still running (left for Python to free at exit,
    PySide can crash on the way out)."""
    yield
    for w in QtWidgets.QApplication.topLevelWidgets():
        w.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def mouse(widget, kind, pos, buttons=Qt.LeftButton, mods=Qt.NoModifier):
    types = {"press": QtCore.QEvent.MouseButtonPress, "move": QtCore.QEvent.MouseMove,
             "release": QtCore.QEvent.MouseButtonRelease, "double": QtCore.QEvent.MouseButtonDblClick}
    p = QtCore.QPointF(*pos)
    button = Qt.NoButton if kind == "move" else Qt.LeftButton
    ev = QtGui_mouse(types[kind], p, widget.mapToGlobal(p.toPoint()), button, buttons, mods)
    QtWidgets.QApplication.sendEvent(widget, ev)


def QtGui_mouse(t, p, g, button, buttons, mods):
    from operator_board.qt import QtGui
    return QtGui.QMouseEvent(t, p, QtCore.QPointF(g), button, buttons, mods)


class Spy:
    def __init__(self, widget):
        self.values, self.started, self.finished = [], 0, 0
        widget.changed.connect(self.values.append)
        widget.gestureStarted.connect(self._start)
        widget.gestureFinished.connect(self._finish)

    def _start(self):
        self.started += 1

    def _finish(self):
        self.finished += 1


def make_wheel(control="gain"):
    w = ColorWheel(CONTROLS[control])
    w.resize(200, 200)
    w.set_value(CONTROLS[control] and (1.0, 1.0, 1.0, 1.0))
    return w


def drag(w, dx, dy, mods=Qt.NoModifier):
    c = (w.width() / 2, w.height() / 2)
    mouse(w, "press", c, mods=mods)
    mouse(w, "move", (c[0] + dx, c[1] + dy), mods=mods)
    mouse(w, "release", (c[0] + dx, c[1] + dy), buttons=Qt.NoButton, mods=mods)


def test_drag_towards_red_raises_red_keeps_brightness():
    w = make_wheel()
    spy = Spy(w)
    ux, uy = channel_direction("r")
    drag(w, 30 * ux, 30 * uy)
    r, g, b, y = spy.values[-1]
    assert r > 1.0 and r > g and r > b and y == 1.0
    assert spy.started == 1 and spy.finished == 1


def test_shift_drag_is_ten_times_finer():
    coarse, fine = make_wheel(), make_wheel()
    a, b = Spy(coarse), Spy(fine)
    ux, uy = channel_direction("g")
    drag(coarse, 20 * ux, 20 * uy)
    drag(fine, 20 * ux, 20 * uy, mods=Qt.ShiftModifier)
    move_coarse = a.values[-1][1] - 1.0
    move_fine = b.values[-1][1] - 1.0
    assert move_fine == pytest.approx(move_coarse / 10, rel=0.05)


def test_click_does_not_jump_the_puck():
    w = make_wheel()
    spy = Spy(w)
    mouse(w, "press", (180, 100))
    mouse(w, "release", (180, 100), buttons=Qt.NoButton)
    assert all(v == (1.0, 1.0, 1.0, 1.0) for v in spy.values)


def test_double_click_resets_with_both_gesture_signals():
    w = make_wheel()
    w.set_value((1.3, 0.9, 1.0, 1.2))
    spy = Spy(w)
    mouse(w, "double", (100, 100))
    assert spy.values[-1] == (1.0, 1.0, 1.0, 1.0)
    assert spy.started == 1 and spy.finished == 1


def test_offset_wheel_neutral_is_zero():
    w = ColorWheel(CONTROLS["offset"])
    w.resize(200, 200)
    w.set_value((0.1, 0.0, 0.0, 0.05))
    spy = Spy(w)
    mouse(w, "double", (100, 100))
    assert spy.values[-1] == (0.0, 0.0, 0.0, 0.0)


def test_round_trip_keeps_common_part():
    w = make_wheel()
    w.set_value((1.2, 1.2, 1.2, 1.0))  # all three raised together: no colour, but kept
    spy = Spy(w)
    drag(w, 0.5, 0)  # a tiny move
    r, g, b, _y = spy.values[-1]
    assert (r + g + b) / 3 == pytest.approx(1.2, abs=0.01)


def test_scroll_changes_brightness_only():
    w = make_wheel()
    spy = Spy(w)
    from operator_board.qt import QtGui
    ev = QtGui.QWheelEvent(QtCore.QPointF(100, 100), QtCore.QPointF(w.mapToGlobal(QtCore.QPoint(100, 100))),
                           QtCore.QPoint(0, 0), QtCore.QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                           Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(w, ev)
    QtWidgets.QApplication.sendEvent(w, ev)  # two notches in a row: still one gesture
    r, g, b, y = spy.values[-1]
    assert (r, g, b) == (1.0, 1.0, 1.0) and y == pytest.approx(1.02)
    assert spy.started == 1 and spy.finished == 0
    import time
    end = time.time() + 3
    while not spy.finished and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    assert spy.finished == 1


def test_puck_stays_inside_the_wheel():
    w = make_wheel()
    spy = Spy(w)
    drag(w, 900, 0)
    r, g, b, _ = spy.values[-1]
    lo, hi = CONTROLS["gain"].lo, CONTROLS["gain"].hi
    assert all(lo <= c <= hi for c in (r, g, b))


# ---- the wheel group -----------------------------------------------------------------------------
def test_wheel_control_shows_titles_and_fields():
    wc = WheelControl(CONTROLS["lift"])
    assert wc.title.text() == "Lift" and wc.subtitle.text() == "Shadows · Offset"
    assert wc.subtitle.font().italic()
    wc.set_value((0.1, 0.0, -0.1, 0.05))
    assert [f.text() for f in wc.fields] == ["0.100", "0.000", "-0.100", "0.050"]


def test_cycle_button_switches_to_hex():
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((1.0, 0.9, 0.8, 1.0))
    wc.mode_button.click()
    assert wc.mode == "hex"
    assert wc.hex_field.isVisibleTo(wc) and not wc.fields[0].isVisibleTo(wc)
    assert wc.hex_field.text() == tint_hex((1.0, 0.9, 0.8))
    wc.mode_button.click()
    assert wc.mode == "rgb"


def test_typing_hex_sets_colour_keeps_brightness():
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((1.0, 1.0, 1.0, 1.3))
    spy = Spy(wc)
    wc.mode_button.click()
    wc.hex_field.setText("#FF8080")
    wc.hex_field.editingFinished.emit()
    r, g, b, y = spy.values[-1]
    assert y == 1.3 and r > g and g == pytest.approx(b)
    assert spy.started == 1 and spy.finished == 1


@pytest.mark.parametrize("bad", ["#GGG", "abc", ""])
def test_bad_hex_changes_nothing(bad):
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((1.0, 0.9, 0.8, 1.0))
    spy = Spy(wc)
    wc.mode_button.click()
    wc.hex_field.setText(bad)
    wc.hex_field.editingFinished.emit()
    assert spy.values == [] and wc.value() == (1.0, 0.9, 0.8, 1.0)
    assert wc.hex_field.text() == tint_hex((1.0, 0.9, 0.8))


def test_typed_number_is_clamped():
    wc = WheelControl(CONTROLS["gamma"])
    wc.set_value((1.0, 1.0, 1.0, 1.0))
    spy = Spy(wc)
    wc.fields[0].setText("-5")
    wc.fields[0].editingFinished.emit()
    assert spy.values[-1][0] == CONTROLS["gamma"].lo
    assert wc.fields[0].text() == "0.000"


def test_typed_text_that_is_not_a_number_changes_nothing():
    wc = WheelControl(CONTROLS["gamma"])
    wc.set_value((1.0, 1.0, 1.0, 1.0))
    spy = Spy(wc)
    wc.fields[1].setText("abc")
    wc.fields[1].editingFinished.emit()
    assert spy.values == [] and wc.fields[1].text() == "1.000"


def test_brightness_slider_drag_is_one_gesture():
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((1.0, 1.0, 1.0, 1.0))
    spy = Spy(wc)
    wc.brightness.sliderPressed.emit()
    wc.brightness.setValue(wc.brightness.value() + 50)
    wc.brightness.setValue(wc.brightness.value() + 50)
    wc.brightness.sliderReleased.emit()
    assert spy.started == 1 and spy.finished == 1 and spy.values[-1][3] > 1.0


def test_set_value_emits_nothing():
    wc = WheelControl(CONTROLS["gain"])
    spy = Spy(wc)
    wc.set_value((1.2, 1.0, 1.0, 1.1))
    assert spy.values == [] and spy.started == 0


# ---- Temperature / Tint ------------------------------------------------------------------------
def test_grade_slider_typed_value_clamped_and_reset():
    s = GradeSlider(CONTROLS["temperature"])
    assert s.title.text() == "Temperature" and s.subtitle.font().italic()
    s.set_value(6500.0)
    spy = Spy(s)
    s.field.setText("99999")
    s.field.editingFinished.emit()
    assert spy.values[-1] == 15000.0
    s.reset()
    assert spy.values[-1] == 6500.0 and spy.started == 2 and spy.finished == 2


def test_grade_slider_drag_is_one_gesture():
    s = GradeSlider(CONTROLS["tint"])
    s.set_value(0.0)
    spy = Spy(s)
    s.slider.sliderPressed.emit()
    s.slider.setValue(s.slider.value() + 100)
    s.slider.sliderReleased.emit()
    assert spy.started == 1 and spy.finished == 1 and spy.values[-1] > 0


def test_channel_directions_are_a_third_of_a_turn_apart():
    angles = [math.atan2(*reversed(channel_direction(c))) for c in "rgb"]
    diffs = sorted((a - b) % (2 * math.pi) for a, b in zip(angles, angles[1:] + angles[:1]))
    assert diffs == pytest.approx([2 * math.pi / 3] * 3)

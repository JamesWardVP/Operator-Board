"""The colour wheel, its wheel group (title, slider, typed values) and the Temperature / Tint slider."""
import math
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from operator_board.qt import QtCore, QtGui, QtWidgets, Qt  # noqa: E402
from operator_board.core.grading import CONTROLS, tint_hex  # noqa: E402

QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from operator_board.ui import color_wheel  # noqa: E402
from operator_board.ui.color_wheel import ColorWheel, GradeSlider, WheelControl, channel_direction  # noqa: E402

NEUTRAL = (1.0, 1.0, 1.0, 1.0)


@pytest.fixture(autouse=True)
def quick_idle(monkeypatch):
    """Scrolls and key presses count as one change until they pause; make the pause short."""
    monkeypatch.setattr(color_wheel, "IDLE_MS", 30)


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
    ev = QtGui.QMouseEvent(types[kind], p, QtCore.QPointF(widget.mapToGlobal(p.toPoint())), button, buttons, mods)
    QtWidgets.QApplication.sendEvent(widget, ev)


def scroll(widget, notches=1, mods=Qt.NoModifier):
    p = QtCore.QPointF(widget.width() / 2, widget.height() / 2)
    ev = QtGui.QWheelEvent(p, QtCore.QPointF(widget.mapToGlobal(p.toPoint())), QtCore.QPoint(0, 0),
                           QtCore.QPoint(0, 120 * notches), Qt.NoButton, mods, Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(widget, ev)
    return ev


def focus(widget):
    widget.show()
    widget.activateWindow()
    widget.setFocus()
    QtWidgets.QApplication.processEvents()
    assert widget.hasFocus()


def wait_until(cond, timeout=2.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.005)
    return cond()


def type_into(field, text):
    """What typing does: the text changes and the field knows the person edited it."""
    field.setText(text)
    field.setModified(True)
    field.editingFinished.emit()


class Spy:
    def __init__(self, widget):
        self.values, self.started, self.finished = [], 0, 0
        self.order = []
        widget.changed.connect(self._changed)
        widget.gestureStarted.connect(self._start)
        widget.gestureFinished.connect(self._finish)

    def _changed(self, v):
        self.values.append(v)
        self.order.append("chg")

    def _start(self):
        self.started += 1
        self.order.append("start")

    def _finish(self):
        self.finished += 1
        self.order.append("end")

    def balanced(self):
        depth = 0
        for e in self.order:
            if e == "start":
                depth += 1
                if depth > 1:
                    return False
            elif e == "end":
                depth -= 1
                if depth < 0:
                    return False
            elif depth != 1:
                return False  # a change outside a gesture
        return depth == 0


def make_wheel(control="gain", value=NEUTRAL):
    w = ColorWheel(CONTROLS[control])
    w.resize(200, 200)
    w.set_value(value)
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
    assert spy.started == 1 and spy.finished == 1 and spy.balanced()


def test_shift_drag_is_ten_times_finer():
    coarse, fine = make_wheel(), make_wheel()
    a, b = Spy(coarse), Spy(fine)
    ux, uy = channel_direction("g")
    drag(coarse, 20 * ux, 20 * uy)
    drag(fine, 20 * ux, 20 * uy, mods=Qt.ShiftModifier)
    assert b.values[-1][1] - 1.0 == pytest.approx((a.values[-1][1] - 1.0) / 10, rel=0.05)


def test_click_without_a_move_is_no_change_at_all():
    w = make_wheel()
    spy = Spy(w)
    mouse(w, "press", (180, 100))
    mouse(w, "release", (180, 100), buttons=Qt.NoButton)
    assert spy.order == []


def test_real_double_click_resets_once():
    w = make_wheel(value=(1.3, 0.9, 1.0, 1.2))
    spy = Spy(w)
    for kind in ("press", "release", "double", "release"):
        mouse(w, kind, (100, 100), buttons=Qt.NoButton if kind == "release" else Qt.LeftButton)
    assert spy.values == [NEUTRAL] and spy.order == ["start", "chg", "end"]


def test_offset_wheel_neutral_is_zero():
    w = make_wheel("offset", (0.1, 0.0, 0.0, 0.05))
    spy = Spy(w)
    mouse(w, "double", (100, 100))
    assert spy.values[-1] == (0.0, 0.0, 0.0, 0.0)


def test_round_trip_is_exact_with_a_tint_and_past_the_rim():
    for v in ((1.2, 0.9, 1.05, 1.1), (1.8, 1.8, 1.8, 1.0), (2.0, 0.0, 0.0, 1.0)):
        assert make_wheel(value=v).value() == pytest.approx(v)


def test_drag_keeps_the_common_part():
    w = make_wheel(value=(1.2, 1.2, 1.2, 1.0))  # all three raised together: no colour, but kept
    spy = Spy(w)
    drag(w, 0.5, 0)
    r, g, b, _y = spy.values[-1]
    assert (r + g + b) / 3 == pytest.approx(1.2, abs=0.01)


def test_scroll_with_focus_is_one_gesture_until_it_pauses():
    w = make_wheel()
    focus(w)
    spy = Spy(w)
    scroll(w)
    scroll(w)
    assert spy.values[-1] == pytest.approx((1.0, 1.0, 1.0, 1.02)) and spy.finished == 0
    assert wait_until(lambda: spy.finished == 1)
    assert spy.started == 1 and spy.balanced()


def test_scroll_without_focus_is_left_to_the_page():
    w = make_wheel()
    spy = Spy(w)
    ev = scroll(w)
    assert spy.order == [] and not ev.isAccepted()


def test_scroll_during_a_drag_is_part_of_the_drag():
    w = make_wheel()
    focus(w)
    spy = Spy(w)
    mouse(w, "press", (100, 100))
    mouse(w, "move", (110, 100))
    scroll(w)
    time.sleep(0.08)
    QtWidgets.QApplication.processEvents()  # the scroll pause passes while the mouse is still down
    mouse(w, "move", (120, 100))
    mouse(w, "release", (120, 100), buttons=Qt.NoButton)
    assert wait_until(lambda: spy.finished >= 1)
    QtWidgets.QApplication.processEvents()
    assert spy.started == 1 and spy.finished == 1 and spy.balanced()


def test_arrow_keys_held_down_are_one_gesture():
    w = make_wheel()
    focus(w)
    spy = Spy(w)
    for _ in range(5):
        QtWidgets.QApplication.sendEvent(w, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
    assert wait_until(lambda: spy.finished == 1)
    assert spy.started == 1 and len(spy.values) == 5 and spy.balanced()


def test_backspace_does_not_reset():
    w = make_wheel(value=(1.2, 1.0, 1.0, 1.0))
    focus(w)
    spy = Spy(w)
    QtWidgets.QApplication.sendEvent(w, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
    assert spy.values == []


def test_puck_stays_inside_the_wheel():
    w = make_wheel()
    drag(w, 900, 0)
    assert math.hypot(w._px, w._py) <= 1.0 + 1e-9


def test_refresh_during_a_drag_is_ignored():
    w = make_wheel()
    spy = Spy(w)
    mouse(w, "press", (100, 100))
    mouse(w, "move", (130, 100))
    during = spy.values[-1]
    w.set_value(NEUTRAL)  # Unreal's slightly older value arrives
    mouse(w, "move", (131, 100))
    assert spy.values[-1][0] == pytest.approx(during[0], abs=0.02)
    mouse(w, "release", (131, 100), buttons=Qt.NoButton)
    assert w.value() == spy.values[-1]


def test_hiding_mid_drag_finishes_the_gesture():
    w = make_wheel()
    w.show()
    spy = Spy(w)
    mouse(w, "press", (100, 100))
    mouse(w, "move", (120, 100))
    w.hide()
    assert spy.started == 1 and spy.finished == 1 and not w.busy()


def test_out_of_range_values_are_kept_until_touched():
    w = make_wheel(value=(3.0, 1.0, 1.0, 2.5))
    assert w.value() == pytest.approx((3.0, 1.0, 1.0, 2.5))
    focus(w)
    spy = Spy(w)
    scroll(w, -1)
    r, g, b, y = spy.values[-1]
    assert (r, g, b) == pytest.approx((3.0, 1.0, 1.0)) and y == pytest.approx(2.49)


# ---- the wheel group -----------------------------------------------------------------------------
def test_wheel_control_shows_titles_and_fields():
    wc = WheelControl(CONTROLS["lift"])
    assert wc.title.text() == "Lift" and wc.subtitle.text() == "Shadows · Offset"
    assert wc.subtitle.font().italic()
    wc.set_value((0.1, 0.0, -0.1, 0.05))
    assert [f.text() for f in wc.fields] == ["0.100", "0.000", "-0.100", "0.050"]


def test_fields_show_values_outside_the_range():
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((3.0, 1.0, 1.0, 2.5))
    assert [f.text() for f in wc.fields] == ["3.000", "1.000", "1.000", "2.500"]


def test_cycle_button_switches_to_hex():
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((1.0, 0.9, 0.8, 1.0))
    wc.mode_button.click()
    assert wc.mode == "hex"
    assert wc.hex_field.isVisibleTo(wc) and not wc.fields[0].isVisibleTo(wc)
    assert wc.hex_field.text() == tint_hex((1.0, 0.9, 0.8))
    wc.mode_button.click()
    assert wc.mode == "rgb"


def test_typing_hex_moves_the_tint_only():
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((1.0, 1.0, 1.0, 1.3))
    spy = Spy(wc)
    wc.mode_button.click()
    type_into(wc.hex_field, "#FF8080")
    r, g, b, y = spy.values[-1]
    assert y == 1.3 and r > g and g == pytest.approx(b)
    assert (r + g + b) / 3 == pytest.approx(1.0)  # no darker overall: only the tint moved
    assert spy.order == ["start", "chg", "end"]


@pytest.mark.parametrize("bad", ["#GGG", "abc", ""])
def test_bad_hex_changes_nothing(bad):
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((1.0, 0.9, 0.8, 1.0))
    spy = Spy(wc)
    wc.mode_button.click()
    type_into(wc.hex_field, bad)
    assert spy.values == [] and wc.value() == (1.0, 0.9, 0.8, 1.0)
    assert wc.hex_field.text() == tint_hex((1.0, 0.9, 0.8))


def test_leaving_fields_without_typing_changes_nothing():
    wc = WheelControl(CONTROLS["gain"])
    wc.set_value((1.2, 0.9, 0.9, 1.07348))  # a drag towards red, and a value with more decimals
    spy = Spy(wc)
    for f in wc.fields + [wc.hex_field]:
        f.editingFinished.emit()  # Enter, or tabbing away, with nothing typed
    assert spy.order == [] and wc.value() == pytest.approx((1.2, 0.9, 0.9, 1.07348))


def test_typed_number_is_clamped():
    wc = WheelControl(CONTROLS["gamma"])
    spy = Spy(wc)
    type_into(wc.fields[0], "-5")
    assert spy.values[-1][0] == CONTROLS["gamma"].lo
    assert wc.fields[0].text() == "0.000"


def test_typed_text_that_is_not_a_number_changes_nothing():
    wc = WheelControl(CONTROLS["gamma"])
    spy = Spy(wc)
    type_into(wc.fields[1], "abc")
    assert spy.values == [] and wc.fields[1].text() == "1.000"


def test_refresh_does_not_wipe_what_is_being_typed():
    wc = WheelControl(CONTROLS["gain"])
    wc.fields[0].setText("1.55")
    wc.fields[0].setModified(True)
    wc.set_value((1.073, 1.0, 1.0, 1.0))
    assert wc.fields[0].text() == "1.55" and wc.fields[1].text() == "1.000"


def test_brightness_slider_drag_is_one_gesture():
    wc = WheelControl(CONTROLS["gain"])
    spy = Spy(wc)
    wc.brightness.sliderPressed.emit()
    wc.brightness.setValue(wc.brightness.value() + 50)
    wc.brightness.setValue(wc.brightness.value() + 50)
    wc.brightness.sliderReleased.emit()
    assert spy.order == ["start", "chg", "chg", "end"] and spy.values[-1][3] > 1.0


def test_brightness_groove_steps_are_one_gesture():
    wc = WheelControl(CONTROLS["gain"])
    spy = Spy(wc)
    for _ in range(4):  # holding the mouse on the groove, or a held key
        wc.brightness.setValue(wc.brightness.value() + 10)
    assert wait_until(lambda: spy.finished == 1)
    assert spy.started == 1 and len(spy.values) == 4 and spy.balanced()


def test_refresh_during_a_slider_drag_is_ignored():
    wc = WheelControl(CONTROLS["gain"])
    wc.brightness.sliderPressed.emit()
    wc.brightness.setValue(700)
    wc.set_value(NEUTRAL)
    assert wc.brightness.value() == 700 and wc.busy()
    wc.brightness.sliderReleased.emit()
    assert not wc.busy()


def test_set_value_emits_nothing():
    wc = WheelControl(CONTROLS["gain"])
    spy = Spy(wc)
    wc.set_value((1.2, 1.0, 1.0, 1.1))
    assert spy.order == []


def test_slider_ignores_scroll_without_focus():
    wc = WheelControl(CONTROLS["gain"])
    spy = Spy(wc)
    ev = scroll(wc.brightness)
    assert spy.order == [] and not ev.isAccepted()


# ---- Temperature / Tint ------------------------------------------------------------------------
def test_grade_slider_typed_value_clamped_and_reset():
    s = GradeSlider(CONTROLS["temperature"])
    assert s.title.text() == "Temperature" and s.subtitle.font().italic()
    spy = Spy(s)
    type_into(s.field, "99999")
    assert spy.values[-1] == 15000.0
    s.reset()
    assert spy.values[-1] == 6500.0 and spy.order == ["start", "chg", "end"] * 2


def test_grade_slider_drag_is_one_gesture():
    s = GradeSlider(CONTROLS["tint"])
    spy = Spy(s)
    s.slider.sliderPressed.emit()
    s.slider.setValue(s.slider.value() + 100)
    s.slider.sliderReleased.emit()
    assert spy.order == ["start", "chg", "end"] and spy.values[-1] > 0


def test_grade_slider_leaving_the_field_changes_nothing():
    s = GradeSlider(CONTROLS["tint"])
    s.set_value(0.0125)
    spy = Spy(s)
    s.field.editingFinished.emit()
    assert spy.order == [] and s.value() == 0.0125


def test_grade_slider_shows_no_minus_zero():
    s = GradeSlider(CONTROLS["tint"])
    s.set_value(-0.0000001)
    assert s.field.text() == "0.000"


def test_grade_slider_steps_are_one_gesture():
    s = GradeSlider(CONTROLS["temperature"])
    spy = Spy(s)
    for _ in range(3):
        s.slider.setValue(s.slider.value() + 100)
    assert wait_until(lambda: spy.finished == 1)
    assert spy.started == 1 and spy.balanced()


def test_channel_directions_are_a_third_of_a_turn_apart():
    angles = [math.atan2(*reversed(channel_direction(c))) for c in "rgb"]
    diffs = sorted((a - b) % (2 * math.pi) for a, b in zip(angles, angles[1:] + angles[:1]))
    assert diffs == pytest.approx([2 * math.pi / 3] * 3)

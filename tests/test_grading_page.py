"""The Colour Grading page in the window (offscreen Qt), with a stand-in grading service."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from operator_board.qt import QtCore, QtGui, QtWidgets, Qt  # noqa: E402
from operator_board.core.grading import Grade  # noqa: E402
from operator_board.ui import color_wheel  # noqa: E402

CLUSTER = "ndisplay_cluster|Root"
VIEWPORT = "ndisplay_viewport|Root|VP_Left"
CAMERA = "icvfx_all_nodes|Root/ICVFXCamera"
PPV = "ppv|Stage PPV"
CCR_MISSING = "The Colour Correct Regions plugin is not enabled in this project"


def _target(key, name, enabled=True):
    kind = key.split("|")[0]
    return {"key": key, "kind": kind, "actor": key.split("|")[1], "applies_to": [], "nth": 0, "name": name,
            "enabled": enabled}


class _Bypassed:
    def __init__(self, keys):
        self.keys = keys

    def bypassed(self):
        return sorted(self.keys)


class FakeGrading:
    """Stands in for GradingService: four targets (no Colour Correct Regions), grades kept in memory,
    every call recorded."""

    def __init__(self, bus):
        self.bus = bus
        self.calls = []
        self.targets_list = [_target(CLUSTER, "Root · Entire cluster"), _target(VIEWPORT, "Root · VP_Left", False),
                             _target(CAMERA, "ICVFXCamera · All nodes"), _target(PPV, "Stage PPV (global)")]
        self.missing = {"ndisplay": "", "ccr": CCR_MISSING}
        self.current = {t["key"]: Grade.neutral() for t in self.targets_list}
        self.current[PPV] = Grade.neutral().set("global.gain", (1.2, 1.0, 0.8, 1.1)).set("temp", 5600.0)
        self.enabled = {t["key"]: t["enabled"] for t in self.targets_list}
        self._bypassed = set()
        self.bypass = _Bypassed(self._bypassed)
        self._selected = ""
        self.active_look = ""
        self.read_delay = {}   # key -> seconds a read of it takes
        self.record = None     # the change log's record (set by the fixture), as the service logs

    # targets and selection
    @property
    def selected(self):
        return self._selected or self.targets_list[0]["key"]

    def select(self, key):
        self.calls.append(("select", key))
        self._selected = key
        self.bus.publish("grading.selected", key=key)

    def refresh_targets(self):
        self.calls.append(("refresh",))
        self.bus.publish("grading.targets", targets=self.targets_list, missing=self.missing)
        return self.targets_list

    def read(self, key):
        time.sleep(self.read_delay.get(key, 0))
        self.calls.append(("read", key))
        if key not in self.current:
            from operator_board.remote.grading import GradingProblem
            raise GradingProblem("Target no longer in the level: " + key)
        return self.current[key]

    # gestures
    def begin_gesture(self, key):
        self.calls.append(("begin", key))
        time.sleep(0.05)  # a read from Unreal takes a moment: changes must still arrive after it
        return self.current[key]

    def send(self, key, values):
        self.calls.append(("send", key, dict(values)))
        grade = self.current[key]
        for k, v in values.items():
            grade = grade.reset(k) if v is None else grade.set(k, v)
        self.current[key] = grade

    def end_gesture(self, key, label):
        self.calls.append(("end", key, label))
        self.bus.publish("grading.changed", key=key, grade=self.current[key])

    # one-shot changes
    def reset(self, key="", control_id=None):
        self.calls.append(("reset", key, control_id))
        return True, "Reset grade"

    def apply(self, key, grade_dict):
        self.calls.append(("apply", key, grade_dict))
        if self.record:
            self.record("Colour Grading", key, "Set grade", value=grade_dict, previous={"was": "before set"},
                        kind="grade", ref={"key": key})

    def is_bypassed(self, key):
        return key in self._bypassed

    def set_bypass(self, key="", on=None):
        key = key or self.selected
        on = (key not in self._bypassed) if on is None else on
        self.calls.append(("bypass", key, on))
        (self._bypassed.add if on else self._bypassed.discard)(key)
        self.bus.publish("grading.bypass", key=key, on=on)
        return on

    def enable(self, key=""):
        self.calls.append(("enable", key))
        self.enabled[key] = True
        self.bus.publish("grading.bypass", key=key, on=False)
        return True, "Grading enabled"

    def restore_bypassed(self):
        self.calls.append(("restore",))
        for key in list(self._bypassed):
            self.set_bypass(key, False)
        return True, "Restored"

    # looks
    def save_look(self, name, keys):
        self.calls.append(("save_look", name, list(keys)))

    def recall_look(self, name):
        self.calls.append(("recall_look", name))
        self.active_look = name
        self.bus.publish("grading.look", name=name)
        return []

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def quick_idle(monkeypatch):
    monkeypatch.setattr(color_wheel, "IDLE_MS", 30)


@pytest.fixture
def opened():
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig, LEDProcessorConfig
    from operator_board.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ctx = AppContext(AppConfig(led_processors=[LEDProcessorConfig(name="Sim", profile_id="simulator")]))
    ctx.grading.stop()
    fake = FakeGrading(ctx.bus)
    fake.record = ctx.changes.record
    ctx.grading = fake
    window = MainWindow(ctx)
    window.resize(1500, 950)
    window.show()
    window.show_page("Colour Grading")
    page = window.current_page()
    wait_until(lambda: page.target_picker.count() > 0)
    yield app, ctx, window, page, fake
    window.skip_save_on_close = True
    window.close()
    app.processEvents()
    ctx.shutdown()
    for w in QtWidgets.QApplication.topLevelWidgets():
        w.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def wait_until(cond, timeout=3.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.005)
    return cond()


def mouse(widget, kind, pos, mods=Qt.NoModifier):
    types = {"press": QtCore.QEvent.MouseButtonPress, "move": QtCore.QEvent.MouseMove,
             "release": QtCore.QEvent.MouseButtonRelease, "double": QtCore.QEvent.MouseButtonDblClick}
    p = QtCore.QPointF(*pos)
    button = Qt.NoButton if kind == "move" else Qt.LeftButton
    buttons = Qt.NoButton if kind == "release" else Qt.LeftButton
    ev = QtGui.QMouseEvent(types[kind], p, QtCore.QPointF(widget.mapToGlobal(p.toPoint())), button, buttons, mods)
    QtWidgets.QApplication.sendEvent(widget, ev)


def pick(page, key):
    index = page.target_picker.findData(key)
    assert index >= 0
    page.target_picker.setCurrentIndex(index)


def calls(fake, *names):
    return [c for c in fake.calls if c[0] in names]


# ---- place in the window ---------------------------------------------------------------------------
def test_page_is_in_the_side_menu_straight_after_remote_objects(opened):
    from operator_board.ui.main_window import NAV
    _app, _ctx, window, page, _fake = opened
    advanced = dict(g for g in NAV if isinstance(g, tuple))["Advanced"]
    assert advanced[advanced.index("Remote Objects") + 1] == "Colour Grading"
    assert page.title == "Colour Grading" and type(page).__name__ == "GradingPage"


# ---- targets ---------------------------------------------------------------------------------------
def test_target_picker_groups_targets_in_priority_order_and_explains_empty_kinds(opened):
    _app, _ctx, _window, page, _fake = opened
    picker = page.target_picker
    texts = [picker.itemText(i) for i in range(picker.count())]
    headers = ["nDisplay", "ICVFX camera", "Post Process Volumes", "Colour Correct Regions"]
    assert [t for t in texts if t in headers] == headers                    # in Live FX order
    assert texts.index("Root · Entire cluster") < texts.index("ICVFXCamera · All nodes") \
        < texts.index("Stage PPV (global)")
    ccr = texts.index("Colour Correct Regions")
    assert CCR_MISSING in texts[ccr + 1]                                     # the plain explanation
    model = picker.model()
    for i, text in enumerate(texts):
        selectable = bool(model.item(i).flags() & Qt.ItemIsEnabled)
        assert selectable == (picker.itemData(i) is not None), text           # only targets can be picked


def test_selecting_a_target_selects_it_everywhere_and_fills_the_controls(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    assert wait_until(lambda: ("select", PPV) in fake.calls and page.gain.value() == (1.2, 1.0, 0.8, 1.1))
    assert ("read", PPV) in fake.calls
    assert page.temperature.value() == 5600.0
    assert page.gain.isEnabled() and page.enable_button.isHidden()


def test_a_target_with_grading_switched_off_is_greyed_with_one_enable_button(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, VIEWPORT)
    assert wait_until(lambda: not page.gain.isEnabled())
    assert not page.temperature.isEnabled() and not page.shadows_gain.isEnabled()
    assert not page.enable_button.isHidden() and page.enable_button.text() == "Enable grading on this target"
    page.enable_button.click()
    assert wait_until(lambda: ("enable", VIEWPORT) in fake.calls and page.gain.isEnabled())
    assert page.enable_button.isHidden()


def test_a_target_no_longer_in_the_level_is_named_and_greyed(opened):
    _app, _ctx, _window, page, fake = opened
    fake._selected = "ppv|Deleted PPV"
    fake.bus.publish("grading.selected", key="ppv|Deleted PPV")
    assert wait_until(lambda: "no longer in the level" in page.target_note.text())
    assert not page.gain.isEnabled() and page.enable_button.isHidden()


# ---- live sending ----------------------------------------------------------------------------------
def test_a_wheel_drag_begins_sends_and_ends_one_gesture_in_order(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    assert wait_until(lambda: page.gain.value() == (1.2, 1.0, 0.8, 1.1))
    wheel = page.gain.wheel
    centre = (wheel.width() / 2, wheel.height() / 2)
    mouse(wheel, "press", centre)
    for step in range(1, 6):
        mouse(wheel, "move", (centre[0] + step * 4, centre[1] - step * 2))
        QtWidgets.QApplication.processEvents()
    mouse(wheel, "release", (centre[0] + 20, centre[1] - 10))
    assert wait_until(lambda: calls(fake, "end"))
    gesture = calls(fake, "begin", "send", "end")
    assert gesture[0] == ("begin", PPV) and gesture[-1][:2] == ("end", PPV)
    sends = gesture[1:-1]
    assert len(sends) >= 2 and all(c[0] == "send" and c[1] == PPV for c in sends)
    assert all(set(c[2]) == {"global.gain"} and len(c[2]["global.gain"]) == 4 for c in sends)
    assert "Gain" in gesture[-1][2]


def test_a_slider_change_sends_white_balance(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    assert wait_until(lambda: page.temperature.value() == 5600.0)
    page.temperature.field.setText("6000")
    page.temperature.field.setModified(True)  # as when typed
    page.temperature.field.editingFinished.emit()
    assert wait_until(lambda: calls(fake, "end"))
    assert ("send", PPV, {"temp": 6000.0}) in fake.calls


def test_double_click_resets_the_wheel_and_unticks_its_override(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    assert wait_until(lambda: page.gain.value() == (1.2, 1.0, 0.8, 1.1))
    wheel = page.gain.wheel
    mouse(wheel, "double", (wheel.width() / 2, wheel.height() / 2))
    assert wait_until(lambda: calls(fake, "end"))
    assert ("send", PPV, {"global.gain": None}) in fake.calls


def test_reset_button_resets_the_whole_grade_of_the_target(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    wait_until(lambda: ("select", PPV) in fake.calls)
    page.reset_button.click()
    assert wait_until(lambda: ("reset", PPV, None) in fake.calls)


def test_undo_puts_back_the_last_grade_change(opened):
    _app, ctx, _window, page, fake = opened
    before = Grade.neutral().to_dict()
    ctx.changes.record("Colour Grading", "Stage PPV (global)", "Gain", value={"x": 1}, previous=before,
                       kind="grade", ref={"key": PPV})
    page.refresh()
    assert page.undo_button.isEnabled()
    page.undo_button.click()
    assert wait_until(lambda: ("apply", PPV, before) in fake.calls)


# ---- bypass, problems ------------------------------------------------------------------------------
def test_bypass_lights_and_marks_the_target(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    assert wait_until(lambda: page.gain.isEnabled())  # its grade has been read
    page.bypass_button.click()
    assert wait_until(lambda: "bypassed" in page.target_picker.itemText(page.target_picker.findData(PPV)))
    assert ("bypass", PPV, True) in fake.calls and page.bypass_button.isChecked()
    assert not page.bypass_note.isHidden() and "Stage PPV" in page.bypass_note.text()
    assert page.gain.isEnabled()  # touching a control ends the bypass, so the controls stay usable
    page.restore_button.click()
    assert wait_until(lambda: not page.bypass_button.isChecked())
    assert page.bypass_note.isHidden()


def test_a_grading_problem_shows_a_red_banner_until_cleared(opened):
    _app, ctx, _window, page, _fake = opened
    ctx.bus.publish("grading.problem", message="render-2:30010 did not get the grade change")
    assert wait_until(lambda: not page.problem_banner.isHidden())
    assert "render-2" in page.problem_banner.text()
    from operator_board.ui.theme import COLORS
    assert COLORS["err"] in page.problem_banner.styleSheet()
    ctx.bus.publish("grading.problem", message="")
    assert wait_until(lambda: page.problem_banner.isHidden())


# ---- looks -----------------------------------------------------------------------------------------
def test_save_look_dialog_ticks_every_target_and_saves_the_ticked_ones(opened):
    from operator_board.ui.pages.grading import LookDialog
    _app, _ctx, _window, page, fake = opened
    dialog = LookDialog(fake.targets_list, page)
    assert dialog.target_list.count() == 4
    assert all(dialog.target_list.item(i).checkState() == Qt.Checked for i in range(4))
    dialog.name_edit.setText("Day ext warm")
    dialog.target_list.item(1).setCheckState(Qt.Unchecked)
    assert dialog.chosen() == [CLUSTER, CAMERA, PPV]
    page._save_look("Day ext warm", dialog.chosen())
    assert wait_until(lambda: ("save_look", "Day ext warm", [CLUSTER, CAMERA, PPV]) in fake.calls)


def test_picking_a_look_recalls_it(opened):
    from operator_board.core.looks import Look
    _app, ctx, _window, page, fake = opened
    ctx.looks.save(Look("Night", {}))
    assert wait_until(lambda: page.looks_picker.findText("Night") >= 0)
    page.looks_picker.activated.emit(page.looks_picker.findText("Night"))
    assert wait_until(lambda: ("recall_look", "Night") in fake.calls)
    assert wait_until(lambda: page.looks_picker.currentText() == "Night")
    page._delete_look("Night", confirm=False)
    assert ctx.looks.names() == []


# ---- refreshing ------------------------------------------------------------------------------------
def test_page_re_reads_every_three_seconds_only_while_live(opened):
    _app, _ctx, window, page, fake = opened
    assert page.reread_timer.interval() == 3000
    pick(page, PPV)
    wait_until(lambda: ("read", PPV) in fake.calls)
    fake.calls.clear()
    window.show_page("Operate")
    QtWidgets.QApplication.processEvents()
    assert not page.is_live()
    page._reread()
    QtWidgets.QApplication.processEvents()
    assert calls(fake, "read") == []
    window.show_page("Colour Grading")
    fake.calls.clear()
    page._reread()
    assert wait_until(lambda: ("read", PPV) in fake.calls)


def test_page_fits_a_laptop_wide_window_without_scrolling_sideways(opened):
    _app, _ctx, window, page, _fake = opened
    window.resize(1366, 768)
    QtWidgets.QApplication.processEvents()
    area = page.parentWidget().parentWidget()  # the scroll area every page sits in
    assert page.minimumSizeHint().width() <= area.viewport().width()


def test_greyed_controls_look_greyed(opened):
    _app, _ctx, _window, page, _fake = opened
    pick(page, VIEWPORT)
    assert wait_until(lambda: not page.gain.isEnabled())
    for widget in (page.gain, page.temperature, page.shadows_gain):
        effect = widget.graphicsEffect()
        assert effect is not None and effect.isEnabled() and effect.opacity() < 0.5  # the wheel paints dim
    pick(page, PPV)
    assert wait_until(lambda: page.gain.isEnabled())
    assert page.gain.graphicsEffect() is None or not page.gain.graphicsEffect().isEnabled()


# ---- found in review ----------------------------------------------------------------------------------
def test_picking_a_target_while_a_slow_read_runs_reads_it_and_greys_until_then(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    assert wait_until(lambda: page.gain.value() == (1.2, 1.0, 0.8, 1.1))
    fake.read_delay[PPV] = 0.4
    page._reread()                                   # a slow timer read of the PPV is running
    pick(page, CAMERA)
    QtWidgets.QApplication.processEvents()
    assert not page.gain.isEnabled()                 # the PPV's values are still shown: not usable
    assert wait_until(lambda: ("read", CAMERA) in fake.calls)
    assert wait_until(lambda: page.gain.isEnabled() and page.gain.value() == (1.0, 1.0, 1.0, 1.0))


def test_a_re_read_waits_for_the_end_of_a_gesture(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    assert wait_until(lambda: page.gain.isEnabled() and ("read", PPV) in fake.calls)
    time.sleep(0.2)
    QtWidgets.QApplication.processEvents()          # every earlier read has finished
    fake.calls.clear()
    page.gain.gestureStarted.emit()
    page.gain.changed.emit((1.3, 1.0, 0.8, 1.1))
    page.gain.gestureFinished.emit()
    page._reread()                                   # the timer fires while end_gesture is still queued
    assert wait_until(lambda: calls(fake, "end") and calls(fake, "read"))
    names = [c[0] for c in fake.calls if c[0] in ("begin", "send", "end", "read")]
    assert names.index("read") > names.index("end")


def test_overlapping_gestures_on_one_target_are_one_gesture(opened):
    _app, _ctx, _window, page, fake = opened
    pick(page, PPV)
    assert wait_until(lambda: page.gain.isEnabled())
    fake.calls.clear()
    page.gain.gestureStarted.emit()                  # a scroll on Gain still open...
    page.gain.changed.emit((1.3, 1.0, 0.8, 1.1))
    page.lift.gestureStarted.emit()                  # ...when Lift is dragged
    page.lift.changed.emit((0.0, 0.0, 0.05, 0.0))
    page.gain.gestureFinished.emit()
    page.lift.changed.emit((0.0, 0.0, 0.06, 0.0))
    page.lift.gestureFinished.emit()
    assert wait_until(lambda: calls(fake, "end"))
    gesture = calls(fake, "begin", "send", "end")
    assert [c[0] for c in gesture].count("begin") == 1 and [c[0] for c in gesture].count("end") == 1
    assert gesture[0][0] == "begin" and gesture[-1][0] == "end"
    assert "Gain" in gesture[-1][2] and "Lift" in gesture[-1][2]


def test_bypass_is_not_offered_on_a_target_with_grading_switched_off(opened):
    _app, _ctx, _window, page, _fake = opened
    pick(page, VIEWPORT)
    assert wait_until(lambda: not page.enable_button.isHidden())
    assert not page.bypass_button.isEnabled()


def test_a_look_can_be_deleted_without_recalling_it(opened):
    from operator_board.core.looks import Look
    _app, ctx, _window, page, fake = opened
    ctx.looks.save(Look("Night", {}))
    ctx.looks.save(Look("Day", {}))
    assert wait_until(lambda: page.looks_picker.count() == 2)
    menu = page.delete_look_button.menu()
    assert menu is not None and sorted(a.text() for a in menu.actions()) == ["Day", "Night"]
    page._delete_look("Night", confirm=False)
    assert ctx.looks.names() == ["Day"] and calls(fake, "recall_look") == []


def test_undo_walks_back_and_is_shared_by_every_copy_of_the_page(opened):
    from operator_board.ui.pages.grading import GradingPage
    _app, ctx, window, page, fake = opened
    g0, g1 = {"step": 0}, {"step": 1}
    ctx.changes.record("Colour Grading", "PPV", "Gain", value=g1, previous=g0, kind="grade", ref={"key": PPV})
    ctx.changes.record("Colour Grading", "PPV", "Lift", value={"step": 2}, previous=g1, kind="grade",
                       ref={"key": PPV})
    page.refresh()
    page.undo_button.click()
    assert not page.undo_button.isEnabled()          # no double undo while it runs
    assert wait_until(lambda: ("apply", PPV, g1) in fake.calls and page.undo_button.isEnabled())
    other = GradingPage(ctx, window)                 # as a custom page's copy of this page
    other.refresh()
    other.undo_button.click()
    assert wait_until(lambda: ("apply", PPV, g0) in fake.calls)
    assert [c for c in calls(fake, "apply")] == [("apply", PPV, g1), ("apply", PPV, g0)]
    other.deleteLater()

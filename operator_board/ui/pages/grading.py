"""Colour Grading: grade the live Unreal image in the style of a colourist's panel (Assimilate Live FX).

Targets come in Live FX's order: the nDisplay image (whole cluster, then each viewport entry), the
ICVFX camera's inner frustum, Post Process Volumes, then Colour Correct Regions. The selected target is
shared app-wide (``GradingService.selected``), so the Stream Deck nudges grade what this page shows.

Every wheel and slider follows the widgets' gesture contract (``ui/color_wheel.py``): a gesture's
start, changes and end go to the grading service in that order on one worker thread, so a change
never overtakes the read that starts the gesture, and the UI never waits for Unreal. A control set
back to exactly neutral (double-click, Delete) is sent as a reset, which also unticks its override.

The page re-reads the selected target when it is picked, after every gesture and every 3 seconds
while it is on screen (``is_live``), so changes made in Unreal show up here.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from operator_board.core.grading import ADVANCED, NEUTRAL, NEUTRAL_TEMP, NEUTRAL_TINT, PRIMARIES, RANGES, Control
from operator_board.qt import QtCore, QtWidgets, Qt, exec_
from operator_board.ui.color_wheel import GradeSlider, WheelControl
from operator_board.ui.pages.base import BasePage
from operator_board.ui.theme import COLORS
from operator_board.ui.widgets import Card, muted

log = logging.getLogger(__name__)

# (heading, target kinds, key in the service's ``missing``) in Live FX's priority order.
GROUPS = [
    ("nDisplay", ("ndisplay_cluster", "ndisplay_viewport"), "ndisplay"),
    ("ICVFX camera", ("icvfx_all_nodes", "icvfx_node"), "icvfx"),
    ("Post Process Volumes", ("ppv",), "ppv"),
    ("Colour Correct Regions", ("ccr",), "ccr"),
]
EMPTY = {
    "ndisplay": "No nDisplay root actor in the open level",
    "icvfx": "No ICVFX camera on the nDisplay root actor",
    "ppv": "No Post Process Volumes in the open level",
    "ccr": "No Colour Correct Regions in the open level",
}
GONE = "no longer in the level"
REREAD_MS = 3000
DIMMED = 0.35  # opacity of controls that cannot be used


def _attr(control: Control) -> str:
    return control.id.replace(".", "_")


def _label(control: Control) -> str:
    """How a change to this control is named in the change log."""
    if control in PRIMARIES:
        return control.title
    return f"{control.range.capitalize()} {control.part}"


def _is_neutral(control: Control, value: Any) -> bool:
    if control.kind == "slider":
        return float(value) == (NEUTRAL_TEMP if control.part == "temp" else NEUTRAL_TINT)
    return tuple(float(v) for v in value) == tuple(float(v) for v in NEUTRAL[control.part])


def _target_name(key: str) -> str:
    parts = key.split("|")
    return parts[1] if len(parts) > 1 else key


class _InOrder:
    """Runs grading calls one after another on a worker thread, in the order they were asked for."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[Callable[[], Any]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    def put(self, fn: Callable[[], Any]) -> None:
        self._queue.put(fn)
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, daemon=True, name="grading-page")
            self._thread.start()

    def _work(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            except Exception:  # noqa: BLE001 - the service publishes problems; anything else is logged
                log.exception("Colour grading change failed")


class _Columns(QtWidgets.QWidget):
    """Lays controls out in as many columns as fit the width (at most ``most``), wrapping the rest
    onto the next rows. A control a custom page has taken is left where it is."""

    def __init__(self, widgets: List[QtWidgets.QWidget], most: int, spacing: int = 20) -> None:
        super().__init__()
        self.setObjectName("PlainRow")
        self._widgets = list(widgets)
        self._most = most
        self._cols = 0
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(spacing)
        self._grid.setVerticalSpacing(spacing)
        self._place(most)

    def _mine(self) -> List[QtWidgets.QWidget]:
        return [w for w in self._widgets if w.parentWidget() is None or w.parentWidget() is self]

    def _widest(self) -> int:
        return max([w.minimumSizeHint().width() for w in self._mine()] or [0])

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self._widest(), super().minimumSizeHint().height())

    def _fit(self, width: int) -> int:
        spacing = self._grid.horizontalSpacing()
        return max(1, min(self._most, (width + spacing) // max(1, self._widest() + spacing)))

    def _place(self, cols: int) -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        for col in range(max(self._cols, cols, self._most)):
            self._grid.setColumnStretch(col, 1 if col < cols else 0)
        for i, widget in enumerate(self._mine()):
            self._grid.addWidget(widget, i // cols, i % cols, Qt.AlignTop)
        self._cols = cols

    def resizeEvent(self, event) -> None:
        cols = self._fit(event.size().width())
        if cols != self._cols:
            self._place(cols)
        super().resizeEvent(event)


class LookDialog(QtWidgets.QDialog):
    """Name a look and choose which targets it holds (all ticked to start with)."""

    def __init__(self, targets: List[Dict[str, Any]], parent=None, name: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Save look")
        self.setMinimumWidth(420)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Look name"))
        self.name_edit = QtWidgets.QLineEdit(name)
        self.name_edit.setPlaceholderText("For example: Day ext warm")
        lay.addWidget(self.name_edit)
        lay.addWidget(QtWidgets.QLabel("Targets to keep in this look"))
        self.target_list = QtWidgets.QListWidget()
        for t in targets:
            item = QtWidgets.QListWidgetItem(t.get("name") or t["key"])
            item.setData(Qt.UserRole, t["key"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.target_list.addItem(item)
        lay.addWidget(self.target_list)
        lay.addWidget(muted("Untick a target to leave its grade out. Recalling the look sets every target it keeps."))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.save_button = buttons.addButton("Save look", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.name_edit.textChanged.connect(self._check)
        self.target_list.itemChanged.connect(self._check)
        self._check()

    def chosen(self) -> List[str]:
        return [self.target_list.item(i).data(Qt.UserRole) for i in range(self.target_list.count())
                if self.target_list.item(i).checkState() == Qt.Checked]

    def _check(self, *_args) -> None:
        self.save_button.setEnabled(bool(self.name_edit.text().strip()) and bool(self.chosen()))


class GradingPage(BasePage):
    title = "Colour Grading"
    control_skip = ("target_note", "bypass_note", "problem_banner", "tabs")

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        self._in_order = _InOrder()
        self._gesture_keys: Dict[int, str] = {}
        self._reading = False
        self._shown_key = ""
        self._filling = False
        self._undone = set()
        self.control_labels = self._labels()

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        self.problem_banner = QtWidgets.QLabel()
        self.problem_banner.setWordWrap(True)
        self.problem_banner.setToolTip("Click to dismiss")
        self.problem_banner.setStyleSheet(f"background: {COLORS['err']}; color: white; font-weight: 600;"
                                          " padding: 8px 12px; border-radius: 6px;")
        self.problem_banner.setCursor(Qt.PointingHandCursor)
        self.problem_banner.mousePressEvent = lambda _e: self.problem_banner.hide()
        self.problem_banner.hide()
        lay.addWidget(self.problem_banner)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self._target_card(), 3)
        top.addWidget(self._looks_card(), 2)
        lay.addLayout(top)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._primaries_tab(), "Primaries")
        self.tabs.addTab(self._advanced_tab(), "Advanced: tonal ranges")
        lay.addWidget(self.tabs, 1)

        self.reread_timer = QtCore.QTimer(self)
        self.reread_timer.setInterval(REREAD_MS)
        self.reread_timer.timeout.connect(self._reread)
        self.reread_timer.start()
        self._update_state()

    # ---- building --------------------------------------------------------------------------------
    @staticmethod
    def _labels() -> Dict[str, str]:
        labels = {_attr(c): f"{c.title} ({c.kind})" for c in PRIMARIES}
        labels.update({_attr(c): f"{c.range.capitalize()} {c.part} (wheel)" for c in ADVANCED})
        labels.update({"target_picker": "Grading target (drop-down)", "looks_picker": "Looks (drop-down)",
                       "bypass_button": "Bypass grading", "reset_button": "Reset grade",
                       "undo_button": "Undo grading change", "refresh_button": "Reload grading targets",
                       "restore_button": "Restore bypassed grading", "delete_look_button": "Delete look"})
        return labels

    def _target_card(self) -> Card:
        card = Card("Target")
        self.target_picker = QtWidgets.QComboBox()
        self.target_picker.setAccessibleName("Grading target")
        self.target_picker.setMinimumWidth(260)
        self.target_picker.currentIndexChanged.connect(self._target_picked)
        self.refresh_button = QtWidgets.QPushButton("↻")
        self.refresh_button.setToolTip("Read the grading targets in the open level again")
        self.refresh_button.setAccessibleName("Reload grading targets")
        self.refresh_button.setFixedWidth(40)
        self.refresh_button.clicked.connect(lambda: self._load_targets(quiet=False))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.target_picker, 1)
        row.addWidget(self.refresh_button)
        card.add_layout(row)

        self.bypass_button = QtWidgets.QPushButton("Bypass")
        self.bypass_button.setCheckable(True)
        self.bypass_button.setToolTip("Switch this target's grading off to compare with the ungraded image; "
                                      "click again to bring it back unchanged")
        self.bypass_button.setStyleSheet(f"QPushButton:checked {{ background: {COLORS['warn']}; color: black;"
                                         " font-weight: 700; }")
        self.bypass_button.clicked.connect(self._bypass_clicked)
        self.reset_button = QtWidgets.QPushButton("Reset grade")
        self.reset_button.setToolTip("Set every grading control on this target back to neutral")
        self.reset_button.clicked.connect(self._reset)
        self.undo_button = QtWidgets.QPushButton("↶ Undo")
        self.undo_button.setToolTip("Put back the grade before the last change")
        self.undo_button.clicked.connect(self._undo)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.bypass_button)
        row.addWidget(self.reset_button)
        row.addWidget(self.undo_button)
        row.addStretch(1)
        card.add_layout(row)

        self.target_note = muted("")
        self.target_note.setWordWrap(True)
        card.add(self.target_note)
        self.enable_button = QtWidgets.QPushButton("Enable grading on this target")
        self.enable_button.setToolTip("Switch on this target's colour grading in Unreal")
        self.enable_button.clicked.connect(self._enable)
        card.add(self.enable_button)
        self.bypass_note = QtWidgets.QLabel()
        self.bypass_note.setWordWrap(True)
        self.bypass_note.setStyleSheet(f"color: {COLORS['warn']};")
        self.restore_button = QtWidgets.QPushButton("Restore all")
        self.restore_button.setToolTip("End every bypass and bring each target's grade back")
        self.restore_button.clicked.connect(self._restore)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.bypass_note, 1)
        row.addWidget(self.restore_button, 0, Qt.AlignTop)
        card.add_layout(row)
        card.layout_.addStretch(1)
        return card

    def _looks_card(self) -> Card:
        card = Card("Looks")
        self.looks_picker = QtWidgets.QComboBox()
        self.looks_picker.setAccessibleName("Looks")
        self.looks_picker.setPlaceholderText("Unsaved grade")
        self.looks_picker.setToolTip("Pick a look to set every target it keeps")
        self.looks_picker.activated.connect(self._look_picked)
        card.add(self.looks_picker)
        self.save_look_button = QtWidgets.QPushButton("Save look…")
        self.save_look_button.setToolTip("Keep the grade of every target under a name, in this project")
        self.save_look_button.clicked.connect(self._ask_save_look)
        self.delete_look_button = QtWidgets.QPushButton("Delete")
        self.delete_look_button.setToolTip("Delete the look shown above (the grade on the wall stays)")
        self.delete_look_button.clicked.connect(lambda: self._delete_look())
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.save_look_button)
        row.addWidget(self.delete_look_button)
        row.addStretch(1)
        card.add_layout(row)
        card.add(muted("Looks can also be recalled from a Stream Deck key or a saved settings preset."))
        card.layout_.addStretch(1)
        return card

    def _make(self, control: Control) -> QtWidgets.QWidget:
        widget = GradeSlider(control) if control.kind == "slider" else WheelControl(control)
        setattr(self, _attr(control), widget)
        widget.gestureStarted.connect(lambda w=widget: self._gesture_started(w))
        widget.changed.connect(lambda value, w=widget: self._gesture_changed(w, value))
        widget.gestureFinished.connect(lambda w=widget: self._gesture_finished(w))
        return widget

    def _primaries_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(tab)
        lay.setContentsMargins(0, 8, 0, 0)
        card = Card("Primaries")
        controls = {c.id: c for c in PRIMARIES}
        wheels = [self._make(controls[cid]) for cid in ("lift", "gamma", "gain", "offset", "saturation", "contrast")]
        white = QtWidgets.QWidget()
        white.setObjectName("PlainRow")
        white_lay = QtWidgets.QVBoxLayout(white)
        white_lay.setContentsMargins(0, 0, 0, 0)
        white_lay.setSpacing(18)
        white_lay.addWidget(self._make(controls["temperature"]))
        white_lay.addWidget(self._make(controls["tint"]))
        white_lay.addStretch(1)
        card.add(_Columns(wheels + [white], 4))
        lay.addWidget(card)
        lay.addStretch(1)
        return tab

    def _advanced_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(tab)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)
        for rng in RANGES[1:]:
            card = Card(rng.capitalize())
            card.add(_Columns([self._make(c) for c in ADVANCED if c.range == rng], 5))
            lay.addWidget(card)
        self._limits = muted("")
        self._limits.setWordWrap(True)
        lay.addWidget(self._limits)
        lay.addStretch(1)
        return tab

    def _grade_widgets(self) -> List[QtWidgets.QWidget]:
        return [getattr(self, _attr(c)) for c in PRIMARIES + ADVANCED]

    # ---- the selected target ------------------------------------------------------------------------
    def _target(self, key: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self.ctx.grading.targets_list if t["key"] == key), None)

    def _usable_key(self) -> str:
        """The selected target, if it is in the level and its grading can be changed."""
        key = self.ctx.grading.selected
        return key if key and self._target(key) is not None else ""

    def _fill_targets(self) -> None:
        grading = self.ctx.grading
        selected = grading.selected
        model = self.target_picker.model()
        self._filling = True
        try:
            self.target_picker.clear()
            bold = self.target_picker.font()
            bold.setBold(True)
            for heading, kinds, missing_key in GROUPS:
                self.target_picker.addItem(heading)
                header = model.item(self.target_picker.count() - 1)
                header.setFlags(Qt.NoItemFlags)
                header.setFont(bold)
                found = [t for t in grading.targets_list if t.get("kind") in kinds]
                for t in found:
                    name = t.get("name") or t["key"]
                    self.target_picker.addItem(name + (" (bypassed)" if grading.is_bypassed(t["key"]) else ""),
                                               t["key"])
                if not found:
                    reason = (getattr(grading, "missing", {}) or {}).get(missing_key) or EMPTY[missing_key]
                    self.target_picker.addItem(f"    {reason}")
                    model.item(self.target_picker.count() - 1).setFlags(Qt.NoItemFlags)
            if selected and self._target(selected) is None and grading.targets_list:
                self.target_picker.addItem(f"{_target_name(selected)} ({GONE})", selected)
                model.item(self.target_picker.count() - 1).setFlags(Qt.NoItemFlags)
            self.target_picker.setCurrentIndex(self.target_picker.findData(selected) if selected else -1)
        finally:
            self._filling = False

    def _target_picked(self, index: int) -> None:
        if self._filling or index < 0:
            return
        key = self.target_picker.itemData(index)
        if key and key != self.ctx.grading.selected:
            self.ctx.grading.select(key)
        elif key:
            self._read(key)

    def _update_state(self) -> None:
        grading = self.ctx.grading
        key = grading.selected
        target = self._target(key) if key else None
        bypassed = bool(target) and grading.is_bypassed(key)
        enabled = bool(target) and bool(getattr(grading, "enabled", {}).get(key, target.get("enabled", True)))
        usable = bool(target) and (enabled or bypassed)
        for widget in self._grade_widgets():
            widget.setEnabled(usable)
            self._dim(widget, not usable)
        for button in (self.bypass_button, self.reset_button):
            button.setEnabled(bool(target))
        self.bypass_button.setChecked(bypassed)
        self.enable_button.setVisible(bool(target) and not enabled and not bypassed)
        if not grading.targets_list:
            note = "Nothing to grade yet. Open a level in Unreal, then press ↻ to read its grading targets."
        elif target is None:
            note = f"{_target_name(key)} is {GONE}. Pick another target." if key else "Pick a target to grade."
        elif not enabled and not bypassed:
            note = "Colour grading is switched off on this target in Unreal, so the controls are greyed."
        else:
            note = ""
        self.target_note.setText(note)
        self.target_note.setVisible(bool(note))
        names = [(self._target(k) or {}).get("name") or _target_name(k) for k in grading.bypass.bypassed()]
        self.bypass_note.setText(("Bypassed, so the wall shows these ungraded: " + ", ".join(names)) if names else "")
        self.bypass_note.setVisible(bool(names))
        self.restore_button.setVisible(bool(names))
        self._update_undo()

    @staticmethod
    def _dim(widget: QtWidgets.QWidget, dim: bool) -> None:
        """Greyed controls are painted faint (the wheel itself paints the same either way)."""
        effect = widget.graphicsEffect()
        if effect is None:
            if not dim:
                return
            effect = QtWidgets.QGraphicsOpacityEffect(widget)
            effect.setOpacity(DIMMED)
            widget.setGraphicsEffect(effect)
        effect.setEnabled(dim)

    def _show_grade(self, grade) -> None:
        for control in PRIMARIES + ADVANCED:
            getattr(self, _attr(control)).set_value(grade.get(control.key))
        limits = getattr(grade, "limits", {}) or {}
        parts = [f"{name} {limits[k]:.2f}" for k, name in (("shadows_max", "shadows end at"),
                                                            ("highlights_min", "highlights start at"),
                                                            ("highlights_max", "highlights end at")) if k in limits]
        self._limits.setText(("Tonal range limits on this target: " + ", ".join(parts) + ".") if parts else "")

    def _read(self, key: str, quiet: bool = True) -> None:
        if self._reading:
            return
        self._reading = True

        def done(grade) -> None:
            self._reading = False
            if key == self.ctx.grading.selected:
                self._shown_key = key
                self._show_grade(grade)
                self._update_state()

        def failed(exc) -> None:
            self._reading = False
            self._update_state()
        self.background(lambda: self.ctx.grading.read(key), done, "Reading the grade", quiet, failed)

    def _reread(self) -> None:
        """Every 3 seconds: show changes made in Unreal, unless a control is being moved."""
        if not self.is_live() or self._reading or any(w.busy() for w in self._grade_widgets()):
            return
        key = self._usable_key()
        if key:
            self._read(key)

    def _load_targets(self, quiet: bool = True) -> None:
        self.background(self.ctx.grading.refresh_targets, None, "Reading the grading targets", quiet)

    def refresh(self) -> None:
        self._fill_targets()
        self._fill_looks()
        self._update_state()
        if not self.ctx.grading.targets_list:
            self._load_targets()
            return
        key = self._usable_key()
        if key and key != self._shown_key:
            self._read(key)

    def on_event(self, topic: str, payload: Dict[str, Any]) -> None:
        if topic in ("grading.targets", "grading.selected", "grading.bypass"):
            self._fill_targets()
            self._update_state()
            key = self._usable_key()
            if key and (topic == "grading.selected" or key != self._shown_key):
                self._read(key)
        elif topic == "grading.changed":
            if payload.get("key") == self.ctx.grading.selected and payload.get("grade") is not None:
                self._show_grade(payload["grade"])
            self._update_undo()
        elif topic == "grading.problem":
            message = payload.get("message") or ""
            self.problem_banner.setText(message)
            self.problem_banner.setVisible(bool(message))
        elif topic in ("grading.look", "grading.looks_changed"):
            self._fill_looks()

    # ---- gestures: start, changes and end go to Unreal in order, off the UI thread ------------------
    def _gesture_started(self, widget) -> None:
        key = self._usable_key()
        self._gesture_keys[id(widget)] = key
        if key:
            grading = self.ctx.grading
            self._in_order.put(lambda: grading.begin_gesture(key))

    def _gesture_changed(self, widget, value) -> None:
        key = self._gesture_keys.get(id(widget))
        if not key:
            return
        control = widget.control
        values = {control.key: None if _is_neutral(control, value) else value}
        grading = self.ctx.grading
        self._in_order.put(lambda: grading.send(key, values))

    def _gesture_finished(self, widget) -> None:
        key = self._gesture_keys.pop(id(widget), "")
        if key:
            grading, label = self.ctx.grading, _label(widget.control)
            self._in_order.put(lambda: grading.end_gesture(key, label))

    # ---- buttons -----------------------------------------------------------------------------------
    def _result(self, result) -> None:
        ok, message = result
        if not ok:
            self.notify(message, False)
        self._fill_targets()
        self._update_state()

    def _bypass_clicked(self) -> None:
        key = self._usable_key()
        if not key:
            self._update_state()
            return
        on = self.bypass_button.isChecked()
        self.background(lambda: self.ctx.grading.set_bypass(key, on), lambda _now: self._result((True, "")),
                        "Bypass", failed=lambda _exc: self._result((True, "")))

    def _reset(self) -> None:
        key = self._usable_key()
        if key:
            self.background(lambda: self.ctx.grading.reset(key, None), self._result, "Reset grade")

    def _enable(self) -> None:
        key = self._usable_key()
        if key:
            self.background(lambda: self.ctx.grading.enable(key), self._result, "Enable grading")

    def _restore(self) -> None:
        self.background(self.ctx.grading.restore_bypassed, self._result, "Restore the grade")

    def _undoable(self) -> list:
        return [r for r in self.ctx.session_changes() if r.kind == "grade" and r.result == "ok"
                and r.previous is not None and id(r) not in self._undone]

    def _update_undo(self) -> None:
        self.undo_button.setEnabled(bool(self._undoable()))

    def _undo(self) -> None:
        records = self._undoable()
        if not records:
            return
        rec = records[-1]
        before = len(self.ctx.changes.records)

        def done(_result) -> None:
            # The undo itself is logged as a change; it is not one to undo next.
            for made in [rec] + list(self.ctx.changes.records[before:]):
                self._undone.add(id(made))
            self.notify(f"Undone: {rec.setting} on {rec.item}")
            self._update_undo()
        self.background(lambda: self.ctx.apply_setting("grade", rec.ref, rec.previous), done, "Undo")

    # ---- looks -------------------------------------------------------------------------------------
    def _fill_looks(self) -> None:
        names = self.ctx.looks.names()
        self.looks_picker.blockSignals(True)
        self.looks_picker.clear()
        self.looks_picker.addItems(names)
        self.looks_picker.setCurrentIndex(self.looks_picker.findText(self.ctx.grading.active_look))
        self.looks_picker.blockSignals(False)
        self.delete_look_button.setEnabled(bool(names))

    def _look_picked(self, index: int) -> None:
        name = self.looks_picker.itemText(index)
        if not name:
            return

        def done(warnings) -> None:
            if warnings:
                self.notify(f"Look '{name}' recalled, but: " + "; ".join(warnings), False)
            else:
                self.notify(f"Look '{name}' recalled")
            self._fill_looks()
        self.background(lambda: self.ctx.grading.recall_look(name), done, f"Recall look '{name}'",
                        failed=lambda _exc: self._fill_looks())

    def _ask_save_look(self) -> None:
        targets = list(self.ctx.grading.targets_list)
        if not targets:
            self.notify("There is nothing to keep in a look yet: no grading targets were found", False)
            return
        dialog = LookDialog(targets, self, self.ctx.grading.active_look)
        if exec_(dialog) != QtWidgets.QDialog.Accepted:
            return
        name = dialog.name_edit.text().strip()
        if name in self.ctx.looks.names() and QtWidgets.QMessageBox.question(
                self, "Save look", f"Replace the look '{name}'?") != QtWidgets.QMessageBox.Yes:
            return
        self._save_look(name, dialog.chosen())

    def _save_look(self, name: str, keys: List[str]) -> None:
        def done(_look) -> None:
            self.ctx.grading.active_look = name
            self.notify(f"Saved look '{name}'")
            self._fill_looks()
        self.background(lambda: self.ctx.grading.save_look(name, keys), done, f"Save look '{name}'")

    def _delete_look(self, confirm: bool = True) -> None:
        name = self.looks_picker.currentText()
        if not name:
            self.notify("Pick the look to delete first", False)
            return
        if confirm and QtWidgets.QMessageBox.question(
                self, "Delete look", f"Delete the look '{name}'? The grade on the wall stays as it is.") \
                != QtWidgets.QMessageBox.Yes:
            return
        self.ctx.looks.delete(name)
        if self.ctx.grading.active_look == name:
            self.ctx.grading.active_look = ""
        self._fill_looks()

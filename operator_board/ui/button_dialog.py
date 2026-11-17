"""The button on a custom page: its editor, and the button itself.

A custom page button is a control surface key (``surfaces.layout.ButtonBinding``): the same action,
parameters, label, colours and text settings, so a key and a page button are set up the same way.
"""
from __future__ import annotations

import json
from typing import Optional

from operator_board.core.serde import from_dict, to_dict
from operator_board.qt import QtGui, QtWidgets, Qt, exec_
from operator_board.surfaces import pictures
from operator_board.surfaces.layout import FONT_WEIGHTS, ButtonBinding
from operator_board.surfaces.render import fit_label
from operator_board.ui.action_editors import (MACRO_ACTION, NUDGE_ACTION, OSC_ACTION, NudgeEditor, OscEditor,
                                              StepsEditor)
from operator_board.ui.pages.surfaces import FONT_SIZES, WEIGHT_LABELS, qt_font
from operator_board.ui.pictures import PictureField, new_picture_label, show_on
from operator_board.ui.widgets import ColorField, make_searchable, muted

DEFAULTS = ButtonBinding()


def binding_from(data) -> ButtonBinding:
    return from_dict(ButtonBinding, data) if isinstance(data, dict) else ButtonBinding()


class ButtonDialog(QtWidgets.QDialog):
    """Choose what a custom page button does and how it looks."""

    def __init__(self, parent, ctx, binding: ButtonBinding) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.result_binding: Optional[ButtonBinding] = None
        self.setWindowTitle("Button")
        self.setMinimumWidth(520)
        form = QtWidgets.QFormLayout()
        self.action = QtWidgets.QComboBox()
        self.action.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.action.setMinimumContentsLength(24)
        self.action.addItem("(none)", "")
        for a in ctx.actions.all():
            self.action.addItem(f"{a.category} › {a.label}", a.id)
        make_searchable(self.action)
        self.label = QtWidgets.QPlainTextEdit()
        self.label.setPlaceholderText("Text on the button (Enter starts a new line)")
        self.label.setFixedHeight(58)
        self.params = QtWidgets.QLineEdit()
        self.params.setPlaceholderText('{"preset": "Camera 1"}')
        self.param_hint = muted("")
        self.param_hint.setWordWrap(True)
        self.params_box = QtWidgets.QWidget()
        self.params_box.setObjectName("PlainRow")
        params_form = QtWidgets.QFormLayout(self.params_box)
        params_form.setContentsMargins(0, 0, 0, 0)
        params_form.addRow("Parameters (JSON)", self.params)
        params_form.addRow("", self.param_hint)
        self.osc_editor = OscEditor()
        self.steps_editor = StepsEditor(ctx.actions)
        self.nudge_editor = NudgeEditor(lambda: [t["key"] for t in ctx.grading.targets_list])
        self.color = ColorField("Button colour")
        self.active_color = ColorField("Active button colour")
        self.text_color = ColorField("Text colour")
        self.font_size = QtWidgets.QComboBox()
        self.font_size.setEditable(True)
        self.font_size.addItems(["Auto (fit the button)"] + FONT_SIZES[1:])
        self.font_size.setToolTip("Text size in pixels. Auto makes the text as large as fits.")
        self.font_weight = QtWidgets.QComboBox()
        for name in FONT_WEIGHTS:
            self.font_weight.addItem(WEIGHT_LABELS[name], name)
        self.word_wrap = QtWidgets.QCheckBox("Word wrap (long text continues on the next line)")
        self.picture = PictureField()
        form.addRow("Action", self.action)
        form.addRow("Label", self.label)
        form.addRow("Picture", self.picture)
        form.addRow(self.params_box)
        form.addRow(self.osc_editor)
        form.addRow(self.steps_editor)
        form.addRow(self.nudge_editor)
        form.addRow("Colour", self.color)
        form.addRow("Active colour", self.active_color)
        form.addRow("Text colour", self.text_color)
        form.addRow("Font size", self.font_size)
        form.addRow("Font weight", self.font_weight)
        form.addRow("", self.word_wrap)
        self.error = QtWidgets.QLabel("")
        self.error.setStyleSheet("color: #e03131; font-weight: 600;")
        self.error.setWordWrap(True)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.error)
        lay.addWidget(buttons)
        self._fill(binding)
        self.action.currentIndexChanged.connect(self._action_changed)

    def _fill(self, b: ButtonBinding) -> None:
        self.action.setCurrentIndex(max(0, self.action.findData(b.action)))
        self.label.setPlainText(b.label)
        self.params.setText(json.dumps(b.params) if b.params else "")
        self.osc_editor.set_params(b.params if b.action == OSC_ACTION else {})
        self.steps_editor.set_steps((b.params or {}).get("steps", []) if b.action == MACRO_ACTION else [])
        self.nudge_editor.set_params(b.params if b.action == NUDGE_ACTION else {})
        self.color.setText(b.color)
        self.active_color.setText(b.active_color)
        self.text_color.setText(b.text_color)
        self.font_size.setCurrentText(str(b.font_size) if b.font_size else self.font_size.itemText(0))
        self.font_weight.setCurrentIndex(max(0, self.font_weight.findData(b.font_weight)))
        self.word_wrap.setChecked(b.word_wrap)
        self.picture.set_binding(b)
        self._action_changed()

    def _action_changed(self) -> None:
        action_id = self.action.currentData() or ""
        self.osc_editor.setVisible(action_id == OSC_ACTION)
        self.steps_editor.setVisible(action_id == MACRO_ACTION)
        self.nudge_editor.setVisible(action_id == NUDGE_ACTION)
        self.params_box.setVisible(action_id not in (OSC_ACTION, MACRO_ACTION, NUDGE_ACTION))
        action = self.ctx.actions.get(action_id)
        if action and action.params:
            hints = []
            for p in action.params:
                choices = p.choices()
                hints.append(p.name + (f" ∈ {choices[:6]}" if choices else f" ({p.kind})"))
            self.param_hint.setText("Parameters: " + "; ".join(hints))
        else:
            self.param_hint.setText("No parameters")

    def binding(self) -> ButtonBinding:
        """The button as filled in; raises ValueError with what is wrong."""
        action_id = self.action.currentData() or ""
        if not action_id:
            raise ValueError("Choose an action for the button")
        if action_id == OSC_ACTION:
            params = self.osc_editor.params()
        elif action_id == MACRO_ACTION:
            params = {"steps": self.steps_editor.steps()}
            if not params["steps"]:
                raise ValueError("Add at least one step")
        elif action_id == NUDGE_ACTION:
            params = self.nudge_editor.params()
        else:
            try:
                params = json.loads(self.params.text()) if self.params.text().strip() else {}
            except ValueError as exc:
                raise ValueError(f"Parameters: {exc}")
            if not isinstance(params, dict):
                raise ValueError('Parameters must look like {"name": value}')
        size_text = self.font_size.currentText().strip()
        if size_text and not size_text.isdigit() and size_text != self.font_size.itemText(0):
            raise ValueError("Font size must be a number of pixels, or Auto")
        for name, value in (("Colour", self.color), ("Active colour", self.active_color),
                            ("Text colour", self.text_color)):
            if value.text() and not QtGui.QColor(value.text()).isValid():
                raise ValueError(f"{name} is not a colour: use #rrggbb or the colour picker")
        action = self.ctx.actions.get(action_id)
        return ButtonBinding(
            action=action_id, label=self.label.toPlainText() or (action.label if action else ""), params=params,
            color=self.color.text() or DEFAULTS.color, active_color=self.active_color.text() or DEFAULTS.active_color,
            text_color=self.text_color.text() or DEFAULTS.text_color,
            font_size=int(size_text) if size_text.isdigit() else 0,
            font_weight=self.font_weight.currentData() or "bold", word_wrap=self.word_wrap.isChecked(),
            **self.picture.values())

    def _accept(self) -> None:
        try:
            self.result_binding = self.binding()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


def edit_button(parent, ctx, binding: Optional[ButtonBinding] = None) -> Optional[ButtonBinding]:
    """Ask for a button's settings; None when cancelled."""
    dialog = ButtonDialog(parent, ctx, binding or ButtonBinding())
    exec_(dialog)
    return dialog.result_binding


class ActionButton(QtWidgets.QPushButton):
    """A custom page button: fills its grid cells, shows its label like a key and lights when on."""

    def __init__(self, page, binding: ButtonBinding) -> None:
        super().__init__()
        self.page = page
        self.binding = binding
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self.setMinimumSize(20, 20)
        self.picture_label = new_picture_label(self)
        self.text_label = QtWidgets.QLabel(self)
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._shown = None
        self.clicked.connect(self.press)
        self.refresh()

    def to_dict(self) -> dict:
        return to_dict(self.binding)

    def press(self) -> None:
        b = self.binding
        if not b.action:
            return
        self.page.invoke(b.action, done=lambda _r: self.refresh(), **dict(b.params or {}))

    def is_active(self) -> bool:
        return bool(self.binding.action and self.page.ctx.actions.state_of(self.binding.action))

    def refresh(self) -> None:
        b = self.binding
        active = self.is_active()
        look = (active, b.color, b.active_color, b.label, b.text_color, b.font_size, b.font_weight, b.word_wrap,
                pictures.picture_id(b.image), b.image_color, b.image_layout, self.width(), self.height())
        if look == self._shown:
            return
        self._shown = look
        background = b.active_color if active else b.color
        self.setStyleSheet(f"QPushButton {{ background: {background}; border: 2px solid "
                           f"{'#ffffff' if active else '#30353d'}; border-radius: 10px; padding: 0; }}"
                           f"QPushButton:hover {{ border-color: {b.active_color}; }}"
                           "QPushButton:pressed { border-color: #ffffff; }")
        action = self.page.ctx.actions.get(b.action)
        self.setToolTip(f"{action.category} › {action.label}" if action else "No action")
        area = show_on(self, self.picture_label, self.text_label, b, pad=8)
        if not area:
            return
        width, height = area

        def measure(line: str, px: int):
            metrics = QtGui.QFontMetrics(qt_font(b.font_weight, px))
            return metrics.horizontalAdvance(line or " "), metrics.height()

        # A fixed size is in real pixels here; Auto grows with the button, up to a readable limit.
        key_px = 72 if b.font_size else max(72, min(160, min(self.width(), self.height() * 2)))
        lines, px = fit_label(pictures.label_text(b), width, height, measure, b.font_size, b.word_wrap, key_px=key_px)
        self.text_label.setFont(qt_font(b.font_weight, px))
        self.text_label.setStyleSheet(f"color: {b.text_color}; background: transparent;")
        self.text_label.setText("\n".join(lines))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.refresh()

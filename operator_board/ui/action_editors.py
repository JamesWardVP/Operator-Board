"""Editors for button actions that need more than a line of JSON: an OSC message, and a list of
steps (several actions in a row, with delays)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from operator_board.qt import QtCore, QtGui, QtWidgets, Qt
from operator_board.surfaces.osc_send import OSC_TYPES, osc_args
from operator_board.ui.widgets import hbox, muted

OSC_ACTION = "osc.send"
MACRO_ACTION = "macro.run"
NAV_ACTIONS = ("surface.next_page", "surface.prev_page", "surface.goto_page")
DELAY = "__delay__"


class OscEditor(QtWidgets.QWidget):
    """Where to send the message, its address, and an argument field that suits the chosen type."""
    changed = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PlainRow")
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.host = QtWidgets.QLineEdit("127.0.0.1")
        self.host.setPlaceholderText("IP address, e.g. 192.168.1.50")
        self.port = QtWidgets.QLineEdit("8000")
        self.port.setValidator(QtGui.QIntValidator(1, 65535, self.port))
        self.port.setFixedWidth(80)
        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText("/cue/1/go")
        self.kind = QtWidgets.QComboBox()
        for key, label in OSC_TYPES.items():
            self.kind.addItem(label, key)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.int_value = QtWidgets.QLineEdit("0")
        self.int_value.setValidator(QtGui.QIntValidator(-2147483648, 2147483647, self.int_value))
        self.float_value = QtWidgets.QLineEdit("0")
        self.float_value.setValidator(QtGui.QDoubleValidator(self.float_value))
        self.string_value = QtWidgets.QLineEdit()
        self.string_value.setPlaceholderText("Text to send")
        self.bool_value = QtWidgets.QComboBox()
        self.bool_value.addItem("True", True)
        self.bool_value.addItem("False", False)
        self.multi_value = QtWidgets.QLineEdit()
        self.multi_value.setPlaceholderText('1 2.5 "two words" true')
        self.multi_value.setToolTip("Separate arguments with spaces. Whole numbers are sent as integers, numbers "
                                    "with a point as floats, true/false as booleans, anything else (quote it if it "
                                    "has spaces) as a string.")
        self.values = {"int": self.int_value, "float": self.float_value, "string": self.string_value,
                       "bool": self.bool_value, "multi": self.multi_value}
        self.value_stack = QtWidgets.QStackedWidget()
        self.value_stack.setObjectName("PlainRow")
        self.value_stack.addWidget(muted("(no argument)"))
        for widget in self.values.values():
            self.value_stack.addWidget(widget)
        self.value_stack.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)
        form.addRow("Send to", hbox(self.host, QtWidgets.QLabel("Port"), self.port))
        form.addRow("OSC address", self.address)
        form.addRow("Argument type", self.kind)
        self.value_label = QtWidgets.QLabel("Value")
        form.addRow(self.value_label, self.value_stack)
        self._kind_changed()

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        widget = self.values.get(kind)
        self.value_stack.setCurrentIndex(0 if widget is None else self.value_stack.indexOf(widget))
        self.value_label.setText({"multi": "Arguments", "none": ""}.get(kind, "Value"))
        self.changed.emit()

    def params(self) -> Dict[str, Any]:
        """The action's parameters (raises ValueError with what is wrong)."""
        kind = self.kind.currentData()
        widget = self.values.get(kind)
        if widget is None:
            value: Any = ""
        elif widget is self.bool_value:
            value = bool(self.bool_value.currentData())
        else:
            value = widget.text()
        if not self.host.text().strip():
            raise ValueError("enter the IP address to send the OSC message to")
        if not self.port.hasAcceptableInput():
            raise ValueError("enter a port between 1 and 65535")
        address = self.address.text().strip()
        if not address.startswith("/"):
            raise ValueError("the OSC address must start with / (for example /cue/1/go)")
        osc_args(kind, value)  # check the value suits the type
        return {"host": self.host.text().strip(), "port": int(self.port.text()), "address": address,
                "type": kind, "value": value}

    def set_params(self, params: Dict[str, Any]) -> None:
        self.host.setText(str(params.get("host", "127.0.0.1")))
        self.port.setText(str(params.get("port", 8000)))
        self.address.setText(str(params.get("address", "")))
        kind = params.get("type", "none")
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        value = params.get("value", "")
        if kind == "bool":
            self.bool_value.setCurrentIndex(0 if value in (True, "true", "True", 1) else 1)
        elif kind in self.values:
            self.values[kind].setText(str(value))


def osc_summary(params: Dict[str, Any]) -> str:
    value = params.get("value", "")
    shown = "" if params.get("type", "none") == "none" else f" {value}"
    return f"{params.get('address', '?')}{shown} → {params.get('host', '?')}:{params.get('port', '?')}"


def edit_osc(parent, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A dialog with the OSC editor; returns the new parameters, or None if cancelled."""
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("OSC message")
    dialog.resize(460, 0)
    lay = QtWidgets.QVBoxLayout(dialog)
    editor = OscEditor()
    editor.set_params(params)
    lay.addWidget(editor)
    error = QtWidgets.QLabel("")
    error.setStyleSheet("color: #ff6b6b;")
    lay.addWidget(error)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    result: Dict[str, Any] = {}

    def accept() -> None:
        try:
            result.update(editor.params())
        except ValueError as exc:
            error.setText(str(exc))
            return
        dialog.accept()
    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    lay.addWidget(buttons)
    accepted = (dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()) == QtWidgets.QDialog.Accepted
    return result if accepted else None


class _StepRow(QtWidgets.QFrame):
    def __init__(self, editor: "StepsEditor", step: Dict[str, Any]) -> None:
        super().__init__()
        self.editor = editor
        self.step = dict(step)
        self.setObjectName("StepRow")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)
        self.number = QtWidgets.QLabel("")
        self.number.setFixedWidth(22)
        lay.addWidget(self.number)
        for text, tip, move in (("▲", "Move up", -1), ("▼", "Move down", 1)):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, m=move: self.editor.move(self, m))
            lay.addWidget(b)
        if "delay" in self.step:
            lay.addWidget(QtWidgets.QLabel("Wait"))
            self.delay = QtWidgets.QLineEdit(str(int(self.step.get("delay") or 0)))
            self.delay.setValidator(QtGui.QIntValidator(0, 3600000, self.delay))
            self.delay.setFixedWidth(80)
            self.delay.textChanged.connect(self._delay_changed)
            lay.addWidget(self.delay)
            lay.addWidget(QtWidgets.QLabel("ms"))
            lay.addStretch(1)
        else:
            action = editor.actions.get(self.step.get("action", ""))
            name = QtWidgets.QLabel(action.label if action else self.step.get("action", "?"))
            name.setToolTip(self.step.get("action", ""))
            lay.addWidget(name, 1)
            if self.step.get("action") == OSC_ACTION:
                self.osc_btn = QtWidgets.QPushButton(osc_summary(self.step.get("params") or {}))
                self.osc_btn.setToolTip("Edit the OSC message")
                self.osc_btn.clicked.connect(self._edit_osc)
                lay.addWidget(self.osc_btn, 2)
            elif action and action.params:
                self.params = QtWidgets.QLineEdit(json.dumps(self.step.get("params") or {}) if self.step.get("params")
                                                  else "")
                self.params.setPlaceholderText(", ".join(p.name for p in action.params) + " (JSON)")
                self.params.setToolTip("Parameters as JSON, e.g. " + json.dumps({p.name: "" for p in action.params}))
                self.params.textChanged.connect(self._params_changed)
                lay.addWidget(self.params, 2)
        for text, tip, fn in (("+", "Add a copy at the bottom", lambda: self.editor.duplicate(self)),
                              ("−", "Remove this step", lambda: self.editor.remove(self))):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            lay.addWidget(b)

    def _delay_changed(self, text: str) -> None:
        self.step["delay"] = int(text) if text.isdigit() else 0

    def _params_changed(self, text: str) -> None:
        self.step["_params_text"] = text

    def _edit_osc(self) -> None:
        params = edit_osc(self, self.step.get("params") or {})
        if params is not None:
            self.step["params"] = params
            self.osc_btn.setText(osc_summary(params))

    def value(self) -> Dict[str, Any]:
        step = {k: v for k, v in self.step.items() if not k.startswith("_")}
        if "_params_text" in self.step:
            text = self.step["_params_text"].strip()
            try:
                step["params"] = json.loads(text) if text else {}
            except ValueError as exc:
                raise ValueError(f"step {self.number.text()} parameters must be JSON: {exc}") from exc
        if step.get("action") == OSC_ACTION and not (step.get("params") or {}).get("address"):
            raise ValueError(f"step {self.number.text()}: set up the OSC message (click it)")
        return step


class StepsEditor(QtWidgets.QWidget):
    """Steps run in order by one button: pick from the list to add one at the bottom; ▲ ▼ reorder,
    + adds a copy at the bottom, − removes it."""

    def __init__(self, actions, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PlainRow")
        self.actions = actions
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.add_combo = QtWidgets.QComboBox()
        self.add_combo.addItem("+ Add a step…", "")
        self.add_combo.addItem("Delay (wait before the next step)", DELAY)
        for a in actions.all():
            if a.id not in (MACRO_ACTION,) + NAV_ACTIONS:
                self.add_combo.addItem(f"{a.category} › {a.label}", a.id)
        self.add_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.add_combo.setMinimumContentsLength(18)
        from operator_board.ui.widgets import make_searchable
        make_searchable(self.add_combo)
        self.add_combo.currentIndexChanged.connect(self._picked)
        lay.addWidget(self.add_combo)
        self.rows_box = QtWidgets.QWidget()
        self.rows_box.setObjectName("PlainRow")
        self.rows = QtWidgets.QVBoxLayout(self.rows_box)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        self.empty = muted("No steps yet: pick an action or a delay above.")
        self.rows.addWidget(self.empty)
        lay.addWidget(self.rows_box)

    def _picked(self, index: int) -> None:
        data = self.add_combo.itemData(index) if index > 0 else ""
        if not data:
            return
        self.add_combo.blockSignals(True)
        self.add_combo.setCurrentIndex(0)
        if self.add_combo.isEditable():
            self.add_combo.lineEdit().clear()
        self.add_combo.blockSignals(False)
        if data == DELAY:
            self.add({"delay": 500})
        elif data == OSC_ACTION:
            params = edit_osc(self, {})
            if params is not None:
                self.add({"action": OSC_ACTION, "params": params})
        else:
            self.add({"action": data, "params": {}})

    def _row_widgets(self) -> List[_StepRow]:
        return [self.rows.itemAt(i).widget() for i in range(self.rows.count())
                if isinstance(self.rows.itemAt(i).widget(), _StepRow)]

    def _renumber(self) -> None:
        rows = self._row_widgets()
        for n, row in enumerate(rows, start=1):
            row.number.setText(f"{n}.")
        self.empty.setVisible(not rows)

    def add(self, step: Dict[str, Any]) -> None:
        self.rows.addWidget(_StepRow(self, step))
        self._renumber()

    def duplicate(self, row: _StepRow) -> None:
        try:
            self.add(row.value())
        except ValueError:
            self.add({k: v for k, v in row.step.items() if not k.startswith("_")})

    def remove(self, row: _StepRow) -> None:
        self.rows.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._renumber()

    def move(self, row: _StepRow, offset: int) -> None:
        index = self.rows.indexOf(row)
        target = index + offset
        if target < 1 or target >= self.rows.count():  # index 0 is the "no steps" label
            return
        self.rows.removeWidget(row)
        self.rows.insertWidget(target, row)
        self._renumber()

    def steps(self) -> List[Dict[str, Any]]:
        return [row.value() for row in self._row_widgets()]

    def set_steps(self, steps: List[Dict[str, Any]]) -> None:
        for row in self._row_widgets():
            self.remove(row)
        for step in steps or []:
            self.add(step)
        self._renumber()

"""Generic form for editing config dataclasses, so every settings page shares one editor."""
from __future__ import annotations

import dataclasses
import json
import typing
from typing import Any, Callable, Dict, List, Optional, Sequence

from operator_board.qt import QtWidgets, exec_


# Words that read badly when a field name is simply capitalised.
_LABEL_WORDS = {"udp": "UDP", "ndisplay": "nDisplay", "uproject": ".uproject", "api": "API", "ip": "IP",
                "dir": "folder", "args": "arguments", "rc": "Remote Control", "mu": "multi-user", "id": "ID",
                "json": "JSON", "dlss": "DLSS", "ui": "UI", "url": "URL"}


def nice_label(field_name: str) -> str:
    """'udp_unicast_endpoint' -> 'UDP unicast endpoint', 'engine_dir_override' -> 'Engine folder override'."""
    words = [_LABEL_WORDS.get(w, w) for w in field_name.split("_")]
    first = words[0]
    if first == first.lower():
        words[0] = first[:1].upper() + first[1:]
    return " ".join(words)


# Width of the label column, so forms stacked on one page line up.
FORM_LABEL_WIDTH = 230


class DataclassForm(QtWidgets.QWidget):
    """Edits a dataclass instance in place.

    choices:  field -> list of values or callable returning (label, value) pairs
    labels:   field -> display label
    hidden:   fields to omit
    browse:   field -> "file" | "dir" to add a browse button
    """

    def __init__(self, obj: Any, choices: Optional[Dict[str, Any]] = None, labels: Optional[Dict[str, str]] = None,
                 hidden: Sequence[str] = (), browse: Optional[Dict[str, str]] = None, parent=None) -> None:
        super().__init__(parent)
        self.obj = obj
        self.choices = choices or {}
        self.labels = labels or {}
        self.browse = browse or {}
        self.hints = typing.get_type_hints(type(obj))
        self.widgets: Dict[str, Any] = {}
        form = QtWidgets.QFormLayout(self)
        for f in dataclasses.fields(obj):
            if f.name in hidden:
                continue
            widget, row = self._make_widget(f.name, getattr(obj, f.name))
            self.widgets[f.name] = widget
            label = QtWidgets.QLabel(self.labels.get(f.name, nice_label(f.name)))
            label.setMinimumWidth(FORM_LABEL_WIDTH)
            form.addRow(label, row)

    def _make_widget(self, name: str, value: Any):
        hint = self.hints.get(name)
        if name in self.choices:
            combo = QtWidgets.QComboBox()
            combo.setEditable(False)
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            opts = self.choices[name]
            opts = opts() if callable(opts) else opts
            for opt in opts:
                label, val = opt if isinstance(opt, tuple) else (str(opt), opt)
                combo.addItem(label, val)
            idx = combo.findData(value)
            if idx < 0:
                combo.addItem(str(value), value)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
            return combo, combo
        if hint is bool:
            box = QtWidgets.QCheckBox()
            box.setChecked(bool(value))
            return box, box
        if hint is int:
            spin = QtWidgets.QSpinBox()
            spin.setRange(-1_000_000, 1_000_000)
            spin.setValue(int(value))
            return spin, spin
        if hint is float:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(-1e9, 1e9)
            spin.setDecimals(3)
            spin.setValue(float(value))
            return spin, spin
        if typing.get_origin(hint) in (list, dict):
            edit = QtWidgets.QLineEdit(self._encode_complex(value))
            edit.setToolTip("Comma separated list" if typing.get_origin(hint) is list else "JSON object")
            return edit, edit
        edit = QtWidgets.QLineEdit(str(value))
        edit.setCursorPosition(0)  # long paths show their start, not their end
        if name in self.browse:
            btn = QtWidgets.QPushButton("Browse…")
            btn.clicked.connect(lambda: self._browse(edit, self.browse[name]))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(btn)
            holder = QtWidgets.QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            return edit, holder
        return edit, edit

    def _encode_complex(self, value: Any) -> str:
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return ", ".join(value)
        return json.dumps(value)

    def _browse(self, edit: QtWidgets.QLineEdit, kind: str) -> None:
        if kind == "dir":
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose file", edit.text())
        if path:
            edit.setText(path)

    def apply(self) -> None:
        """Write widget values back to the dataclass. Raises ValueError on bad input."""
        for name, widget in self.widgets.items():
            hint = self.hints.get(name)
            if isinstance(widget, QtWidgets.QComboBox):
                value = widget.currentData()
            elif isinstance(widget, QtWidgets.QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                value = widget.value()
            else:
                text = widget.text()
                origin = typing.get_origin(hint)
                if origin is list:
                    args = typing.get_args(hint)
                    if args and args[0] is str:
                        value = [s.strip() for s in text.split(",") if s.strip()]
                    else:
                        value = json.loads(text or "[]")
                elif origin is dict:
                    try:
                        value = json.loads(text or "{}")
                    except ValueError as exc:
                        raise ValueError(f"{name}: invalid JSON ({exc})") from exc
                else:
                    value = text
            setattr(self.obj, name, value)


def edit_dataclass(parent, title: str, obj: Any, validate: Optional[Callable[[Any], Optional[str]]] = None,
                   **form_kwargs) -> bool:
    """Show a modal editor for obj. Returns True if the user saved (obj is modified in place)."""
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(680)
    lay = QtWidgets.QVBoxLayout(dialog)
    work = dataclasses.replace(obj)
    form = DataclassForm(work, **form_kwargs)
    lay.addWidget(form)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
    lay.addWidget(buttons)

    def accept() -> None:
        try:
            form.apply()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(dialog, "Invalid value", str(exc))
            return
        problem = validate(work) if validate else None
        if problem:
            QtWidgets.QMessageBox.warning(dialog, "Invalid value", problem)
            return
        dialog.accept()

    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    if exec_(dialog) == QtWidgets.QDialog.Accepted:
        for f in dataclasses.fields(obj):
            setattr(obj, f.name, getattr(work, f.name))
        return True
    return False


def unique_name_validator(existing: List[str], original: str):
    def check(obj) -> Optional[str]:
        if not obj.name.strip():
            return "Name cannot be empty"
        if obj.name != original and obj.name in existing:
            return f"'{obj.name}' already exists"
        return None
    return check

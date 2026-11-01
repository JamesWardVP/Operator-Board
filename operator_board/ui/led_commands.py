"""Edit an LED processor type's commands and test patterns.

The built-in processor types are never changed: saving edits to one makes a custom copy
("<name> (custom)") that the processor then uses. Editing a custom type saves it in place.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Tuple

from operator_board.qt import QtGui, QtWidgets, Qt
from operator_board.ui.widgets import hbox, muted

STANDARD = [("freeze_on", "Freeze on"), ("freeze_off", "Freeze off"), ("blackout_on", "Blackout on"),
            ("blackout_off", "Blackout off"), ("test_pattern_off", "Test pattern off"), ("brightness", "Brightness")]
STATUS = "status_command"

SCHEMA_HELP = {
    "http": 'HTTP: {"method": "GET", "path": "/api/...", "json": {...} or "body": "..."}. '
            'Brightness: put {value} where the number goes and give "value": {"min": 0, "max": 100}.',
    "https": 'HTTPS: {"method": "GET", "path": "/api/...", "json": {...} or "body": "..."}.',
    "tcp": 'TCP: {"hex": "55 AA 00 ..."} or {"text": "..."} or {"bytes": [85, 170]}. NovaStar frames: add '
           '"checksum": "novastar".',
    "udp": 'UDP: {"hex": "..."} or {"text": "..."} or {"bytes": [...]}.',
    "osc": 'OSC: {"address": "/freeze", "args": [1]}.',
    "simulator": "Simulator: any JSON object (nothing is sent).",
}


class LEDCommandsDialog(QtWidgets.QDialog):
    def __init__(self, parent, registry, profile_id: str) -> None:
        super().__init__(parent)
        self.registry = registry
        self.base_id = profile_id
        self.profile = copy.deepcopy(registry.get(profile_id) or {})
        self.saved: Optional[Dict[str, Any]] = None
        self.builtin = registry.is_builtin(profile_id)
        self.setWindowTitle(f"Commands: {self.profile.get('name', profile_id)}")
        self.resize(900, 560)
        lay = QtWidgets.QVBoxLayout(self)
        if self.builtin:
            lay.addWidget(muted("This is a built-in processor type, so it stays as it is: saving makes a custom "
                                "copy for your processor (it then appears in the Processor type list as well)."))
        else:
            based = self.profile.get("based_on")
            lay.addWidget(muted("A custom processor type" + (f", based on {registry.get(based).get('name', based)}"
                                                              if based and registry.get(based) else "")
                                + ". Saving changes it for every processor that uses it."))
        self.name = QtWidgets.QLineEdit(self.profile.get("name", "") if not self.builtin else
                                        f"{self.profile.get('name', profile_id)} (custom)")
        name_row = hbox(QtWidgets.QLabel("Name"), self.name)
        name_row.setStretch(1, 1)
        lay.addLayout(name_row)

        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        self.entries = QtWidgets.QListWidget()
        self.entries.setMinimumWidth(260)
        self.entries.currentRowChanged.connect(self._entry_changed)
        left.addWidget(self.entries, 1)
        add = QtWidgets.QPushButton("+ Test pattern")
        add.clicked.connect(self._add_pattern)
        self.remove_btn = QtWidgets.QPushButton("Remove test pattern")
        self.remove_btn.clicked.connect(self._remove_pattern)
        left.addLayout(hbox(add, self.remove_btn))
        body.addLayout(left, 0)
        right = QtWidgets.QVBoxLayout()
        self.what = QtWidgets.QLabel("")
        self.what.setObjectName("CardTitle")
        right.addWidget(self.what)
        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.editor.setPlaceholderText("Empty = not supported by this processor")
        right.addWidget(self.editor, 1)
        self.error = QtWidgets.QLabel("")
        self.error.setStyleSheet("color: #ff6b6b;")
        self.error.setWordWrap(True)
        right.addWidget(self.error)
        help_label = muted(SCHEMA_HELP.get(self.profile.get("transport", ""), "") +
                           ' Use {"ref": "other_command"} to reuse another command.')
        help_label.setWordWrap(True)
        right.addWidget(help_label)
        body.addLayout(right, 1)
        lay.addLayout(body, 1)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("Save as custom" if self.builtin else "Save")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        # (kind, key) per row: ("command", name) | ("status", "") | ("pattern", index)
        self.rows: List[Tuple[str, Any]] = []
        self.texts: Dict[int, str] = {}
        self._current = -1
        self._fill()

    # ---- entries ------------------------------------------------------------------------
    def _fill(self, select: int = 0) -> None:
        self.entries.blockSignals(True)
        self.entries.clear()
        self.rows, self.texts = [], {}
        commands = self.profile.get("commands", {})
        names = [n for n, _ in STANDARD] + sorted(k for k in commands if k not in dict(STANDARD))
        for name in names:
            label = dict(STANDARD).get(name, name)
            supported = bool(commands.get(name))
            self._add_row(("command", name), f"{label}" + ("" if supported else "  (not supported)"),
                          commands.get(name) or None)
        self._add_row(("status", ""), "Connection check", self.profile.get(STATUS) or None)
        for i, pattern in enumerate(self.profile.get("test_patterns", [])):
            self._add_row(("pattern", i), f"Test pattern: {pattern.get('label', pattern.get('id'))}", pattern)
        self.entries.blockSignals(False)
        self._current = -1
        self.entries.setCurrentRow(min(select, self.entries.count() - 1))

    def _add_row(self, key, label: str, value) -> None:
        self.texts[len(self.rows)] = json.dumps(value, indent=2) if value else ""
        self.rows.append(key)
        self.entries.addItem(label)

    def _entry_changed(self, row: int) -> None:
        if self._current >= 0 and not self._keep_current():
            self.entries.blockSignals(True)
            self.entries.setCurrentRow(self._current)
            self.entries.blockSignals(False)
            return
        self._current = row
        if row < 0:
            return
        kind, key = self.rows[row]
        self.what.setText(self.entries.item(row).text().split("  (")[0].upper())
        self.editor.setPlainText(self.texts.get(row, ""))
        self.remove_btn.setEnabled(kind == "pattern")
        self.error.setText("")

    def _parse(self, text: str):
        text = text.strip()
        if not text:
            return None
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("a command is a JSON object: { ... }")
        return value

    def _keep_current(self) -> bool:
        """Check the JSON being edited; keep it, or say what is wrong and stay on it."""
        text = self.editor.toPlainText()
        try:
            value = self._parse(text)
            kind, _key = self.rows[self._current]
            if kind == "pattern":
                if not value or not value.get("id") or not isinstance(value.get("commands"), list):
                    raise ValueError('a test pattern needs "id", "label" and a "commands" list')
        except ValueError as exc:
            self.error.setText(f"Not valid: {exc}")
            return False
        self.texts[self._current] = text
        return True

    def _collect(self) -> Dict[str, Any]:
        profile = copy.deepcopy(self.profile)
        commands: Dict[str, Any] = {}
        patterns = []
        status = None
        for row, (kind, key) in enumerate(self.rows):
            value = self._parse(self.texts.get(row, ""))
            if kind == "command" and value:
                commands[key] = value
            elif kind == "status":
                status = value
            elif kind == "pattern" and value:
                patterns.append(value)
        profile["commands"] = commands
        profile["test_patterns"] = patterns
        if status:
            profile[STATUS] = status
        else:
            profile.pop(STATUS, None)
        profile["name"] = self.name.text().strip() or profile.get("name", self.base_id)
        return profile

    def _add_pattern(self) -> None:
        if self._current >= 0 and not self._keep_current():
            return
        self.profile = self._collect()
        n = len(self.profile["test_patterns"]) + 1
        self.profile["test_patterns"].append({"id": f"pattern_{n}", "label": f"Pattern {n}", "commands": [{}]})
        self._fill(select=10 ** 6)  # the new pattern, last in the list

    def _remove_pattern(self) -> None:
        if self._current < 0 or self.rows[self._current][0] != "pattern":
            return
        index = self.rows[self._current][1]
        self.texts[self._current] = json.dumps(self.profile["test_patterns"][index])  # skip validation
        self.profile = self._collect()
        del self.profile["test_patterns"][index]
        self._fill(max(0, self._current - 1))

    def _save(self) -> None:
        if self._current >= 0 and not self._keep_current():
            return
        try:
            profile = self._collect()
        except ValueError as exc:
            self.error.setText(f"Not valid: {exc}")
            return
        try:
            self.saved = self.registry.save_custom(profile, self.base_id)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Commands", f"Could not save: {exc}")
            return
        self.accept()


def edit_led_commands(parent, registry, profile_id: str) -> Optional[Dict[str, Any]]:
    """Open the editor; returns the saved (custom) profile, or None if cancelled."""
    dialog = LEDCommandsDialog(parent, registry, profile_id)
    if (dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()) == QtWidgets.QDialog.Accepted:
        return dialog.saved
    return None

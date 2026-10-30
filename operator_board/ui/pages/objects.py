"""Remote control of instance-editable objects: Remote Control Presets and an actor property browser."""
from __future__ import annotations

import json

from operator_board.qt import QtWidgets, Qt
from operator_board.remote.targets import TARGET_CHOICES
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, make_table, muted, set_row


def parse_value(text: str):
    """Accept JSON (numbers, true/false, {"X":..}) and fall back to a plain string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


VALUE_HINT = 'Value as JSON, e.g. 1.5  true  "text"  {"X":0,"Y":0,"Z":100}'


class ObjectsPage(BasePage):
    title = "Remote Objects"

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        self.target = QtWidgets.QComboBox()
        for t in TARGET_CHOICES:
            self.target.addItem(t)
        lay.addLayout(hbox(QtWidgets.QLabel("Apply changes to"), self.target, None))
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._presets_tab(), "Remote Control Presets")
        tabs.addTab(self._actors_tab(), "Actor browser")
        lay.addWidget(tabs)

    # ---- presets ------------------------------------------------------------------
    def _presets_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        left = Card("Presets")
        self.presets = QtWidgets.QListWidget()
        self.presets.currentTextChanged.connect(self._load_preset)
        b = QtWidgets.QPushButton("↻ Load presets")
        b.clicked.connect(self._load_presets)
        left.add(b)
        left.add(self.presets)
        left.add(muted("Expose properties and functions in a Remote Control Preset in your project; they appear "
                       "here as the instance-editable controls."))
        lay.addWidget(left, 1)
        right = Card("Exposed properties & functions")
        self.rows = make_table(["Group", "Label", "Type", "Value"])
        self.rows.itemSelectionChanged.connect(self._row_selected)
        right.add(self.rows)
        self.value = QtWidgets.QLineEdit()
        self.value.setPlaceholderText(VALUE_HINT)
        read = QtWidgets.QPushButton("Read")
        read.clicked.connect(self._read_value)
        write = QtWidgets.QPushButton("Set")
        write.clicked.connect(self._write_value)
        call = QtWidgets.QPushButton("Call function")
        call.clicked.connect(self._call)
        right.add_layout(hbox(self.value, read, write, call))
        lay.addWidget(right, 2)
        self._preset_rows = []
        return w

    def _load_presets(self) -> None:
        def fill(names):
            self.presets.clear()
            self.presets.addItems(names)
        self.background(lambda: self.ctx.objects.presets(), fill, "Loading presets")

    def _load_preset(self, name: str) -> None:
        if not name:
            return

        def fill(rows):
            self._preset_rows = rows
            self.rows.setRowCount(0)
            for i, r in enumerate(rows):
                set_row(self.rows, i, [r["group"], r["label"], r["type"], ""])
        self.background(lambda: self.ctx.objects.preset_rows(name), fill, "Loading preset")

    def _current_row(self):
        rows = sorted({i.row() for i in self.rows.selectedIndexes()})
        return (rows[0], self._preset_rows[rows[0]]) if rows and rows[0] < len(self._preset_rows) else (None, None)

    def _row_selected(self) -> None:
        idx, row = self._current_row()
        if row and row["kind"] == "property":
            self._read_value()

    def _read_value(self) -> None:
        idx, row = self._current_row()
        preset = self.presets.currentItem().text() if self.presets.currentItem() else ""
        if row is None or row["kind"] != "property":
            return

        def show(value):
            text = json.dumps(value)
            self.value.setText(text)
            self.rows.setItem(idx, 3, QtWidgets.QTableWidgetItem(text))
        self.background(lambda: self.ctx.objects.get_preset_value(preset, row["label"]), show, "Reading value")

    def _write_value(self) -> None:
        idx, row = self._current_row()
        preset = self.presets.currentItem().text() if self.presets.currentItem() else ""
        if row is None:
            return
        value, target = parse_value(self.value.text()), self.target.currentText()
        self.background(lambda: self.ctx.objects.set_preset_value(preset, row["label"], value, target),
                        self.report(f"Set {row['label']}"), "Setting value")

    def _call(self) -> None:
        idx, row = self._current_row()
        preset = self.presets.currentItem().text() if self.presets.currentItem() else ""
        if row is None or row["kind"] != "function":
            self.notify("Select an exposed function", False)
            return
        target = self.target.currentText()
        self.background(lambda: self.ctx.objects.call_preset_function(preset, row["label"], target),
                        self.report(f"Called {row['label']}"), "Calling function")

    # ---- actor browser ------------------------------------------------------------------
    def _actors_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        left = Card("Actors in level")
        self.class_filter = QtWidgets.QLineEdit()
        self.class_filter.setPlaceholderText("Class filter (e.g. Light, CineCamera)")
        b = QtWidgets.QPushButton("List actors")
        b.clicked.connect(self._list_actors)
        left.add_layout(hbox(self.class_filter, b))
        self.actors = QtWidgets.QListWidget()
        self.actors.currentItemChanged.connect(self._describe)
        left.add(self.actors)
        lay.addWidget(left, 1)
        right = Card("Properties")
        self.props = make_table(["Property", "Type", "Description"])
        right.add(self.props)
        self.prop_value = QtWidgets.QLineEdit()
        self.prop_value.setPlaceholderText(VALUE_HINT)
        read = QtWidgets.QPushButton("Read")
        read.clicked.connect(self._read_prop)
        write = QtWidgets.QPushButton("Set")
        write.clicked.connect(self._write_prop)
        right.add_layout(hbox(self.prop_value, read, write))
        lay.addWidget(right, 2)
        return w

    def _list_actors(self) -> None:
        def fill(actors):
            self.actors.clear()
            for a in actors:
                item = QtWidgets.QListWidgetItem(f"{a['label']}  ({a['class']})")
                item.setData(Qt.UserRole, a["path"])
                self.actors.addItem(item)
        self.background(lambda: self.ctx.objects.actors(self.class_filter.text().strip()), fill, "Listing actors")

    def _describe(self, item, _prev=None) -> None:
        if item is None:
            return
        path = item.data(Qt.UserRole)

        def fill(desc):
            self.props.setRowCount(0)
            for i, p in enumerate(desc.get("Properties", []) or []):
                set_row(self.props, i, [p.get("Name", ""), p.get("Type", ""), p.get("Description", "")])
        self.background(lambda: self.ctx.objects.describe(path), fill, "Describing actor")

    def _selected_prop(self):
        item = self.actors.currentItem()
        rows = sorted({i.row() for i in self.props.selectedIndexes()})
        if not item or not rows:
            return None, None
        return item.data(Qt.UserRole), self.props.item(rows[0], 0).text()

    def _read_prop(self) -> None:
        path, prop = self._selected_prop()
        if path:
            self.background(lambda: self.ctx.objects.get_property(path, prop),
                            lambda v: self.prop_value.setText(json.dumps(v)), "Reading property")

    def _write_prop(self) -> None:
        path, prop = self._selected_prop()
        if path:
            value, target = parse_value(self.prop_value.text()), self.target.currentText()
            self.background(lambda: self.ctx.objects.set_property(path, prop, value, target),
                            self.report(f"Set {prop}"), "Setting property")

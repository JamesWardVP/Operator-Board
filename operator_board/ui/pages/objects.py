"""Remote control of instance-editable objects: Remote Control Presets and an actor property browser."""
from __future__ import annotations

import json

from operator_board.qt import QtGui, QtWidgets, Qt
from operator_board.remote.targets import TARGET_CHOICES
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, DragNumber, LiveSetter, hbox, make_table, muted, set_row


def parse_value(text: str):
    """Accept JSON (numbers, true/false, {"X":..}) and fall back to a plain string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


VALUE_HINT = 'Value as JSON, e.g. 1.5  true  "text"  {"X":0,"Y":0,"Z":100}'


class ObjectsPage(BasePage):
    title = "Remote Objects"
    EDITOR_EVENTS = ("editor.ready", "editor.level_changed")


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
        b = QtWidgets.QPushButton("↻ Refresh")
        b.setToolTip("Read the Remote Control Presets again (done automatically when the editor is ready)")
        b.clicked.connect(lambda: self._load_presets())
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

    def on_event(self, topic, payload) -> None:
        super().on_event(topic, payload)
        if topic in self.EDITOR_EVENTS:  # the editor is ready or opened another level
            self._load_presets(quiet=True)
            self._list_actors(quiet=True)

    @staticmethod
    def _message(widget, text: str) -> None:
        widget.clear()
        item = QtWidgets.QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        widget.addItem(item)

    def _load_presets(self, quiet: bool = False) -> None:
        def fill(names):
            self.presets.clear()
            if names:
                self.presets.addItems(names)
            else:
                self._message(self.presets, "No Remote Control presets found")
        self.background(lambda: self.ctx.objects.presets(), fill, "Loading presets", quiet=quiet,
                        failed=lambda exc: self._message(self.presets, "Could not read presets; press Refresh"))

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
        quick = []
        for label, text in (("All", ""), ("Lights", "Light"), ("Cameras", "Camera"), ("nDisplay", "nDisplay")):
            q = QtWidgets.QPushButton(label)
            q.clicked.connect(lambda _=False, t=text: (self.class_filter.setText(t), self._list_actors()))
            quick.append(q)
        left.add_layout(hbox(QtWidgets.QLabel("Show"), *quick, None))
        b = QtWidgets.QPushButton("↻ Refresh")
        b.setToolTip("List the level's actors again (done automatically when the editor is ready)")
        b.clicked.connect(lambda: self._list_actors())
        self.class_filter.returnPressed.connect(lambda: self._list_actors())
        left.add_layout(hbox(self.class_filter, b))
        self.actors = QtWidgets.QListWidget()
        self.actors.currentItemChanged.connect(self._describe)
        left.add(self.actors)
        lay.addWidget(left, 1)
        right = Card("Properties")
        self.quick_card = QtWidgets.QFrame()
        self.quick_form = QtWidgets.QFormLayout(self.quick_card)
        self.quick_form.setContentsMargins(0, 0, 0, 8)
        self.quick_title = muted("")
        right.add(self.quick_title)
        right.add(self.quick_card)
        self.quick_card.hide()
        self.props = make_table(["Property", "Type", "Value", "Description"])
        header = self.props.horizontalHeader()
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        self.props.setColumnWidth(1, 90)
        self.props.setColumnWidth(2, 220)
        self.props.setWordWrap(False)
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

    def _list_actors(self, quiet: bool = False) -> None:
        def fill(actors):
            self.actors.clear()
            if not actors:
                self._message(self.actors, "No actors found" + (" for that class filter" if
                                                                self.class_filter.text().strip() else ""))
            for a in actors:
                item = QtWidgets.QListWidgetItem(f"{a['label']}  ({a['class']})")
                item.setData(Qt.UserRole, a["path"])
                self.actors.addItem(item)
        self.background(lambda: self.ctx.objects.actors(self.class_filter.text().strip()), fill, "Listing actors",
                        quiet=quiet, failed=lambda exc: self._message(self.actors, "Could not list actors; press Refresh"))

    def _describe(self, item, _prev=None) -> None:
        if item is None or item.data(Qt.UserRole) is None:
            return
        path = item.data(Qt.UserRole)
        self.background(lambda: self.ctx.objects.quick_controls(path), lambda q: self._show_quick(path, q),
                        "Reading light / camera", quiet=True)

        def fill(result):
            desc, values = result
            self.props.setRowCount(0)
            for i, p in enumerate(desc.get("Properties", []) or []):
                name = p.get("Name", "")
                set_row(self.props, i, [name, p.get("Type", ""), self._short(values.get(name)) if name in values
                                        else "", p.get("Description", "")])
                self.props.item(i, 1).setToolTip(p.get("Type", ""))

        def read():  # the description and every current value
            return self.ctx.objects.describe(path), self.ctx.objects.property_values(path)
        self.background(read, fill, "Describing actor")

    @staticmethod
    def _short(value) -> str:
        text = json.dumps(value) if not isinstance(value, str) else value
        return text if len(text) <= 120 else text[:117] + "…"

    # ---- light and camera quick controls ----------------------------------------------------
    def _show_quick(self, path: str, quick) -> None:
        while self.quick_form.rowCount():
            self.quick_form.removeRow(0)
        controls = quick.get("controls") or []
        if not controls:
            self.quick_card.hide()
            self.quick_title.setText("")
            return
        kind = "Light" if quick.get("kind") == "light" else "Camera"
        drivers = quick.get("drivers") or []
        self.quick_title.setText(f"{kind} controls (changes apply in the editor and reach the nodes through multi-user)"
                                 + (f". Locked: driven by {' and '.join(drivers)}" if drivers else ""))
        for c in controls:
            self.quick_form.addRow(f"{c['label']}" + (f" ({c['unit']})" if c.get("unit") else ""),
                                   self._quick_widget(path, c))
        self.quick_card.show()

    def _quick_widget(self, path: str, c) -> QtWidgets.QWidget:
        locked = c.get("locked") or ""
        apply = lambda value, cid=c["id"]: self.background(  # noqa: E731
            lambda: self.ctx.objects.set_quick_control(path, cid, value),
            lambda _r: self.notify(f"{c['label']} set"), f"Setting {c['label']}")
        if c["type"] == "choice":
            w = QtWidgets.QComboBox()
            for value, label in c.get("options") or []:
                w.addItem(label, value)
            w.setCurrentIndex(max(0, w.findData(c["value"])))

            def changed(_i, combo=w, cid=c["id"]):
                value = combo.currentData()
                # Changing units converts the intensity (as the Details panel does): show the new value.
                self.background(lambda: self.ctx.objects.set_quick_control(path, cid, value),
                                lambda _r: (self.notify(f"{c['label']}: {combo.currentText()}"),
                                            self._reload_quick(path)), f"Setting {c['label']}")
            w.currentIndexChanged.connect(changed)
        elif c["type"] == "bool":
            w = QtWidgets.QCheckBox()
            w.setChecked(bool(c["value"]))
            w.toggled.connect(lambda on: apply(on))
        elif c["type"] == "color":
            w = QtWidgets.QPushButton("Choose…")
            w.setProperty("rgb", list(c["value"]))

            def pick(_=False, button=w):
                r, g, b = button.property("rgb")
                chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(r, g, b), self, c["label"])
                if chosen.isValid():
                    rgb = [chosen.red(), chosen.green(), chosen.blue()]
                    button.setProperty("rgb", rgb)
                    self._paint_swatch(button, rgb)
                    apply(rgb)
            w.clicked.connect(pick)
            self._paint_swatch(w, c["value"])
        else:  # a number: drag it like a Details panel field, or click and type
            w = DragNumber(float(c["value"]), c.get("min"), c.get("max"))
            live = LiveSetter(self, lambda value, cid=c["id"]: self.ctx.objects.set_quick_control(path, cid, value),
                              f"Setting {c['label']}")
            w.dragging.connect(lambda value: live(value))
            w.committed.connect(lambda value: live(value, lambda _r: self.notify(f"{c['label']} set to {value:g}")))
        if locked:
            w.setEnabled(False)
            w.setToolTip(locked + ", which would overwrite a change made here")
        return w

    def _reload_quick(self, path: str) -> None:
        self.background(lambda: self.ctx.objects.quick_controls(path), lambda q: self._show_quick(path, q),
                        "Reading light / camera", quiet=True)

    @staticmethod
    def _paint_swatch(button, rgb) -> None:
        r, g, b = rgb
        text = "#111" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#fff"
        button.setStyleSheet(f"background: rgb({r},{g},{b}); color: {text};")
        button.setText(f"#{r:02x}{g:02x}{b:02x}")

    def _selected_prop(self):
        item = self.actors.currentItem()
        rows = sorted({i.row() for i in self.props.selectedIndexes()})
        if not item or not rows:
            return None, None
        return item.data(Qt.UserRole), self.props.item(rows[0], 0).text()

    def _read_prop(self) -> None:
        path, prop = self._selected_prop()
        row = sorted({i.row() for i in self.props.selectedIndexes()})

        def show(value):
            self.prop_value.setText(json.dumps(value))
            if row:
                self.props.setItem(row[0], 2, QtWidgets.QTableWidgetItem(self._short(value)))
        if path:
            self.background(lambda: self.ctx.objects.get_property(path, prop), show, "Reading property")

    def _write_prop(self) -> None:
        path, prop = self._selected_prop()
        if path:
            value, target = parse_value(self.prop_value.text()), self.target.currentText()
            self.background(lambda: self.ctx.objects.set_property(path, prop, value, target),
                            self.report(f"Set {prop}"), "Setting property")

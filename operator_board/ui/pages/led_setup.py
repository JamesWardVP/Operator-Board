"""LED processor setup: choose the processor type, set its address, test and fine-tune commands. Each
processor can be frozen, blacked out, located and resumed from its row; the Advanced section adds
groups of processors and individual control of each one."""
from __future__ import annotations

from operator_board.core.config import LEDProcessorConfig
from operator_board.led.profiles import CONFIDENCE_LABELS
from operator_board.qt import QtGui, QtWidgets, Qt
from operator_board.ui.forms import edit_dataclass, unique_name_validator
from operator_board.ui.group_dialog import edit_led_group
from operator_board.ui.led_controls import ModeButtons
from operator_board.ui.led_commands import edit_led_commands
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, make_table, muted, set_row

CONFIDENCE_COLORS = {"source": "#2f9e44", "partial": "#f08c00", "assumed": "#e03131", "user": "#3d8bfd"}
COMMANDS = ["freeze_on", "freeze_off", "blackout_on", "blackout_off", "test_pattern_off", "locate_on", "locate_off",
            "brightness"]
ROW_MODES = (("Freeze", "frozen"), ("Blackout", "blackout"), ("Locate", "locate"), ("Resume", "normal"))
HEADERS = ["Name", "Processor type", "Address", "Enabled", "Status"] + [m[0] for m in ROW_MODES] + \
          ["Last command / error"]
LAST = len(HEADERS) - 1


class LEDSetupPage(BasePage):
    title = "LED Processor Setup"
    refresh_topics = ("led.",)

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        top = QtWidgets.QHBoxLayout()
        card = Card("LED processors on this stage")
        self.table = make_table(HEADERS)
        self.table.itemSelectionChanged.connect(self._show_details)
        self.table.doubleClicked.connect(lambda _: self._edit())
        card.add(self.table)
        btns = []
        for label, fn in [("Add processor", self._add), ("Edit", self._edit), ("Remove", self._remove),
                          ("Test connection", self._test), ("Test all", self._test_all)]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            btns.append(b)
        card.add_layout(hbox(*btns, None))
        top.addWidget(card, 3)
        top.addWidget(self._advanced_card(), 2)
        lay.addLayout(top, 2)

        bottom = QtWidgets.QHBoxLayout()
        manual = Card("Selected processor")
        self.notes = muted("")
        manual.add(self.notes)
        self.selected_modes_holder = QtWidgets.QHBoxLayout()
        self.selected_modes = None
        manual.add_layout(self.selected_modes_holder)
        self.pattern = QtWidgets.QComboBox()
        self.show_pattern = show = QtWidgets.QPushButton("Show pattern")
        show.clicked.connect(lambda: self._on_selected(lambda p: p.set_test_pattern(self.pattern.currentData()), "Pattern"))
        off = QtWidgets.QPushButton("Pattern off")
        off.clicked.connect(lambda: self._on_selected(lambda p: p.set_test_pattern(None), "Pattern off"))
        manual.add_layout(hbox(self.pattern, show, off, None))
        self.brightness = QtWidgets.QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(100)
        self.bright_label = QtWidgets.QLabel("100%")
        self.brightness.valueChanged.connect(lambda v: self.bright_label.setText(f"{v}%"))
        self.brightness.sliderReleased.connect(
            lambda: self._on_selected(lambda p: p.set_brightness(self.brightness.value()), "Brightness"))
        manual.add_layout(hbox(QtWidgets.QLabel("Brightness"), self.brightness, self.bright_label))
        self.history = QtWidgets.QPlainTextEdit()
        self.history.setReadOnly(True)
        manual.add(self.history)
        bottom.addWidget(manual, 1)

        cmds = Card("Command definitions")
        self.cmds = make_table(["Command", "Confidence", "Definition"])
        self.cmds.setWordWrap(True)
        cmds.add(self.cmds)
        cmds.add(muted("Green: matches the published Bitfocus Companion module source. Amber: the endpoint is known "
                       "but the value format is assumed. Red: assumed. Blue: your own. Test amber and red commands on "
                       "your hardware before a show."))
        self.edit_cmds = QtWidgets.QPushButton("Edit commands…")
        self.edit_cmds.setToolTip("Change this processor type's commands and test patterns. Built-in types are "
                                  "kept as they are: your changes are saved as a custom copy.")
        self.edit_cmds.clicked.connect(self._edit_commands)
        self.delete_custom = QtWidgets.QPushButton("Delete custom version")
        self.delete_custom.setToolTip("Delete this custom processor type; processors using it go back to the "
                                      "type it was based on")
        self.delete_custom.clicked.connect(self._delete_custom)
        cmds.add_layout(hbox(self.edit_cmds, self.delete_custom, None))
        bottom.addWidget(cmds, 1)
        lay.addLayout(bottom, 3)

    # ---- advanced: groups and individual control ---------------------------------------
    def _advanced_card(self) -> QtWidgets.QWidget:
        card = Card()
        self.adv_toggle = QtWidgets.QToolButton()
        self.adv_toggle.setText("ADVANCED: GROUPS AND INDIVIDUAL CONTROL")
        self.adv_toggle.setObjectName("CardTitle")
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.adv_toggle.setArrowType(Qt.RightArrow)
        self.adv_toggle.setStyleSheet("QToolButton { border: none; background: transparent; font-weight: 700; }")
        self.adv_toggle.toggled.connect(self._toggle_advanced)
        card.add(self.adv_toggle)
        self.adv_body = QtWidgets.QWidget()
        self.adv_body.setObjectName("PlainRow")
        body = QtWidgets.QVBoxLayout(self.adv_body)
        body.setContentsMargins(0, 0, 0, 0)
        new_group = QtWidgets.QPushButton("+ New group")
        new_group.setToolTip("A group of LED processors that you can freeze, black out... together")
        new_group.clicked.connect(lambda: self._edit_group(None))
        body.addLayout(hbox(new_group, None))
        self.adv_rows = QtWidgets.QVBoxLayout()
        self.adv_rows.setSpacing(6)
        body.addLayout(self.adv_rows)
        body.addWidget(muted("Each button puts that group or processor into the mode (pressing a lit one returns "
                             "it to normal). The LED Processors page shows the same as columns."))
        body.addStretch(1)
        card.add(self.adv_body)
        self.adv_body.hide()
        self._adv_key = None
        self._adv_strips = []
        return card

    def _toggle_advanced(self, on: bool) -> None:
        self.adv_toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.adv_body.setVisible(on)
        self._adv_key = None
        self.refresh()

    def _edit_group(self, group) -> None:
        if edit_led_group(self, self.ctx.config, group):
            self.save_config()
            self.ctx.bus.publish("led.groups_changed")
            self._adv_key = None
            self.refresh()

    def _fill_advanced(self) -> None:
        if not self.adv_body.isVisible():
            return
        cfg = self.ctx.config
        key = ([(g.name, g.color, tuple(g.processors)) for g in cfg.led_groups], [p.name for p in cfg.led_processors])
        if key != self._adv_key:
            self._adv_key = key
            while self.adv_rows.count():
                item = self.adv_rows.takeAt(0)
                w = item.widget()
                if w:
                    w.hide()
                    w.deleteLater()
            self._adv_strips = []
            for g in cfg.led_groups:
                name = QtWidgets.QLabel(f"  {g.name}  ")
                name.setStyleSheet(f"background: {g.color}; color: white; border-radius: 9px; font-weight: 700;")
                name.setToolTip(", ".join(g.processors) or "No processors")
                edit = QtWidgets.QPushButton("Edit")
                edit.clicked.connect(lambda _=False, g=g: self._edit_group(g))
                self._add_adv_row(name, g.name, edit)
            for p in cfg.led_processors:
                self._add_adv_row(QtWidgets.QLabel(p.name), p.name, None)
            if not cfg.led_processors:
                self.adv_rows.addWidget(muted("Add LED processors first."))
        for strip in self._adv_strips:
            strip.refresh()

    def _add_adv_row(self, label: QtWidgets.QWidget, target: str, extra) -> None:
        row = QtWidgets.QWidget()
        row.setObjectName("PlainRow")
        lay = QtWidgets.QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        label.setMinimumWidth(110)
        lay.addWidget(label)
        strip = ModeButtons(self, target, compact=True)
        self._adv_strips.append(strip)
        lay.addWidget(strip, 1)
        if extra is not None:
            lay.addWidget(extra)
        self.adv_rows.addWidget(row)

    # ---- editing --------------------------------------------------------------------
    def _profile_choices(self):
        return [(p["name"], p["id"]) for p in self.ctx.led.registry.all()]

    def _kwargs(self):
        return dict(choices={"profile_id": self._profile_choices},
                    labels={"profile_id": "Processor type", "host": "IP address / host",
                            "port": "Port (0 = default)", "options": "Options (JSON)",
                            "command_overrides": "Command overrides (JSON)"})

    def _names(self):
        return [p.name for p in self.ctx.config.led_processors]

    def _add(self) -> None:
        cfg = LEDProcessorConfig(name=f"Processor {len(self.ctx.config.led_processors) + 1}")
        if edit_dataclass(self, "Add LED processor", cfg, unique_name_validator(self._names(), ""), **self._kwargs()):
            self.ctx.config.led_processors.append(cfg)
            self._changed()

    def _selected_cfg(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return self.ctx.config.led_processors[rows[0]] if rows and rows[0] < len(self.ctx.config.led_processors) else None

    def _edit(self) -> None:
        cfg = self._selected_cfg()
        if cfg and edit_dataclass(self, f"Edit {cfg.name}", cfg, unique_name_validator(self._names(), cfg.name),
                                  **self._kwargs()):
            self._changed()

    def _remove(self) -> None:
        cfg = self._selected_cfg()
        if cfg:
            self.ctx.config.led_processors.remove(cfg)
            self._changed()

    def _changed(self) -> None:
        self.ctx.led.reload()
        self.save_config()
        self.refresh()

    def _edit_commands(self) -> None:
        cfg = self._selected_cfg()
        if cfg is None:
            self.notify("Select a processor first", False)
            return
        if self.ctx.led.registry.get(cfg.profile_id) is None:
            self.notify(f"Unknown processor type {cfg.profile_id}", False)
            return
        saved = edit_led_commands(self, self.ctx.led.registry, cfg.profile_id)
        if not saved:
            return
        was = cfg.profile_id
        cfg.profile_id = saved["id"]
        self._changed()
        self.notify(f"{cfg.name} now uses {saved['name']}" if was != saved["id"] else f"Saved {saved['name']}")

    def _delete_custom(self) -> None:
        cfg = self._selected_cfg()
        registry = self.ctx.led.registry
        if cfg is None or registry.is_builtin(cfg.profile_id) or registry.get(cfg.profile_id) is None:
            return
        profile = registry.get(cfg.profile_id)
        users = [c for c in self.ctx.config.led_processors if c.profile_id == cfg.profile_id]
        based_on = profile.get("based_on")
        if QtWidgets.QMessageBox.question(
                self, "Delete custom version",
                f"Delete {profile.get('name')}?\n\n"
                + (f"{', '.join(c.name for c in users)} will use "
                   f"{(registry.get(based_on) or {}).get('name', based_on)} again." if based_on else
                   "Processors using it will need another type.")) != QtWidgets.QMessageBox.Yes:
            return
        try:
            registry.delete_custom(cfg.profile_id)
        except OSError as exc:
            self.notify(f"Could not delete: {exc}", False)
            return
        if based_on:
            for c in users:
                c.profile_id = based_on
        self._changed()

    def _processor(self):
        cfg = self._selected_cfg()
        return self.ctx.led.get(cfg.name) if cfg else None

    def _on_selected(self, fn, label) -> None:
        p = self._processor()
        if p is None:
            self.notify("Select a processor first", False)
            return

        def done(_):
            self.notify(f"{p.name}: {label}")
            self.ctx.led._publish()
        self.background(lambda: fn(p), done, f"{p.name} {label}")

    def _test(self) -> None:
        p = self._processor()
        if p:
            self.background(p.check_connection, lambda ok: (self.notify(
                f"{p.name}: {'reachable' if ok else 'unreachable - ' + p.state.last_error}", ok), self.refresh()))

    def _test_all(self) -> None:
        self.background(self.ctx.led.check_all, lambda res: (self.notify(
            ", ".join(f"{k}: {'OK' if v else 'FAIL'}" for k, v in res.items()) or "No processors"), self.refresh()))

    # ---- display ----------------------------------------------------------------------
    def _show_details(self) -> None:
        p = self._processor()
        self.cmds.setRowCount(0)
        self.pattern.clear()
        cfg = self._selected_cfg()
        custom = bool(cfg and cfg.profile_id and not self.ctx.led.registry.is_builtin(cfg.profile_id)
                      and self.ctx.led.registry.get(cfg.profile_id))
        self.edit_cmds.setEnabled(cfg is not None)
        self.delete_custom.setVisible(custom)
        if p is None:
            self.notes.setText("Select a processor to control it individually.")
            self.history.clear()
            if self.selected_modes is not None:
                self.selected_modes.hide()
            return
        if self.selected_modes is None or self.selected_modes.target != p.name:
            if self.selected_modes is not None:
                self.selected_modes.hide()
                self.selected_modes.deleteLater()
            self.selected_modes = ModeButtons(self, p.name, modes=("frozen", "blackout", "locate", "normal"))
            self.selected_modes_holder.addWidget(self.selected_modes)
        self.selected_modes.show()
        self.selected_modes.refresh()
        lit = p.mode == "pattern"
        self.show_pattern.setStyleSheet("QPushButton { background: #fab005; color: #111; border-color: #ffffff; "
                                        "font-weight: 800; }" if lit else "")
        prof = p.profile
        self.notes.setText(f"<b>{prof.get('name')}</b> · {prof.get('transport', '').upper()} port {p.port}<br>"
                           f"{prof.get('notes', '')}")
        self.notes.setTextFormat(Qt.RichText)
        for t in p.test_patterns:
            self.pattern.addItem(t.get("label", t["id"]), t["id"])
        self.history.setPlainText("\n".join(p.state.history))
        names = COMMANDS + [f"pattern:{t['id']}" for t in p.test_patterns]
        for i, name in enumerate(names):
            if name.startswith("pattern:"):
                pattern = next(t for t in p.test_patterns if t["id"] == name[8:])
                cmd = (pattern.get("commands") or [{}])[0]
                conf, definition = cmd.get("confidence", "assumed"), str(pattern.get("commands"))
            else:
                conf = p.confidence(name) if p.has(name) else ""
                definition = p.describe_command(name).replace("\n", " ")
            set_row(self.cmds, i, [name, CONFIDENCE_LABELS.get(conf, "not supported") if conf else "not supported",
                                   definition])
            if conf in CONFIDENCE_COLORS:
                self.cmds.item(i, 1).setForeground(QtGui.QColor(CONFIDENCE_COLORS[conf]))

    def refresh(self) -> None:
        sel = self._selected_cfg()
        self.table.setRowCount(0)
        for i, cfg in enumerate(self.ctx.config.led_processors):
            p = self.ctx.led.get(cfg.name)
            prof = self.ctx.led.registry.get(cfg.profile_id) or {}
            status = "unknown" if not p or p.state.online is None else ("online" if p.state.online else "OFFLINE")
            port = cfg.port or prof.get("default_port", "")
            set_row(self.table, i, [cfg.name, prof.get("name", cfg.profile_id), f"{cfg.host}:{port}",
                                    "yes" if cfg.enabled else "no", status] + [""] * len(ROW_MODES) +
                    [(p.state.last_error or p.state.last_command) if p else ""])
            for col, (_label, mode) in enumerate(ROW_MODES, start=5):
                strip = ModeButtons(self, cfg.name, compact=True, modes=(mode,))
                strip.refresh()
                self.table.setCellWidget(i, col, strip)
        if sel in self.ctx.config.led_processors:
            self.table.selectRow(self.ctx.config.led_processors.index(sel))
        self._show_details()
        self._fill_advanced()

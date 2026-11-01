"""LED processor configuration: choose the processor type, set its address, test and fine-tune commands."""
from __future__ import annotations

from operator_board.core.config import LEDProcessorConfig
from operator_board.led.profiles import CONFIDENCE_LABELS
from operator_board.qt import QtGui, QtWidgets, Qt
from operator_board.ui.forms import edit_dataclass, unique_name_validator
from operator_board.ui.led_commands import edit_led_commands
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, make_table, muted, set_row

CONFIDENCE_COLORS = {"source": "#2f9e44", "partial": "#f08c00", "assumed": "#e03131", "user": "#3d8bfd"}
COMMANDS = ["freeze_on", "freeze_off", "blackout_on", "blackout_off", "test_pattern_off", "brightness"]


class LEDSetupPage(BasePage):
    title = "LED Processors"
    refresh_topics = ("led.",)

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        card = Card("LED processors on this stage")
        self.table = make_table(["Name", "Processor type", "Address", "Enabled", "Status", "Last command / error"])
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
        lay.addWidget(card, 2)

        bottom = QtWidgets.QHBoxLayout()
        manual = Card("Selected processor")
        self.notes = muted("")
        manual.add(self.notes)
        row = []
        for label, fn in [("Freeze", lambda p: p.set_freeze(True)), ("Unfreeze", lambda p: p.set_freeze(False)),
                          ("Blackout", lambda p: p.set_blackout(True)), ("Restore", lambda p: p.set_blackout(False))]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(lambda _=False, fn=fn, label=label: self._on_selected(fn, label))
            row.append(b)
        manual.add_layout(hbox(*row, None))
        self.pattern = QtWidgets.QComboBox()
        show = QtWidgets.QPushButton("Show pattern")
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
            return
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
                                    "yes" if cfg.enabled else "no", status,
                                    (p.state.last_error or p.state.last_command) if p else ""])
        if sel in self.ctx.config.led_processors:
            self.table.selectRow(self.ctx.config.led_processors.index(sel))
        self._show_details()

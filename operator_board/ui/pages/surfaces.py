"""Control surface configuration (Stream Deck, MIDI, OSC, phone/tablet web panel, Companion) and an
on-screen virtual deck."""
from __future__ import annotations

import json
import secrets

from operator_board.qt import QtGui, QtWidgets, Qt
from operator_board.surfaces.layout import (KINDS, MODELS, ButtonBinding, SurfacePage, default_layout, load_layout,
                                            save_layout)
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, local_addresses, make_searchable, muted

KIND_HINTS = {
    "streamdeck": "Plugged into this PC. Quit the Elgato Stream Deck app first: only one program can use the deck.",
    "web": "Open the web panel address below on the phone or tablet (same network). Works with any layout.",
    "osc": "In TouchOSC / Open Stage Control, send /operator/key &lt;n&gt; (0 = top-left) to this PC's UDP port "
           "(turn OSC on below). /operator/action/&lt;id&gt; runs any action; /operator/page next|prev.",
    "midi": "Plug the controller in and turn MIDI on below. Pads press the key in the same position; pad lights "
            "follow the keys where the controller supports it.",
    "custom": "Choose any size. Use it on the web panel, from OSC (/operator/key &lt;n&gt;), or with any MIDI "
              "controller (keys count up from the base note, bottom-left first).",
}


class SurfacesPage(BasePage):
    title = "Control Surfaces"
    refresh_topics = ("surface.", "led.", "takes.", "multiuser.", "action.")

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        self.selected = None
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)

        left = QtWidgets.QVBoxLayout()
        deck = Card("Layout")
        self.model = QtWidgets.QComboBox()
        for mid, (label, _c, _r) in MODELS.items():
            self.model.addItem(label, mid)
        make_searchable(self.model)
        self.model.currentIndexChanged.connect(self._model_changed)  # also fires for a typed search
        self.model.setMinimumContentsLength(28)
        self.model.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.custom_cols = QtWidgets.QLineEdit()
        self.custom_rows = QtWidgets.QLineEdit()
        self.base_note = QtWidgets.QLineEdit()
        for edit, top in ((self.custom_cols, 16), (self.custom_rows, 16), (self.base_note, 127)):
            edit.setValidator(QtGui.QIntValidator(0 if edit is self.base_note else 1, top, edit))
            edit.setFixedWidth(56)
            edit.editingFinished.connect(self._custom_changed)
        self.custom_row = QtWidgets.QWidget()
        custom_lay = hbox(QtWidgets.QLabel("Columns"), self.custom_cols, QtWidgets.QLabel("Rows"), self.custom_rows,
                          QtWidgets.QLabel("MIDI base note"), self.base_note, None)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        self.custom_row.setLayout(custom_lay)
        self.kind_hint = muted("")
        self.kind_hint.setTextFormat(Qt.RichText)
        self.page_combo = QtWidgets.QComboBox()
        self.page_combo.activated.connect(self._page_changed)
        add_page = QtWidgets.QPushButton("+ Page")
        add_page.clicked.connect(self._add_page)
        ren_page = QtWidgets.QPushButton("Rename")
        ren_page.clicked.connect(self._rename_page)
        del_page = QtWidgets.QPushButton("Delete page")
        del_page.clicked.connect(self._delete_page)
        self.page_combo.setMinimumContentsLength(10)
        self.page_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        device_row = hbox(QtWidgets.QLabel("Device"), self.model)
        device_row.setStretch(1, 1)
        deck.add_layout(device_row)
        deck.add(self.custom_row)
        deck.add(self.kind_hint)
        deck.add_layout(hbox(QtWidgets.QLabel("Page"), self.page_combo, add_page, ren_page, del_page, None))
        self.live = QtWidgets.QCheckBox("Live mode (pressing a key runs its action)")
        deck.add(self.live)
        self.grid_holder = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.grid_holder)
        self.grid.setSpacing(8)
        deck.add(self.grid_holder)
        files = []
        for label, fn in [("Save", self._save), ("Save as…", self._save_as), ("Load…", self._load),
                          ("Reset to default", self._reset)]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            files.append(b)
        deck.add_layout(hbox(*files, None))
        self.file_label = muted("")
        deck.add(self.file_label)
        left.addWidget(deck, 1)

        web = Card("Phone & tablet web panel")
        self.web_label = muted("")
        self.web_label.setTextFormat(Qt.RichText)
        self.web_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.web_label.setOpenExternalLinks(True)
        web.add(self.web_label)
        self.network_btn = QtWidgets.QPushButton()
        self.network_btn.clicked.connect(self._toggle_network)
        self.copy_btn = QtWidgets.QPushButton("Copy address")
        self.copy_btn.clicked.connect(self._copy_url)
        web.add_layout(hbox(self.network_btn, self.copy_btn, None))
        left.addWidget(web, 0)

        hw = Card("Hardware & integrations")
        self.osc_on = QtWidgets.QCheckBox("OSC (TouchOSC, Open Stage Control...) on UDP port")
        self.osc_port = QtWidgets.QLineEdit()
        self.osc_port.setValidator(QtGui.QIntValidator(1, 65535, self.osc_port))
        self.osc_port.setFixedWidth(70)
        self.osc_on.toggled.connect(self._backends_changed)
        self.osc_port.editingFinished.connect(self._backends_changed)
        hw.add_layout(hbox(self.osc_on, self.osc_port, None))
        self.midi_on = QtWidgets.QCheckBox("MIDI controller")
        self.midi_port = QtWidgets.QComboBox()
        self.midi_port.setMinimumContentsLength(18)
        self.midi_port.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.midi_on.toggled.connect(self._backends_changed)
        self.midi_port.activated.connect(self._backends_changed)
        midi_rescan = QtWidgets.QPushButton("↻")
        midi_rescan.setFixedWidth(40)
        midi_rescan.setToolTip("Look for MIDI controllers again")
        midi_rescan.clicked.connect(self._fill_midi_ports)
        hw.add_layout(hbox(self.midi_on, self.midi_port, midi_rescan, None))
        self.hw_label = muted("")
        self.hw_label.setTextFormat(Qt.RichText)
        self.hw_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hw.add(self.hw_label)
        left.addWidget(hw, 0)
        lay.addLayout(left, 3)

        edit = Card("Key binding")
        form = QtWidgets.QFormLayout()
        self.action = QtWidgets.QComboBox()
        # Size by a fixed length, not by the longest action name, so the page can shrink.
        self.action.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.action.setMinimumContentsLength(18)
        self.action.addItem("(none)", "")
        for a in ctx.actions.all():
            self.action.addItem(f"{a.category} › {a.label}", a.id)
        self.action.currentIndexChanged.connect(self._action_changed)
        self.label = QtWidgets.QLineEdit()
        self.params = QtWidgets.QLineEdit()
        self.params.setPlaceholderText('{"preset": "Camera 1"}')
        self.param_hint = muted("")
        self.color = QtWidgets.QLineEdit()
        self.active_color = QtWidgets.QLineEdit()
        form.addRow("Action", self.action)
        form.addRow("Label", self.label)
        form.addRow("Parameters (JSON)", self.params)
        form.addRow("", self.param_hint)
        form.addRow("Colour", self.color)
        form.addRow("Active colour", self.active_color)
        edit.add_layout(form)
        apply = QtWidgets.QPushButton("Apply to key")
        apply.clicked.connect(self._apply)
        clear = QtWidgets.QPushButton("Clear key")
        clear.clicked.connect(self._clear)
        edit.add_layout(hbox(apply, clear))
        edit.layout_.addStretch(1)
        lay.addWidget(edit, 2)

    # ---- layout helpers ------------------------------------------------------------
    @property
    def layout_obj(self):
        return self.ctx.surface.layout

    def _page(self) -> SurfacePage:
        return self.ctx.surface.current_page

    def _model_changed(self) -> None:
        data = self.model.currentData()
        if data and data != self.layout_obj.model:
            self.layout_obj.model = data
            self.ctx.bus.publish("surface.changed")
            self.refresh()

    def _custom_changed(self) -> None:
        lay = self.layout_obj
        for edit, attr in ((self.custom_cols, "custom_columns"), (self.custom_rows, "custom_rows"),
                           (self.base_note, "midi_base_note")):
            if edit.hasAcceptableInput():
                setattr(lay, attr, int(edit.text()))
        self.ctx.bus.publish("surface.changed")
        self.refresh()

    # ---- web panel, OSC, MIDI -----------------------------------------------------------
    def _panel_urls(self):
        s = self.ctx.config.surfaces
        port = self.ctx.api.port if self.ctx.api else s.api_port
        token = f"?token={s.api_token}" if s.api_token else ""
        hosts = local_addresses() if s.api_bind == "0.0.0.0" else [s.api_bind]
        return [f"http://{h}:{port}/panel{token}" for h in hosts]

    def _toggle_network(self) -> None:
        s = self.ctx.config.surfaces
        if s.api_bind == "0.0.0.0":
            s.api_bind = "127.0.0.1"
        else:
            if QtWidgets.QMessageBox.question(
                    self, "Phones and tablets",
                    "Let phones and tablets on this network open the web panel?\n\nAnyone with the address "
                    "(it includes a secret token) can press the buttons, so use it on the stage network only."
            ) != QtWidgets.QMessageBox.Yes:
                return
            s.api_bind = "0.0.0.0"
            if not s.api_token:
                s.api_token = secrets.token_urlsafe(9)
            s.api_server_enabled = True
        error = self.ctx.restart_api()
        self.save_config()
        if error:
            self.notify(error, False)
        self.refresh()

    def _copy_url(self) -> None:
        urls = self._panel_urls()
        if urls:
            QtWidgets.QApplication.clipboard().setText(urls[0])
            self.notify(f"Copied {urls[0]}")

    def _fill_midi_ports(self) -> None:
        from operator_board.surfaces.midi import input_names
        current = self.ctx.config.surfaces.midi_port
        self.midi_port.blockSignals(True)
        self.midi_port.clear()
        self.midi_port.addItem("(first MIDI input found)", "")
        for name in input_names():
            self.midi_port.addItem(name, name)
        if current and self.midi_port.findData(current) < 0:
            self.midi_port.addItem(current + " (not connected)", current)
        self.midi_port.setCurrentIndex(max(0, self.midi_port.findData(current)))
        self.midi_port.blockSignals(False)

    def _backends_changed(self, *_args) -> None:
        s = self.ctx.config.surfaces
        s.osc_enabled = self.osc_on.isChecked()
        if self.osc_port.hasAcceptableInput():
            s.osc_port = int(self.osc_port.text())
        s.midi_enabled = self.midi_on.isChecked()
        s.midi_port = self.midi_port.currentData() or ""
        self.ctx.start_surface_backends()
        self.save_config()
        self.refresh()

    def _page_changed(self, index: int) -> None:
        self.ctx.surface.page = index
        self.selected = None
        self.refresh()

    def _add_page(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New page", "Page name:")
        if ok and name:
            self.layout_obj.pages.append(SurfacePage(name=name))
            self.ctx.surface.page = len(self.layout_obj.pages) - 1
            self.refresh()

    def _rename_page(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename page", "Page name:", text=self._page().name)
        if ok and name:
            self._page().name = name
            self.refresh()

    def _delete_page(self) -> None:
        if len(self.layout_obj.pages) > 1:
            self.layout_obj.pages.pop(self.ctx.surface.page)
            self.ctx.surface.page = 0
            self.refresh()

    def _key_clicked(self, index: int) -> None:
        if self.live.isChecked():
            self.background(lambda: self.ctx.surface.press(index),
                            lambda r: (self.notify(r.message, r.ok), self.refresh()), "Key")
            return
        self.selected = index
        b = self._page().button(index) or ButtonBinding()
        self.action.setCurrentIndex(max(0, self.action.findData(b.action)))
        self.label.setText(b.label)
        self.params.setText(json.dumps(b.params) if b.params else "")
        self.color.setText(b.color)
        self.active_color.setText(b.active_color)
        self.refresh()

    def _action_changed(self) -> None:
        action = self.ctx.actions.get(self.action.currentData() or "")
        if action and action.params:
            hints = []
            for p in action.params:
                choices = p.choices()
                hints.append(p.name + (f" ∈ {choices[:6]}" if choices else f" ({p.kind})"))
            self.param_hint.setText("Parameters: " + "; ".join(hints))
        else:
            self.param_hint.setText("No parameters")

    def _apply(self) -> None:
        if self.selected is None:
            self.notify("Click a key on the deck first", False)
            return
        try:
            params = json.loads(self.params.text()) if self.params.text().strip() else {}
        except ValueError as exc:
            self.notify(f"Parameters must be JSON: {exc}", False)
            return
        action_id = self.action.currentData() or ""
        action = self.ctx.actions.get(action_id)
        self._page().buttons[str(self.selected)] = ButtonBinding(
            action=action_id, label=self.label.text() or (action.label if action else ""), params=params,
            color=self.color.text() or "#2b2f36", active_color=self.active_color.text() or "#d9480f")
        self.ctx.bus.publish("surface.changed")

    def _clear(self) -> None:
        if self.selected is not None:
            self._page().buttons.pop(str(self.selected), None)
            self.ctx.bus.publish("surface.changed")

    # ---- files ------------------------------------------------------------------------
    def _save(self) -> None:
        path = self.ctx.config.surfaces.layout_path
        if not path:
            return self._save_as()
        save_layout(self.layout_obj, path)
        self.notify(f"Saved layout to {path}")

    def _save_as(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save surface layout", "operator_board_surface.json",
                                                        "Surface layout (*.json)")
        if path:
            save_layout(self.layout_obj, path)
            self.ctx.config.surfaces.layout_path = path
            self.save_config()
            self.notify(f"Saved layout to {path}")
            self.refresh()

    def _load(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load surface layout", "", "Surface layout (*.json)")
        if not path:
            return
        try:
            layout = load_layout(path)
        except (OSError, ValueError, TypeError) as exc:
            self.notify(f"Could not load layout: {exc}", False)
            return
        self.ctx.surface.set_layout(layout)
        self.ctx.config.surfaces.layout_path = path
        self.save_config()
        self.refresh()

    def _reset(self) -> None:
        if QtWidgets.QMessageBox.question(self, "Reset", "Replace the current layout with the default?") \
                == QtWidgets.QMessageBox.Yes:
            self.ctx.surface.set_layout(default_layout())
            self.ctx.config.surfaces.layout_path = ""
            self.save_config()
            self.refresh()

    # ---- display ----------------------------------------------------------------------
    def refresh(self) -> None:
        lay = self.layout_obj
        self.model.setCurrentIndex(max(0, self.model.findData(lay.model)))
        kind = KINDS.get(lay.model, "streamdeck")
        self.custom_row.setVisible(lay.model == "custom")
        for edit, value in ((self.custom_cols, lay.custom_columns), (self.custom_rows, lay.custom_rows),
                            (self.base_note, lay.midi_base_note)):
            if not edit.hasFocus():
                edit.setText(str(value))
        self.kind_hint.setText(KIND_HINTS.get(kind, ""))
        self.page_combo.clear()
        self.page_combo.addItems([p.name for p in lay.pages])
        self.page_combo.setCurrentIndex(self.ctx.surface.page)
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.hide()  # deleteLater is deferred; hide now so old keys never show behind new ones
                w.deleteLater()
        key = 96 if lay.columns <= 5 else max(44, 560 // lay.columns)  # big surfaces still fit
        # Reserve the keys' room so the cards below cannot squash them (the page scrolls instead).
        self.grid_holder.setMinimumSize(lay.columns * (key + 8) + 8, lay.rows * (key + 8) + 8)
        for i in range(lay.key_count):
            b = self._page().button(i)
            active = self.ctx.surface.is_active(i)
            btn = QtWidgets.QPushButton((b.label if b else "") or "")
            btn.setFixedSize(key, key)
            bg = (b.active_color if active else b.color) if b and b.action else "#16181c"
            border = "#ffffff" if i == self.selected else "#30353d"
            btn.setStyleSheet(f"QPushButton {{ background: {bg}; border: 2px solid {border}; border-radius: 10px;"
                              " font-weight: 700; font-size: 12px; }")
            btn.setToolTip(b.action if b else "unassigned")
            btn.clicked.connect(lambda _=False, i=i: self._key_clicked(i))
            self.grid.addWidget(btn, i // lay.columns, i % lay.columns)
        self.file_label.setText(f"Layout file: {self.ctx.config.surfaces.layout_path or '(built-in default)'}")
        s = self.ctx.config.surfaces
        from operator_board.surfaces.streamdeck import AVAILABLE
        decks = len(self.ctx.streamdeck.decks) if self.ctx.streamdeck else 0
        api = self.ctx.api
        base = f"http://{s.api_bind if s.api_bind != '0.0.0.0' else '<this-pc-ip>'}:{api.port if api else s.api_port}"
        token = f"?token={s.api_token}" if s.api_token else ""
        lan = s.api_bind == "0.0.0.0"
        urls = self._panel_urls()
        if not api:
            self.web_label.setText("The web panel needs the HTTP API, which is off (Settings &gt; Control surfaces).")
        else:
            links = "<br>".join(f"<a href='{u}' style='color:#4dabf7'>{u}</a>" for u in urls) or "(no network found)"
            self.web_label.setText(
                ("Open on a phone or tablet on this network:<br>" if lan else
                 "Only this PC can open it now (allow phones and tablets to use it from other devices):<br>")
                + links + "<br>It shows the current layout; buttons work like Stream Deck keys.")
        self.network_btn.setText("Only this PC" if lan else "Allow phones and tablets on this network")
        self.copy_btn.setEnabled(bool(api and urls))
        for box, value in ((self.osc_on, s.osc_enabled), (self.midi_on, s.midi_enabled)):
            if box.isChecked() != value:
                box.blockSignals(True)
                box.setChecked(value)
                box.blockSignals(False)
        if not self.osc_port.hasFocus():
            self.osc_port.setText(str(s.osc_port))
        if self.midi_port.count() == 0:
            self._fill_midi_ports()
        from operator_board.surfaces.midi import AVAILABLE as MIDI_OK
        osc = self.ctx.osc
        midi = self.ctx.midi
        osc_text = (f"listening on UDP {osc.port}" if osc else
                    ("could not open the port (in use?)" if s.osc_enabled else "off"))
        midi_text = (f"connected: {midi.opened}" if midi else
                     ("library not installed - pip install mido python-rtmidi" if not MIDI_OK else
                      ("no matching MIDI controller found" if s.midi_enabled else "off")))
        self.hw_label.setText(
            f"<b>OSC:</b> {osc_text}<br><b>MIDI:</b> {midi_text}<br>"
            f"<b>Elgato Stream Deck (direct):</b> "
            f"{'%d connected' % decks if AVAILABLE else 'library not installed - pip install streamdeck pillow'}<br>"
            f"<b>Bitfocus Companion / HTTP API:</b> {'running at ' + base if api else 'disabled (see Settings)'}<br>"
            f"Companion 'Generic HTTP' button URL examples:<br>"
            f"<code>{base}/api/action/led.freeze{token}</code><br>"
            f"<code>{base}/api/action/camera.recall?preset=Camera%201{'&token=' + s.api_token if s.api_token else ''}</code><br>"
            f"<code>{base}/api/state{token}</code> (feedback)")

"""Main operator dashboard: LED wall, Unreal instances, multi-user, level, takes and cameras at a glance."""
from __future__ import annotations

from operator_board.qt import QtCore, QtGui, QtWidgets, Qt
from operator_board.ui.pages.base import BasePage
from operator_board.ui.theme import COLORS
from operator_board.ui.widgets import (Banner, BigButton, Card, HazardOverlay, RecordButton, StateDot, clear_layout,
                                       hbox, muted, scroll)


def _restyle(label: QtWidgets.QLabel, css: str) -> None:
    if label.styleSheet() != css:  # restyling re-polishes the widget; the timing labels update 10x a second
        label.setStyleSheet(css)


class DeviceTile(QtWidgets.QFrame):
    def __init__(self, page: "OperatePage", name: str) -> None:
        super().__init__()
        self.page, self.name = page, name
        self.setStyleSheet(f"QFrame {{ background: {COLORS['panel2']}; border-radius: 8px; }}")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        self.dot = StateDot()
        self.label = QtWidgets.QLabel(name)
        self.label.setStyleSheet("font-weight: 700;")
        self.state = muted("")
        self.state.setWordWrap(True)
        self.link = QtWidgets.QPushButton("Connect")
        self.link.setFixedWidth(120)
        self.link.setToolTip("Connect to or disconnect from this machine's SwitchboardListener. "
                             "Disconnecting does not stop Unreal.")
        self.link.clicked.connect(self._toggle_link)
        self.btn = QtWidgets.QPushButton("Start")
        self.btn.setFixedWidth(80)
        self.btn.clicked.connect(self._toggle)
        lay.addWidget(self.dot)
        lay.addWidget(self.label)
        lay.addWidget(self.state, 1)
        lay.addWidget(self.link)
        lay.addWidget(self.btn)

    def update_from(self, rt) -> None:
        self.dot.set_state(rt.state)
        role = "nDisplay " + (rt.config.ndisplay_node or "") if rt.config.role == "ndisplay" else "Editor"
        self.state.setText(f"{role} · {rt.state}" + (f" · {rt.last_error}" if rt.state == "error" else ""))
        self.btn.setText("Stop" if rt.state in ("running", "starting") else "Start")
        connected = rt.client is not None and rt.client.connected
        self.link.setEnabled(rt.state != "connecting")
        self.link.setText("Connecting…" if rt.state == "connecting" else "Disconnect" if connected else "Connect")

    def _toggle_link(self) -> None:
        rt = self.page.ctx.devices.get(self.name)
        connected = rt is not None and rt.client is not None and rt.client.connected
        self.page.invoke("devices.disconnect" if connected else "devices.connect", device=self.name)

    def _toggle(self) -> None:
        rt = self.page.ctx.devices.get(self.name)
        action = "devices.stop" if rt and rt.state in ("running", "starting") else "devices.start"
        self.page.invoke(action, device=self.name)


class OperatePage(BasePage):
    title = "Operate"
    refresh_topics = ("led.", "device.", "devices.", "multiuser.", "takes.", "levels.", "project.", "cameras.",
                      "config.")

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        root = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(root)
        grid.setSpacing(14)
        grid.setContentsMargins(16, 16, 16, 16)

        self.banner = Banner()
        grid.addWidget(self.banner, 0, 0, 1, 3)

        # --- LED wall ---------------------------------------------------------
        wall = Card("LED Wall")
        self.freeze_btn = BigButton("FREEZE", COLORS["freeze"], height=110)
        self.blackout_btn = BigButton("BLACKOUT", COLORS["blackout"], height=110)
        self.normal_btn = BigButton("WALL NORMAL", COLORS["ok"], height=110)
        self.freeze_btn.clicked.connect(lambda: self.invoke("led.freeze"))
        self.blackout_btn.clicked.connect(lambda: self.invoke("led.blackout"))
        self.normal_btn.clicked.connect(lambda: self.invoke("led.restore"))
        wall.add_layout(hbox(self.freeze_btn, self.blackout_btn, self.normal_btn, spacing=12))
        self.pattern_combo = QtWidgets.QComboBox()
        self.pattern_btn = QtWidgets.QPushButton("Show test pattern")
        self.pattern_off_btn = QtWidgets.QPushButton("Pattern off")
        self.pattern_btn.clicked.connect(lambda: self.invoke("led.test_pattern", pattern=self.pattern_combo.currentData()))
        self.pattern_off_btn.clicked.connect(lambda: self.invoke("led.test_pattern_off"))
        wall.add_layout(hbox(QtWidgets.QLabel("Test pattern"), self.pattern_combo, self.pattern_btn,
                             self.pattern_off_btn, None))
        self.proc_row = QtWidgets.QHBoxLayout()
        wall.add_layout(self.proc_row)
        grid.addWidget(wall, 1, 0, 1, 3)
        self.wall_controls = [self.freeze_btn, self.blackout_btn, self.normal_btn, self.pattern_combo,
                              self.pattern_btn, self.pattern_off_btn]
        self.wall_overlay = HazardOverlay(wall)
        self._wall_state = None      # None (usable), "none" (no processor) or "offline:<names>"
        self._wall_dismissed = None  # a state the operator chose to "use anyway"

        # --- Unreal instances ---------------------------------------------------
        inst = Card("Unreal Instances")
        connect_all = QtWidgets.QPushButton("Connect all")
        disconnect_all = QtWidgets.QPushButton("Disconnect all")
        disconnect_all.setToolTip("Close every listener connection. Unreal keeps running.")
        start_all = QtWidgets.QPushButton("▶ Start all")
        stop_all = QtWidgets.QPushButton("■ Stop all")
        connect_all.clicked.connect(lambda: self.invoke("devices.connect_all"))
        disconnect_all.clicked.connect(lambda: self.invoke("devices.disconnect_all"))
        start_all.clicked.connect(lambda: self.invoke("devices.start_all"))
        stop_all.clicked.connect(lambda: self.invoke("devices.stop_all"))
        for b in (connect_all, disconnect_all, start_all, stop_all):
            b.setMinimumHeight(44)
        inst.add_layout(hbox(connect_all, disconnect_all, start_all, stop_all))
        self.groups_box = QtWidgets.QVBoxLayout()
        inst.add_layout(self.groups_box)
        self.devices_box = QtWidgets.QVBoxLayout()
        inst.add_layout(self.devices_box)
        self.no_devices = muted("No devices configured yet - add them on the Devices page.")
        inst.add(self.no_devices)
        inst.layout_.addStretch(1)
        self.tiles = {}
        self._sig = {}
        self._chips = {}
        self._group_labels = {}
        grid.addWidget(inst, 2, 0, 2, 2)

        # --- Multi-user -----------------------------------------------------------
        mu = Card("Multi-User")
        self.mu_server = QtWidgets.QLineEdit()
        self.mu_session = QtWidgets.QLineEdit()
        form = QtWidgets.QFormLayout()
        form.addRow("Server name", self.mu_server)
        form.addRow("Session", self.mu_session)
        mu.add_layout(form)
        self.mu_btn = BigButton("START SERVER", COLORS["mu"], height=60)
        self.mu_btn.clicked.connect(self._toggle_mu)
        mu.add(self.mu_btn)
        self.mu_auto = QtWidgets.QCheckBox("Auto start multi-user")
        self.mu_auto.setToolTip("Start the multi-user server first whenever an editor or nDisplay node that "
                                "joins multi-user is started")
        self.mu_auto.toggled.connect(self._auto_mu_changed)
        mu.add(self.mu_auto)
        self.mu_count = QtWidgets.QLabel("0")
        self.mu_count.setObjectName("Big")
        mu.add_layout(hbox(self.mu_count, muted("connected clients"), None))
        self.mu_clients = muted("")
        mu.add(self.mu_clients)
        self.mu_warning = QtWidgets.QLabel()
        self.mu_warning.setWordWrap(True)
        self.mu_warning.setStyleSheet(f"color: {COLORS['warn']}; font-weight: 600;")
        self.mu_warning.hide()
        mu.add(self.mu_warning)
        mu.layout_.addStretch(1)
        grid.addWidget(mu, 2, 2)

        # --- Level ----------------------------------------------------------------
        lvl = Card("Project & Level")
        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.activated.connect(self._switch_project)
        self.level_combo = QtWidgets.QComboBox()
        self.level_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.level_combo.currentIndexChanged.connect(self._level_selected)
        load = QtWidgets.QPushButton("Load level")
        load.setMinimumHeight(40)
        load.clicked.connect(lambda: self.invoke("level.load", level=self.level_combo.currentData() or ""))
        refresh = QtWidgets.QPushButton("↻")
        refresh.setFixedWidth(40)
        refresh.clicked.connect(self._refresh_levels)
        form = QtWidgets.QFormLayout()
        form.addRow("Project", self.project_combo)
        form.addRow("Level", hbox(self.level_combo, refresh))
        lvl.add_layout(form)
        lvl.add(load)
        grid.addWidget(lvl, 3, 2)

        # --- Takes ----------------------------------------------------------------
        takes = Card("Take")
        self.take_label = QtWidgets.QLabel("")
        self.take_label.setObjectName("Big")
        self.rec_btn = RecordButton(ctx.takes.countdown_remaining, height=60)
        self.rec_btn.clicked.connect(lambda: self.invoke("takes.toggle_record"))
        self.save_take_btn = QtWidgets.QPushButton("Save last take")
        self.save_take_btn.setToolTip("Save the last recorded take in the editor. In a multi-user session it is also "
                                      "written into the project on the editor's PC, without sending anything more "
                                      "to the other machines or the wall.")
        self.save_take_btn.clicked.connect(lambda: self.invoke("takes.save_last"))
        self.scene_edit = QtWidgets.QLineEdit()
        self.scene_edit.setPlaceholderText("Scene name")
        self.scene_edit.setToolTip("Scene (slate) name. Press Enter to apply; a new scene starts at its next "
                                   "unused take.")
        self.scene_edit.editingFinished.connect(self._scene_changed)
        self.take_edit = QtWidgets.QLineEdit()  # not a QSpinBox: styled spin boxes clip their text
        self.take_edit.setValidator(QtGui.QIntValidator(1, 9999, self.take_edit))
        self.take_edit.setAlignment(Qt.AlignCenter)
        self.take_edit.setToolTip("Take number: type any number and press Enter")
        self.take_edit.editingFinished.connect(self._take_changed)
        minus = QtWidgets.QPushButton("Take −")
        plus = QtWidgets.QPushButton("Take +")
        minus.clicked.connect(lambda: self.invoke("takes.prev_take"))
        plus.clicked.connect(lambda: self.invoke("takes.next_take"))
        self.take_controls = [self.scene_edit, self.take_edit, minus, plus]
        takes.add(self.take_label)
        scene_row = hbox(QtWidgets.QLabel("Scene"), self.scene_edit)
        scene_row.setStretch(1, 1)
        takes.add_layout(scene_row)
        take_row = hbox(minus, self.take_edit, plus)
        for i in range(3):
            take_row.setStretch(i, 1)
        takes.add_layout(take_row)
        takes.add(self.rec_btn)
        takes.add(self.save_take_btn)
        # Timecode from the primary editor and genlock from the listeners; "No data" when unknown.
        self.tc_value = QtWidgets.QLabel()
        self.tc_value.setObjectName("Timecode")
        self.tc_value.setToolTip("The primary editor's engine timecode (read over Remote Control every second)")
        self.tc_rate = muted("")
        self.genlock_value = QtWidgets.QLabel()
        self.genlock_value.setWordWrap(True)
        self.genlock_value.setToolTip("Genlock from each running machine's NVIDIA sync board (as Switchboard shows "
                                      "it), else the editor's custom time step")
        timing = QtWidgets.QFormLayout()
        timing.addRow("Timecode", hbox(self.tc_value, self.tc_rate, None))
        timing.addRow("Genlock", self.genlock_value)
        takes.add_layout(timing)
        grid.addWidget(takes, 4, 0)
        self._timing_timer = QtCore.QTimer(self)
        self._timing_timer.timeout.connect(self._update_timing)
        self._timing_timer.start(100)
        self._update_timing()

        # --- Cameras --------------------------------------------------------------
        cams = Card("Camera Positions")
        self.cam_box = QtWidgets.QGridLayout()
        cams.add_layout(self.cam_box)
        cams.layout_.addStretch(1)
        grid.addWidget(cams, 4, 1, 1, 2)

        for col in range(3):
            grid.setColumnStretch(col, 1)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(root))

    # ---- interactions ------------------------------------------------------------
    def _toggle_mu(self) -> None:
        if self.ctx.multiuser.running:
            self.invoke("multiuser.stop")
        else:
            self.invoke("multiuser.start", server_name=self.mu_server.text().strip(),
                        session_name=self.mu_session.text().strip())

    # ---- LED wall availability ------------------------------------------------------
    def _wall_unavailable(self):
        """None if the wall controls can be used, else a state key for the overlay."""
        active = self.ctx.led.active()
        if not active:
            return "none"
        if all(p.state.online is False for p in active):
            return "offline:" + ", ".join(p.name for p in active)
        return None

    def _update_wall_overlay(self) -> None:
        state = self._wall_unavailable()
        if state == self._wall_state:
            return
        self._wall_state = state
        blocked = state is not None and state != self._wall_dismissed
        for control in self.wall_controls:
            control.setEnabled(not blocked)
        if not blocked:
            self.wall_overlay.hide()
            return
        setup = ("Set up LED processors", self._open_led_setup)
        if state == "none":
            self.wall_overlay.show_message(
                "LED WALL CONTROL UNAVAILABLE",
                "No LED processor is set up. Add your processor (type and IP address) to use Freeze, "
                "Blackout and test patterns from here.", [setup])
        else:
            names = state.split(":", 1)[1]
            self.wall_overlay.show_message(
                "LED PROCESSORS NOT CONNECTED",
                f"{names} could not be reached. Check the network and the IP address and port.",
                [("Retry connection", self._retry_led), setup, ("Use anyway", self._use_wall_anyway)])

    def _open_led_setup(self) -> None:
        titles = [p.title for p in self.window_.pages]
        if "LED Processors" in titles:
            self.window_.nav.setCurrentRow(titles.index("LED Processors"))

    def _retry_led(self) -> None:
        self.background(self.ctx.led.check_all, lambda _r: self.refresh(), "LED connection check")

    def _use_wall_anyway(self) -> None:
        """Lift the overlay for this state; it comes back if the state changes."""
        self._wall_dismissed = self._wall_state
        self._wall_state = "dismissed"  # force the next update to re-evaluate
        self._update_wall_overlay()

    def _scene_changed(self) -> None:
        name = self.scene_edit.text().strip()
        if not name:
            self.scene_edit.setText(self.ctx.config.takes.slate)
        elif name != self.ctx.config.takes.slate:
            self.invoke("takes.set_slate", done=lambda _r: self.save_config(), slate=name)

    def _take_changed(self) -> None:
        text = self.take_edit.text().strip()
        if not text.isdigit() or int(text) < 1:
            self.take_edit.setText(str(self.ctx.config.takes.take))
        elif int(text) != self.ctx.config.takes.take:
            self.invoke("takes.set_take", done=lambda _r: self.save_config(), take=int(text))

    def _auto_mu_changed(self, on: bool) -> None:
        if self.ctx.config.multiuser.auto_start != on:
            self.ctx.config.multiuser.auto_start = on
            self.save_config()

    def _switch_project(self) -> None:
        name = self.project_combo.currentText()
        if name and name != self.ctx.config.active_project:
            self.invoke("project.switch", project=name)

    def _refresh_levels(self) -> None:
        self.background(self.ctx.refresh_levels, lambda _: self.refresh(), "Listing levels")

    def _level_selected(self, index: int) -> None:
        if 0 <= index < len(self.ctx.level_cache):
            self.ctx.selected_level = index

    # ---- refresh -----------------------------------------------------------------
    NO_DATA = "No data"

    def on_event(self, topic: str, payload) -> None:
        super().on_event(topic, payload)
        if topic == "multiuser.session_renamed" and payload.get("reason") == "exists":
            # Hard to miss on purpose: the operator may expect the previous session's work to be there.
            box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, "New multi-user session",
                                        payload.get("warning") or "", QtWidgets.QMessageBox.Ok, self)
            box.setInformativeText("Editors and nDisplay nodes started now join the new session. To carry on in "
                                   "the old one instead, type its name in the Session box and turn off "
                                   "'Number sessions' in Settings > Multi-user server.")
            box.setAttribute(Qt.WA_DeleteOnClose)
            box.open()

    def _update_timing(self) -> None:
        if not self.isVisible() and self.tc_value.text():
            return
        tc = self.ctx.timing.timecode()
        recording = self.ctx.takes.recording
        self.tc_value.setText(tc or self.NO_DATA)
        self.tc_rate.setText(self.ctx.timing.rate_text() if tc else "")
        if tc is None:
            _restyle(self.tc_value, f"color: {COLORS['muted']};")
        else:
            _restyle(self.tc_value, f"color: {COLORS['err'] if recording else COLORS['text']}; font-size: 22px; "
                                    "font-weight: 700; font-family: Consolas, 'Courier New', monospace;")
        text, ok = self.ctx.timing.genlock()
        colour = COLORS["muted"] if text is None else {True: COLORS["ok"], False: COLORS["err"]}.get(ok, COLORS["warn"])
        self.genlock_value.setText(text or self.NO_DATA)
        _restyle(self.genlock_value, f"color: {colour}; font-weight: {600 if text else 400};")

    def refresh(self) -> None:
        led = self.ctx.led
        self.freeze_btn.set_active(led.frozen)
        self.blackout_btn.set_active(led.blackout)
        if led.blackout:
            self.banner.show_state("WALL BLACKED OUT", COLORS["blackout"])
        elif led.frozen:
            self.banner.show_state("WALL FROZEN", COLORS["freeze"])
        elif led.test_pattern:
            self.banner.show_state(f"TEST PATTERN: {led.test_pattern.upper()}", "#a07400")
        else:
            self.banner.show_state("", None)
        patterns = led.all_test_patterns()
        if self._changed("patterns", patterns):
            current = self.pattern_combo.currentData()
            self.pattern_combo.clear()
            for pid, label in patterns:
                self.pattern_combo.addItem(label, pid)
            idx = self.pattern_combo.findData(current)
            if idx >= 0:
                self.pattern_combo.setCurrentIndex(idx)
        if self._changed("procs", [(p.name, p.config.enabled) for p in led.processors]):
            clear_layout(self.proc_row)
            self._chips = {}
            for p in led.processors:
                chip = self._chips[p.name] = QtWidgets.QLabel(f"  {p.name} · {p.profile.get('manufacturer', '')}  ")
                self.proc_row.addWidget(chip)
            if not led.processors:
                self.proc_row.addWidget(muted("No LED processors configured - add them on the LED Processors page."))
            self.proc_row.addStretch(1)
        for p in led.processors:
            chip, st = self._chips.get(p.name), p.state
            if chip is None:
                continue
            color = "#495057" if not p.config.enabled else ("#e03131" if st.online is False else
                                                             "#2f9e44" if st.online else "#3d8bfd")
            chip.setStyleSheet(f"background: {color}; border-radius: 10px; padding: 4px; color: white;")
            chip.setToolTip(st.last_error or st.last_command or "No commands sent yet")
        self._update_wall_overlay()

        # devices & groups
        devices = self.ctx.devices.devices()
        self.no_devices.setVisible(not devices)
        names = [d.name for d in devices]
        for name in list(self.tiles):
            if name not in names:
                tile = self.tiles.pop(name)
                tile.hide()
                tile.deleteLater()
        for rt in devices:
            tile = self.tiles.get(rt.name)
            if tile is None:
                tile = self.tiles[rt.name] = DeviceTile(self, rt.name)
                self.devices_box.addWidget(tile)
            tile.update_from(rt)
        groups = self.ctx.config.groups
        if self._changed("groups", [(g.name, g.color) for g in groups]):
            clear_layout(self.groups_box)
            self._group_labels = {}
            for g in groups:
                label = self._group_labels[g.name] = QtWidgets.QLabel()
                label.setStyleSheet(f"border-left: 4px solid {g.color}; padding-left: 8px;")
                start = QtWidgets.QPushButton("▶ Start group")
                stop = QtWidgets.QPushButton("■ Stop group")
                start.clicked.connect(lambda _=False, n=g.name: self.invoke("group.start", group=n))
                stop.clicked.connect(lambda _=False, n=g.name: self.invoke("group.stop", group=n))
                self.groups_box.addLayout(hbox(label, None, start, stop))
        for g in groups:
            members = self.ctx.devices.group_devices(g.name)
            running = sum(1 for m in members if m.state == "running")
            self._group_labels[g.name].setText(f"<b>{g.name}</b>  <span style='color:{COLORS['muted']}'>"
                                               f"{running}/{len(members)} running</span>")

        # multi-user
        mu = self.ctx.multiuser
        if not self.mu_server.hasFocus():
            self.mu_server.setText(self.ctx.config.multiuser.server_name)
        if not self.mu_session.hasFocus():
            self.mu_session.setText(self.ctx.config.multiuser.session_name)
        self.mu_btn.setText("STOP SERVER" if mu.running else "START SERVER")
        if self.mu_auto.isChecked() != self.ctx.config.multiuser.auto_start:
            self.mu_auto.blockSignals(True)
            self.mu_auto.setChecked(self.ctx.config.multiuser.auto_start)
            self.mu_auto.blockSignals(False)
        self.mu_btn.set_active(mu.running)
        self.mu_count.setText(str(mu.client_count) if mu.running else "–")
        if mu.running:
            self.mu_clients.setText(", ".join(mu.clients) or mu.last_error or "Waiting for clients to join")
        else:
            self.mu_clients.setText(mu.last_error or "Server not running")
        self.mu_warning.setText(f"⚠ {mu.session_warning}" if mu.session_warning else "")
        self.mu_warning.setVisible(bool(mu.session_warning))

        # project & levels
        projects = [p.name for p in self.ctx.config.projects]
        if self._changed("projects", projects) or self.project_combo.currentText() != self.ctx.config.active_project:
            self.project_combo.blockSignals(True)
            self.project_combo.clear()
            self.project_combo.addItems(projects)
            self.project_combo.setCurrentText(self.ctx.config.active_project)
            self.project_combo.blockSignals(False)
        if self._changed("levels", [l.path for l in self.ctx.level_cache]):
            self.level_combo.blockSignals(True)
            self.level_combo.clear()
            for lvl in self.ctx.level_cache:
                self.level_combo.addItem(lvl.name, lvl.path)
                self.level_combo.setItemData(self.level_combo.count() - 1, lvl.path, Qt.ToolTipRole)
            self.level_combo.blockSignals(False)
        if self.ctx.level_cache and self.level_combo.currentIndex() != self.ctx.selected_level:
            self.level_combo.blockSignals(True)
            self.level_combo.setCurrentIndex(self.ctx.selected_level)
            self.level_combo.blockSignals(False)

        # takes
        t = self.ctx.config.takes
        self.take_label.setText(f"{t.slate}  ·  T{t.take:03d}")
        if not self.scene_edit.hasFocus() and self.scene_edit.text() != t.slate:
            self.scene_edit.setText(t.slate)
        if not self.take_edit.hasFocus() and self.take_edit.text() != str(t.take):
            self.take_edit.setText(str(t.take))
        for control in self.take_controls:  # no renaming or renumbering mid-take
            control.setEnabled(not self.ctx.takes.recording)
        self.rec_btn.show_state(self.ctx.takes.recording, self.ctx.takes.counting_down)
        last = self.ctx.takes.log.takes[-1] if self.ctx.takes.log.takes else None
        self.save_take_btn.setEnabled(last is not None and not self.ctx.takes.recording
                                      and self.ctx.config.takes.record_with_take_recorder)
        self.save_take_btn.setText(f"Save last take ({last.slate} T{last.take:03d})" if last else "Save last take")

        # cameras
        if self._changed("cameras", [c.name for c in self.ctx.config.cameras]):
            clear_layout(self.cam_box)
            for i, cam in enumerate(self.ctx.config.cameras):
                b = QtWidgets.QPushButton(cam.name)
                b.setMinimumHeight(48)
                b.clicked.connect(lambda _=False, n=cam.name: self.invoke("camera.recall", preset=n))
                self.cam_box.addWidget(b, i // 4, i % 4)
            if not self.ctx.config.cameras:
                self.cam_box.addWidget(muted("Save camera positions on the Levels & Cameras page."), 0, 0)

    def _changed(self, key: str, signature) -> bool:
        """True when a structural signature changed, so widgets are only rebuilt when needed."""
        signature = repr(signature)
        if self._sig.get(key) == signature:
            return False
        self._sig[key] = signature
        return True

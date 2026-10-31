"""Main operator dashboard: LED wall, Unreal instances, multi-user, level, takes and cameras at a glance."""
from __future__ import annotations

from operator_board.qt import QtCore, QtWidgets, Qt
from operator_board.ui.pages.base import BasePage
from operator_board.ui.theme import COLORS
from operator_board.ui.widgets import Banner, BigButton, Card, StateDot, clear_layout, hbox, muted, scroll


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
        self.mu_count = QtWidgets.QLabel("0")
        self.mu_count.setObjectName("Big")
        mu.add_layout(hbox(self.mu_count, muted("connected clients"), None))
        self.mu_clients = muted("")
        mu.add(self.mu_clients)
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
        self.rec_btn = BigButton("● RECORD", COLORS["err"], height=60)
        self.rec_btn.clicked.connect(lambda: self.invoke("takes.toggle_record"))
        minus = QtWidgets.QPushButton("Take −")
        plus = QtWidgets.QPushButton("Take +")
        minus.clicked.connect(lambda: self.invoke("takes.prev_take"))
        plus.clicked.connect(lambda: self.invoke("takes.next_take"))
        takes.add(self.take_label)
        takes.add_layout(hbox(minus, plus))
        takes.add(self.rec_btn)
        grid.addWidget(takes, 4, 0)

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
        self.mu_btn.set_active(mu.running)
        self.mu_count.setText(str(mu.client_count) if mu.running else "–")
        self.mu_clients.setText(", ".join(mu.clients) if mu.running else (mu.last_error or "Server not running"))

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
        self.rec_btn.setText("■ STOP" if self.ctx.takes.recording else "● RECORD")
        self.rec_btn.set_active(self.ctx.takes.recording)

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

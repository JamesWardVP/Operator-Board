"""Control surface configuration (Stream Deck / Companion) and an on-screen virtual deck."""
from __future__ import annotations

import json

from operator_board.qt import QtWidgets, Qt
from operator_board.surfaces.layout import MODELS, ButtonBinding, SurfacePage, default_layout, load_layout, save_layout
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, muted


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
        self.model.activated.connect(self._model_changed)
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
        deck.add_layout(hbox(QtWidgets.QLabel("Device"), self.model, None))
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

        hw = Card("Hardware & integrations")
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
        self.layout_obj.model = self.model.currentData()
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
        self.page_combo.clear()
        self.page_combo.addItems([p.name for p in lay.pages])
        self.page_combo.setCurrentIndex(self.ctx.surface.page)
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.hide()  # deleteLater is deferred; hide now so old keys never show behind new ones
                w.deleteLater()
        for i in range(lay.key_count):
            b = self._page().button(i)
            active = self.ctx.surface.is_active(i)
            btn = QtWidgets.QPushButton((b.label if b else "") or "")
            btn.setFixedSize(96, 96)
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
        self.hw_label.setText(
            f"<b>Elgato Stream Deck (direct):</b> "
            f"{'%d connected' % decks if AVAILABLE else 'library not installed - pip install streamdeck pillow'}<br>"
            f"<b>Bitfocus Companion / HTTP API:</b> {'running at ' + base if api else 'disabled (see Settings)'}<br>"
            f"Companion 'Generic HTTP' button URL examples:<br>"
            f"<code>{base}/api/action/led.freeze{token}</code><br>"
            f"<code>{base}/api/action/camera.recall?preset=Camera%201{'&token=' + s.api_token if s.api_token else ''}</code><br>"
            f"<code>{base}/api/state{token}</code> (feedback)")

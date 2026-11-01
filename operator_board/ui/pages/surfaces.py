"""Control surface configuration (Stream Deck, MIDI, OSC, phone/tablet web panel, Companion) and an
on-screen virtual deck.

Any number of surfaces can be set up at once, several of one type too (two Stream Deck Minis with
different buttons). Pick a type from the list to add one; click a surface to edit its buttons;
double-click its name to rename it.
"""
from __future__ import annotations

import json
import secrets

from operator_board.qt import QtGui, QtWidgets, Qt
from operator_board.surfaces.layout import (FONT_WEIGHTS, KINDS, MODELS, ButtonBinding, SurfacePage, fit_to_model,
                                            new_surface)
from operator_board.surfaces.render import fit_label
from operator_board.ui.action_editors import MACRO_ACTION, OSC_ACTION, OscEditor, StepsEditor
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, ColorField, hbox, local_addresses, make_searchable, muted

KIND_HINTS = {
    "streamdeck": "Plugged into this PC. Quit the Elgato Stream Deck app first: only one program can use a deck. "
                  "With several decks of one size, choose which deck this surface drives.",
    "web": "Open the web panel address below on the phone or tablet (same network). Works with any layout.",
    "osc": "In TouchOSC / Open Stage Control, send /operator/key &lt;n&gt; (0 = top-left) to this PC's UDP port "
           "(turn OSC on below). /operator/action/&lt;id&gt; runs any action; /operator/page next|prev.",
    "midi": "Plug the controller in. Pads press the key in the same position; pad lights follow the keys where "
            "the controller supports it. With several controllers, choose which one this surface uses.",
    "custom": "Choose any size. Use it on the web panel, from OSC, or with a MIDI controller chosen below "
              "(keys count up from the base note, bottom-left first).",
}
WEIGHT_LABELS = {"light": "Light", "normal": "Normal", "semibold": "Semibold", "bold": "Bold", "black": "Black"}
QT_WEIGHTS = {"light": "Light", "normal": "Normal", "semibold": "DemiBold", "bold": "Bold", "black": "Black"}
FONT_SIZES = ["Auto (fit the key)", "8", "10", "12", "14", "16", "18", "20", "24", "28", "32", "40"]
LIST_HEADERS = ["Name", "Type", "Device", "Status"]


def qt_font(weight: str, px: int) -> QtGui.QFont:
    font = QtGui.QFont()
    font.setPixelSize(max(1, px))
    font.setWeight(getattr(QtGui.QFont, QT_WEIGHTS.get(weight, "Bold")))
    return font


class KeyButton(QtWidgets.QPushButton):
    """An on-screen key that shows its label the way the Stream Deck does (wrap, size, weight, colour)."""

    def __init__(self, size: int, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.text_label = QtWidgets.QLabel(self)
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text_label.setGeometry(4, 4, size - 8, size - 8)

    def show_label(self, text: str, color: str, size: int, weight: str, wrap: bool) -> None:
        pad = max(3, self.width() // 14)
        area = self.width() - 2 * pad

        def measure(line: str, px: int):
            metrics = QtGui.QFontMetrics(qt_font(weight, px))
            return metrics.horizontalAdvance(line or " "), metrics.height()

        lines, px = fit_label(text, area, area, measure, size, wrap, key_px=self.width())
        self.text_label.setFont(qt_font(weight, px))
        self.text_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.text_label.setText("\n".join(lines))


class SurfacesPage(BasePage):
    title = "Control Surfaces"
    refresh_topics = ("surface.", "led.", "takes.", "multiuser.", "action.")

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        self.selected = None
        self.current = ctx.surfaces.primary
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)

        left = QtWidgets.QVBoxLayout()
        # ---- every surface ----------------------------------------------------------
        surfaces = Card("Control surfaces")
        self.add_combo = QtWidgets.QComboBox()
        self.add_combo.addItem("+ Add a control surface…", "")
        for mid, (label, _c, _r) in MODELS.items():
            self.add_combo.addItem(label, mid)
        make_searchable(self.add_combo)
        if self.add_combo.isEditable():
            self.add_combo.lineEdit().setPlaceholderText("+ Add a control surface (type to search)…")
        self.add_combo.setMinimumContentsLength(28)
        self.add_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.add_combo.currentIndexChanged.connect(self._add_surface)  # also fires for a typed search
        surfaces.add(self.add_combo)
        self.list = QtWidgets.QTreeWidget()
        self.list.setHeaderLabels(LIST_HEADERS)
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(130)
        self.list.setMaximumHeight(200)
        self.list.itemSelectionChanged.connect(self._surface_selected)
        self.list.itemDoubleClicked.connect(self._start_rename)
        self.list.itemChanged.connect(self._item_changed)
        header = self.list.header()
        header.setStretchLastSection(True)
        for col in range(3):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)
        surfaces.add(self.list)
        surfaces.add(muted("Tick a surface to use it. Double-click a name to rename it. Click a surface to edit "
                           "its buttons below."))
        buttons = []
        for label, fn, tip in [
                ("Duplicate", self._duplicate, "A copy with the same buttons, for another device of the same type"),
                ("Remove", self._remove, "Remove the selected surfaces"),
                ("↻ Refresh devices", self._refresh_devices,
                 "Look for Stream Decks and MIDI controllers plugged in or unplugged since the program opened "
                 "(this also happens by itself when Windows sees a device come or go)"),
                ("Export selected…", lambda: self._export(True), "Save the selected surfaces to a file"),
                ("Export all…", lambda: self._export(False), "Save every surface to a file (backup)"),
                ("Import…", self._import, "Add surfaces from a file (nothing here is replaced)")]:
            b = QtWidgets.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            buttons.append(b)
        surfaces.add_layout(hbox(*buttons[:3], None))
        surfaces.add_layout(hbox(*buttons[3:], None))
        left.addWidget(surfaces, 0)

        # ---- the selected surface's buttons ----------------------------------------------
        self.deck_card = Card("Buttons")
        self.model = QtWidgets.QComboBox()
        for mid, (label, _c, _r) in MODELS.items():
            self.model.addItem(label, mid)
        make_searchable(self.model)
        self.model.currentIndexChanged.connect(self._model_changed)
        self.model.setMinimumContentsLength(24)
        self.model.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        type_row = hbox(QtWidgets.QLabel("Type"), self.model)
        type_row.setStretch(1, 1)
        self.deck_card.add_layout(type_row)
        self.device = QtWidgets.QComboBox()
        self.device.setMinimumContentsLength(24)
        self.device.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.device.activated.connect(self._device_changed)
        self.device_row = QtWidgets.QWidget()
        self.device_row.setObjectName("PlainRow")
        device_lay = hbox(QtWidgets.QLabel("Device"), self.device)
        device_lay.setStretch(1, 1)
        device_lay.setContentsMargins(0, 0, 0, 0)
        self.device_row.setLayout(device_lay)
        self.deck_card.add(self.device_row)
        self.custom_cols = QtWidgets.QLineEdit()
        self.custom_rows = QtWidgets.QLineEdit()
        self.base_note = QtWidgets.QLineEdit()
        for edit, top in ((self.custom_cols, 16), (self.custom_rows, 16), (self.base_note, 127)):
            edit.setValidator(QtGui.QIntValidator(0 if edit is self.base_note else 1, top, edit))
            edit.setFixedWidth(56)
            edit.editingFinished.connect(self._custom_changed)
        self.custom_row = QtWidgets.QWidget()
        self.custom_row.setObjectName("PlainRow")
        custom_lay = hbox(QtWidgets.QLabel("Columns"), self.custom_cols, QtWidgets.QLabel("Rows"), self.custom_rows,
                          QtWidgets.QLabel("MIDI base note"), self.base_note, None)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        self.custom_row.setLayout(custom_lay)
        self.deck_card.add(self.custom_row)
        self.kind_hint = muted("")
        self.kind_hint.setTextFormat(Qt.RichText)
        self.deck_card.add(self.kind_hint)
        self.page_combo = QtWidgets.QComboBox()
        self.page_combo.activated.connect(self._page_changed)
        self.page_combo.setMinimumContentsLength(10)
        self.page_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        page_btns = []
        for label, fn in [("+ Page", self._add_page), ("Rename", self._rename_page), ("Delete page", self._delete_page)]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            page_btns.append(b)
        self.deck_card.add_layout(hbox(QtWidgets.QLabel("Page"), self.page_combo, *page_btns, None))
        self.live = QtWidgets.QCheckBox("Live mode (pressing a key runs its action)")
        self.deck_card.add(self.live)
        self.grid_holder = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.grid_holder)
        self.grid.setSpacing(8)
        self.deck_card.add(self.grid_holder)
        reset = QtWidgets.QPushButton("Reset to default buttons")
        reset.clicked.connect(self._reset)
        self.deck_card.add_layout(hbox(reset, None))
        left.addWidget(self.deck_card, 0)
        left.addStretch(1)

        # ---- web panel ---------------------------------------------------------------------
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

        # ---- OSC and status -------------------------------------------------------------
        hw = Card("Hardware & integrations")
        self.osc_on = QtWidgets.QCheckBox("OSC (TouchOSC, Open Stage Control...) on UDP port")
        self.osc_port = QtWidgets.QLineEdit()
        self.osc_port.setValidator(QtGui.QIntValidator(1, 65535, self.osc_port))
        self.osc_port.setFixedWidth(70)
        self.osc_on.toggled.connect(self._backends_changed)
        self.osc_port.editingFinished.connect(self._backends_changed)
        hw.add_layout(hbox(self.osc_on, self.osc_port, None))
        self.hw_label = muted("")
        self.hw_label.setTextFormat(Qt.RichText)
        self.hw_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hw.add(self.hw_label)
        lay.addLayout(left, 3)

        # ---- key editor -------------------------------------------------------------------
        edit = Card("Key")
        form = QtWidgets.QFormLayout()
        self.action = QtWidgets.QComboBox()
        # Size by a fixed length, not by the longest action name, so the page can shrink.
        self.action.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.action.setMinimumContentsLength(18)
        self.action.addItem("(none)", "")
        for a in ctx.actions.all():
            self.action.addItem(f"{a.category} › {a.label}", a.id)
        make_searchable(self.action)
        self.action.currentIndexChanged.connect(self._action_changed)
        self.label = QtWidgets.QPlainTextEdit()
        self.label.setPlaceholderText("Text on the key (Enter starts a new line)")
        self.label.setFixedHeight(58)
        self.params = QtWidgets.QLineEdit()
        self.params.setPlaceholderText('{"preset": "Camera 1"}')
        self.param_hint = muted("")
        self.params_box = QtWidgets.QWidget()
        self.params_box.setObjectName("PlainRow")
        params_form = QtWidgets.QFormLayout(self.params_box)
        params_form.setContentsMargins(0, 0, 0, 0)
        params_form.addRow("Parameters (JSON)", self.params)
        params_form.addRow("", self.param_hint)
        self.osc_editor = OscEditor()
        self.steps_editor = StepsEditor(ctx.actions)
        self.color = ColorField("Key colour")
        self.active_color = ColorField("Active key colour")
        self.text_color = ColorField("Text colour")
        self.font_size = QtWidgets.QComboBox()
        self.font_size.setEditable(True)
        self.font_size.addItems(FONT_SIZES)
        self.font_size.setValidator(None)
        self.font_size.setToolTip("Size on a 72-pixel key (a Stream Deck MK.2 key); bigger and smaller keys "
                                  "scale it. Auto makes the text as large as fits.")
        self.font_weight = QtWidgets.QComboBox()
        for name in FONT_WEIGHTS:
            self.font_weight.addItem(WEIGHT_LABELS[name], name)
        self.word_wrap = QtWidgets.QCheckBox("Word wrap (long text continues on the next line)")
        self.word_wrap.setChecked(True)
        form.addRow("Action", self.action)
        form.addRow("Label", self.label)
        form.addRow(self.params_box)
        form.addRow(self.osc_editor)
        form.addRow(self.steps_editor)
        form.addRow("Colour", self.color)
        form.addRow("Active colour", self.active_color)
        form.addRow("Text colour", self.text_color)
        form.addRow("Font size", self.font_size)
        form.addRow("Font weight", self.font_weight)
        form.addRow("", self.word_wrap)
        edit.add_layout(form)
        apply = QtWidgets.QPushButton("Apply to key")
        apply.clicked.connect(self._apply)
        clear = QtWidgets.QPushButton("Clear key")
        clear.clicked.connect(self._clear)
        edit.add_layout(hbox(apply, clear))
        edit.add(muted("Click a key on the deck to edit it."))
        right = QtWidgets.QVBoxLayout()
        right.addWidget(edit, 0)
        right.addWidget(web, 0)
        right.addWidget(hw, 0)
        right.addStretch(1)
        lay.addLayout(right, 2)
        self._fill_key_form(ButtonBinding())

    # ---- the surface being edited -------------------------------------------------------
    @property
    def layout_obj(self):
        return self.current.layout if self.current else None

    def _page(self) -> SurfacePage:
        return self.current.current_page

    def _changed(self, devices: bool = False, navigation: bool = False) -> None:
        self.ctx.surfaces.changed(self.current, devices=devices, navigation=navigation)
        self.save_config()

    def _selected_surfaces(self):
        rows = sorted({self.list.indexOfTopLevelItem(i) for i in self.list.selectedItems()})
        return [self.ctx.surfaces.controllers[r] for r in rows if 0 <= r < len(self.ctx.surfaces.controllers)]

    def _select(self, controller) -> None:
        self.current = controller
        self.selected = None
        self.refresh()

    def _surface_selected(self) -> None:
        chosen = self._selected_surfaces()
        if chosen and chosen[0] is not self.current:
            self._select(chosen[0])

    def _add_surface(self, index: int) -> None:
        model = self.add_combo.itemData(index) if index > 0 else ""
        if not model:
            return
        self.add_combo.blockSignals(True)
        self.add_combo.setCurrentIndex(0)
        if self.add_combo.isEditable():
            self.add_combo.lineEdit().clear()
        self.add_combo.blockSignals(False)
        controller = self.ctx.surfaces.add(model)
        self.save_config()
        self.notify(f"Added {controller.layout.name}")
        self._select(controller)

    def _start_rename(self, item, column: int) -> None:
        if column == 0:
            self.list.editItem(item, 0)

    def _item_changed(self, item, column: int) -> None:
        if column != 0:
            return
        row = self.list.indexOfTopLevelItem(item)
        if not 0 <= row < len(self.ctx.surfaces.controllers):
            return
        controller = self.ctx.surfaces.controllers[row]
        enabled = item.checkState(0) == Qt.Checked
        if enabled != controller.layout.enabled:
            controller.layout.enabled = enabled
            self.ctx.surfaces.changed(controller, devices=True)
            self.save_config()
        if item.text(0) != controller.layout.name:
            name = self.ctx.surfaces.rename(controller, item.text(0))
            self.save_config()
            self.list.blockSignals(True)
            item.setText(0, name)
            self.list.blockSignals(False)

    def _duplicate(self) -> None:
        if self.current:
            controller = self.ctx.surfaces.duplicate(self.current)
            self.save_config()
            self._select(controller)

    def _remove(self) -> None:
        chosen = self._selected_surfaces() or ([self.current] if self.current else [])
        if not chosen:
            return
        names = ", ".join(c.layout.name for c in chosen)
        if QtWidgets.QMessageBox.question(self, "Remove", f"Remove {names}?\n\nExport them first to keep a copy.") \
                != QtWidgets.QMessageBox.Yes:
            return
        for c in chosen:
            self.ctx.surfaces.remove(c)
        self.save_config()
        self._select(self.ctx.surfaces.primary)

    def _refresh_devices(self) -> None:
        self.background(self.ctx.refresh_surface_devices, lambda msg: (self.notify(msg), self.refresh()),
                        "Refresh devices")

    def _export(self, selected: bool) -> None:
        chosen = self._selected_surfaces() if selected else self.ctx.surfaces.controllers
        if not chosen:
            self.notify("Select the surfaces to export first", False)
            return
        default = (chosen[0].layout.name if len(chosen) == 1 else "operator_board_surfaces") + ".json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export control surfaces", default,
                                                        "Control surfaces (*.json)")
        if path:
            try:
                count = self.ctx.surfaces.export(path, chosen)
            except OSError as exc:
                self.notify(f"Could not export: {exc}", False)
                return
            self.notify(f"Exported {count} surface{'s' if count != 1 else ''} to {path}")

    def _import(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import control surfaces", "",
                                                        "Control surfaces (*.json)")
        if not path:
            return
        try:
            added = self.ctx.surfaces.import_file(path)
        except (OSError, ValueError, TypeError) as exc:
            self.notify(f"Could not import: {exc}", False)
            return
        self.save_config()
        self.notify(f"Imported {', '.join(c.layout.name for c in added) or 'nothing'}")
        if added:
            self._select(added[0])

    def _model_changed(self) -> None:
        data = self.model.currentData()
        lay = self.layout_obj
        if not lay or not data or data == lay.model:
            return
        old_keys = lay.key_count
        resized = fit_to_model(lay, data)
        if resized.key_count < old_keys:  # spread the keys over more pages so none is lost
            lay.pages = resized.pages
        lay.model = data
        self.current.page = 0
        self.selected = None
        self._changed(devices=True, navigation=True)
        self.refresh()

    def _device_changed(self) -> None:
        if self.layout_obj is not None:
            self.layout_obj.device = self.device.currentData() or ""
            self._changed(devices=True)
            self.refresh()

    def _custom_changed(self) -> None:
        lay = self.layout_obj
        if lay is None:
            return
        for edit, attr in ((self.custom_cols, "custom_columns"), (self.custom_rows, "custom_rows"),
                           (self.base_note, "midi_base_note")):
            if edit.hasAcceptableInput() and getattr(lay, attr) != int(edit.text()):
                setattr(lay, attr, int(edit.text()))
                self._changed(devices=True, navigation=True)
        self.refresh()

    # ---- web panel, OSC ---------------------------------------------------------------
    def _panel_urls(self):
        s = self.ctx.config.surfaces
        port = self.ctx.api.port if self.ctx.api else s.api_port
        query = [f"surface={self.layout_obj.id}"] if self.layout_obj else []
        if s.api_token:
            query.append(f"token={s.api_token}")
        hosts = local_addresses() if s.api_bind == "0.0.0.0" else [s.api_bind]
        return [f"http://{h}:{port}/panel" + ("?" + "&".join(query) if query else "") for h in hosts]

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

    def _backends_changed(self, *_args) -> None:
        s = self.ctx.config.surfaces
        s.osc_enabled = self.osc_on.isChecked()
        if self.osc_port.hasAcceptableInput():
            s.osc_port = int(self.osc_port.text())
        self.ctx.start_surface_backends()
        self.save_config()
        self.refresh()

    # ---- pages --------------------------------------------------------------------------
    def _page_changed(self, index: int) -> None:
        self.current.page = index
        self.selected = None
        self.refresh()

    def _add_page(self) -> None:
        if not self.current:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "New page", "Page name:")
        if ok and name:
            self.layout_obj.pages.append(SurfacePage(name=name))
            self.current.page = len(self.layout_obj.pages) - 1
            self._changed(navigation=True)  # every page gets a Next page key
            self.refresh()

    def _rename_page(self) -> None:
        if not self.current:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename page", "Page name:", text=self._page().name)
        if ok and name:
            self._page().name = name
            self._changed()
            self.refresh()

    def _delete_page(self) -> None:
        if self.current and len(self.layout_obj.pages) > 1:
            self.layout_obj.pages.pop(self.current.page)
            self.current.page = 0
            self._changed()
            self.refresh()

    def _reset(self) -> None:
        if self.current and QtWidgets.QMessageBox.question(
                self, "Reset", f"Replace the buttons of {self.layout_obj.name} with the default buttons?") \
                == QtWidgets.QMessageBox.Yes:
            self.layout_obj.pages = new_surface(self.layout_obj.model).pages
            self.current.page = 0
            self._changed()
            self.refresh()

    # ---- keys ----------------------------------------------------------------------------
    def _key_clicked(self, index: int) -> None:
        if self.live.isChecked():
            controller = self.current
            self.background(lambda: controller.press(index),
                            lambda r: (self.notify(r.message, r.ok), self.refresh()), "Key")
            return
        self.selected = index
        self._fill_key_form(self._page().button(index) or ButtonBinding())
        self.refresh()

    def _fill_key_form(self, b: ButtonBinding) -> None:
        self.action.setCurrentIndex(max(0, self.action.findData(b.action)))
        self.label.setPlainText(b.label)
        self.params.setText(json.dumps(b.params) if b.params else "")
        self.osc_editor.set_params(b.params if b.action == OSC_ACTION else {})
        self.steps_editor.set_steps((b.params or {}).get("steps", []) if b.action == MACRO_ACTION else [])
        self._action_changed()
        self.color.setText(b.color)
        self.active_color.setText(b.active_color)
        self.text_color.setText(b.text_color)
        self.font_size.setCurrentText(str(b.font_size) if b.font_size else FONT_SIZES[0])
        self.font_weight.setCurrentIndex(max(0, self.font_weight.findData(b.font_weight)))
        self.word_wrap.setChecked(b.word_wrap)

    def _action_changed(self) -> None:
        action_id = self.action.currentData() or ""
        self.osc_editor.setVisible(action_id == OSC_ACTION)
        self.steps_editor.setVisible(action_id == MACRO_ACTION)
        self.params_box.setVisible(action_id not in (OSC_ACTION, MACRO_ACTION))
        action = self.ctx.actions.get(action_id)
        if action and action.params:
            hints = []
            for p in action.params:
                choices = p.choices()
                hints.append(p.name + (f" ∈ {choices[:6]}" if choices else f" ({p.kind})"))
            self.param_hint.setText("Parameters: " + "; ".join(hints))
        else:
            self.param_hint.setText("No parameters")

    def _apply(self) -> None:
        if self.selected is None or not self.current:
            self.notify("Click a key on the deck first", False)
            return
        action_id = self.action.currentData() or ""
        try:
            if action_id == OSC_ACTION:
                params = self.osc_editor.params()
            elif action_id == MACRO_ACTION:
                params = {"steps": self.steps_editor.steps()}
                if not params["steps"]:
                    raise ValueError("add at least one step")
            else:
                params = json.loads(self.params.text()) if self.params.text().strip() else {}
        except ValueError as exc:
            self.notify(f"Parameters: {exc}", False)
            return
        size_text = self.font_size.currentText().strip()
        if size_text and not size_text.isdigit() and size_text != FONT_SIZES[0]:
            self.notify("Font size must be a number of pixels, or Auto", False)
            return
        for field, value in (("Colour", self.color), ("Active colour", self.active_color),
                             ("Text colour", self.text_color)):
            if value.text() and not QtGui.QColor(value.text()).isValid():
                self.notify(f"{field} is not a colour: use #rrggbb or the colour picker", False)
                return
        action = self.ctx.actions.get(action_id)
        self._page().buttons[str(self.selected)] = ButtonBinding(
            action=action_id, label=self.label.toPlainText() or (action.label if action else ""), params=params,
            color=self.color.text() or "#2b2f36", active_color=self.active_color.text() or "#d9480f",
            text_color=self.text_color.text() or "#ffffff",
            font_size=int(size_text) if size_text.isdigit() else 0,
            font_weight=self.font_weight.currentData() or "bold", word_wrap=self.word_wrap.isChecked())
        self._changed()
        self.refresh()

    def _clear(self) -> None:
        if self.selected is not None and self.current:
            self._page().buttons.pop(str(self.selected), None)
            self._changed()
            self.refresh()

    # ---- display ----------------------------------------------------------------------
    def _status(self, controller) -> str:
        lay = controller.layout
        if not lay.enabled:
            return "off"
        kind = lay.kind
        if kind == "streamdeck":
            from operator_board.surfaces.streamdeck import AVAILABLE
            if not AVAILABLE:
                return "Stream Deck library not installed"
            if not self.ctx.config.surfaces.streamdeck_enabled:
                return "Stream Decks are off in Settings"
            deck = self.ctx.streamdeck.deck_for(controller) if self.ctx.streamdeck else None
            return f"connected ({deck.deck_type})" if deck else "not found: plug it in, then Refresh devices"
        if kind == "midi" or (kind == "custom" and lay.device):
            from operator_board.surfaces.midi import AVAILABLE as MIDI_OK
            if not MIDI_OK:
                return "MIDI library not installed"
            midi = self.ctx.midi_for(controller)
            return f"connected ({midi.opened})" if midi else "not found: plug it in, then Refresh devices"
        if kind == "osc":
            return f"OSC on UDP {self.ctx.osc.port}" if self.ctx.osc else "OSC is off (turn it on below)"
        if kind == "web":
            return "web panel" if self.ctx.api else "web panel off (Settings)"
        return "web panel / OSC"

    def _device_text(self, controller) -> str:
        lay = controller.layout
        if lay.kind == "streamdeck":
            deck = self.ctx.streamdeck.deck_for(controller) if self.ctx.streamdeck else None
            return (deck.serial if deck else "") or lay.device or "any"
        if lay.kind in ("midi", "custom"):
            midi = self.ctx.midi_for(controller)
            return (midi.opened if midi else "") or lay.device or ("any" if lay.kind == "midi" else "")
        return ""

    def _fill_list(self) -> None:
        if self.list.state() == QtWidgets.QAbstractItemView.EditingState:
            return  # do not end a rename in progress
        self.list.blockSignals(True)
        self.list.clear()
        for controller in self.ctx.surfaces.controllers:
            lay = controller.layout
            item = QtWidgets.QTreeWidgetItem([lay.name, MODELS.get(lay.model, (lay.model,))[0].split(" (")[0],
                                              self._device_text(controller), self._status(controller)])
            item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if lay.enabled else Qt.Unchecked)
            item.setToolTip(0, "Double-click to rename; untick to stop using it")
            self.list.addTopLevelItem(item)
            if controller is self.current:
                item.setSelected(True)
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def _fill_devices(self) -> None:
        lay = self.layout_obj
        kind = lay.kind if lay else ""
        show = kind in ("streamdeck", "midi", "custom")
        self.device_row.setVisible(show)
        if not show:
            return
        self.device.blockSignals(True)
        self.device.clear()
        if kind == "streamdeck":
            keys = lay.key_count
            self.device.addItem(f"Next free Stream Deck with {keys} keys", "")
            for deck in (self.ctx.streamdeck.decks if self.ctx.streamdeck else []):
                owner = deck.surface.layout.name if deck.surface and deck.surface is not self.current else ""
                self.device.addItem(f"{deck.deck_type} · {deck.serial}" + (f" (used by {owner})" if owner else ""),
                                    deck.serial)
        else:
            from operator_board.surfaces.midi import input_names
            self.device.addItem("Next free MIDI input" if kind == "midi" else "(no MIDI controller)", "")
            for name in input_names():
                self.device.addItem(name, name)
        if lay.device and self.device.findData(lay.device) < 0:
            self.device.addItem(f"{lay.device} (not connected)", lay.device)
        self.device.setCurrentIndex(max(0, self.device.findData(lay.device)))
        self.device.blockSignals(False)

    def refresh(self) -> None:
        if self.current not in self.ctx.surfaces.controllers:
            self.current = self.ctx.surfaces.primary
        self._fill_list()
        lay = self.layout_obj
        self.deck_card.setEnabled(lay is not None)
        self._show_web_and_status()
        if lay is None:
            self.kind_hint.setText("Add a control surface above.")
            return
        self.model.blockSignals(True)
        self.model.setCurrentIndex(max(0, self.model.findData(lay.model)))
        self.model.blockSignals(False)
        kind = KINDS.get(lay.model, "streamdeck")
        self.custom_row.setVisible(lay.model == "custom")
        for edit, value in ((self.custom_cols, lay.custom_columns), (self.custom_rows, lay.custom_rows),
                            (self.base_note, lay.midi_base_note)):
            if not edit.hasFocus():
                edit.setText(str(value))
        self._fill_devices()
        hint = KIND_HINTS.get(kind, "")
        number = self.ctx.surfaces.controllers.index(self.current) + 1
        if kind in ("osc", "custom") and len(self.ctx.surfaces.controllers) > 1:
            hint += f" For this surface in particular, send /operator/surface/{number}/key &lt;n&gt;."
        self.kind_hint.setText(hint)
        self.page_combo.clear()
        self.page_combo.addItems([p.name for p in lay.pages])
        self.page_combo.setCurrentIndex(self.current.page)
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
            active = self.current.is_active(i)
            btn = KeyButton(key)
            if b and b.action:
                btn.show_label(b.label or b.action, b.text_color, b.font_size, b.font_weight, b.word_wrap)
            bg = (b.active_color if active else b.color) if b and b.action else "#16181c"
            border = "#ffffff" if i == self.selected else "#30353d"
            btn.setStyleSheet(f"QPushButton {{ background: {bg}; border: 2px solid {border}; border-radius: 10px; }}")
            btn.setToolTip(b.action if b else "unassigned")
            btn.clicked.connect(lambda _=False, i=i: self._key_clicked(i))
            self.grid.addWidget(btn, i // lay.columns, i % lay.columns)

    def _show_web_and_status(self) -> None:
        self.deck_card_title(self.layout_obj.name if self.layout_obj else "")
        s = self.ctx.config.surfaces
        api = self.ctx.api
        base = f"http://{s.api_bind if s.api_bind != '0.0.0.0' else '<this-pc-ip>'}:{api.port if api else s.api_port}"
        token = f"?token={s.api_token}" if s.api_token else ""
        lan = s.api_bind == "0.0.0.0"
        urls = self._panel_urls()
        name = self.layout_obj.name if self.layout_obj else "the first surface"
        if not api:
            self.web_label.setText("The web panel needs the HTTP API, which is off (Settings &gt; Control surfaces).")
        else:
            links = "<br>".join(f"<a href='{u}' style='color:#4dabf7'>{u}</a>" for u in urls) or "(no network found)"
            self.web_label.setText(
                ("Open on a phone or tablet on this network:<br>" if lan else
                 "Only this PC can open it now (allow phones and tablets to use it from other devices):<br>")
                + links + f"<br>It shows the buttons of <b>{name}</b>; they work like Stream Deck keys.")
        self.network_btn.setText("Only this PC" if lan else "Allow phones and tablets on this network")
        self.copy_btn.setEnabled(bool(api and urls))
        if self.osc_on.isChecked() != s.osc_enabled:
            self.osc_on.blockSignals(True)
            self.osc_on.setChecked(s.osc_enabled)
            self.osc_on.blockSignals(False)
        if not self.osc_port.hasFocus():
            self.osc_port.setText(str(s.osc_port))
        from operator_board.surfaces.midi import AVAILABLE as MIDI_OK
        from operator_board.surfaces.streamdeck import AVAILABLE
        osc = self.ctx.osc
        decks = self.ctx.streamdeck.decks if self.ctx.streamdeck else []
        osc_text = (f"listening on UDP {osc.port}" if osc else
                    ("could not open the port (in use?)" if s.osc_enabled else "off"))
        midi_text = (", ".join(m.opened for m in self.ctx.midi) or "none in use") if MIDI_OK else \
            "library not installed - pip install mido python-rtmidi"
        deck_text = (", ".join(f"{d.deck_type} {d.serial}" for d in decks) or "none found") if AVAILABLE else \
            "library not installed - pip install streamdeck pillow"
        self.hw_label.setText(
            f"<b>OSC:</b> {osc_text}<br><b>MIDI controllers:</b> {midi_text}<br>"
            f"<b>Elgato Stream Decks (direct):</b> {deck_text}<br>"
            f"<b>Bitfocus Companion / HTTP API:</b> {'running at ' + base if api else 'disabled (see Settings)'}<br>"
            f"Companion 'Generic HTTP' button URL examples:<br>"
            f"<code>{base}/api/action/led.freeze{token}</code><br>"
            f"<code>{base}/api/action/camera.recall?preset=Camera%201{'&token=' + s.api_token if s.api_token else ''}</code><br>"
            f"<code>{base}/api/state{token}</code> (feedback)")

    def deck_card_title(self, name: str) -> None:
        label = self.deck_card.findChild(QtWidgets.QLabel, "CardTitle")
        if label is not None:
            label.setText(f"BUTTONS: {name.upper()}" if name else "BUTTONS")

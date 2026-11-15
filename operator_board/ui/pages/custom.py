"""A custom page: buttons and other pages' panels on a snapping grid, laid out by the operator.

A panel is not a copy of the code behind it. The view keeps a hidden instance of the page the panel
belongs to (a "controller") and shows that page's own card, tab or the whole page, so the panel
behaves exactly as it does on its page and reads the same live state. Panels from one page share a
controller, so parts that work together (a list and its details) still do.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from operator_board.core.custom_pages import (CONTROL_PREFIX, DEFAULT_SIZE, KIND_BUTTON, KIND_PANEL, TAB_PREFIX,
                                              WHOLE_PAGE, CustomPage, PageItem, new_id)
from operator_board.qt import QtCore, QtWidgets, Qt
from operator_board.ui.button_dialog import ActionButton, binding_from, edit_button
from operator_board.ui.custom_grid import GridCanvas
from operator_board.ui.pages.base import BasePage
from operator_board.ui.theme import COLORS
from operator_board.ui.widgets import Card, hbox, muted

MIN_PANEL = (4, 2)   # smallest size (columns, rows) a new panel starts with
MAX_PANEL = (24, 20)
MIN_CONTROL = (2, 1)  # ... and a single control
BAR_WIDTH = 12       # columns a new panel that is not a card starts with (a status bar stretches)


def panel_label(key: str) -> str:
    if key == WHOLE_PAGE:
        return "Whole page"
    if key.startswith(CONTROL_PREFIX):
        return key[len(CONTROL_PREFIX):].replace("/", " › ")
    return key[len(TAB_PREFIX):] + " (whole tab)" if key.startswith(TAB_PREFIX) else key


def _ancestors(widget: QtWidgets.QWidget) -> list:
    chain, parent = [], widget.parentWidget()
    while parent is not None:
        chain.append(parent)
        parent = parent.parentWidget()
    return chain


class CustomPageView(BasePage):
    EDITOR_EVENTS = ("editor.ready", "editor.level_changed")
    is_custom = True

    def __init__(self, ctx, window, page: CustomPage) -> None:
        super().__init__(ctx, window)
        self.page_ = page
        self.title = page.name
        self.controllers: List[BasePage] = []
        # id(controller) -> [(widget shown from it, the widgets it sat inside)]
        self._taken: Dict[int, list] = {}
        self._labels: Dict[str, str] = {}     # item id -> the label of the control it shows
        self._exclusive: set = set()          # id(controller) of pages shown whole or by tab
        self.buttons: Dict[str, ActionButton] = {}
        self._built = False

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 8)
        lay.setSpacing(6)
        self.name_label = QtWidgets.QLabel(page.name)
        self.name_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.hint = muted("Drag an item to move it; drag its bottom-right corner to resize it. Items snap to the grid.")
        self.hint.hide()
        self.add_btn = QtWidgets.QToolButton()
        self.add_btn.setText("+ Add item")
        self.add_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.add_btn.setToolTip("Add a button for any action, or a panel from another page")
        self.add_btn.setStyleSheet(f"QToolButton {{ background: {COLORS['panel2']}; border: 1px solid "
                                   f"{COLORS['border']}; border-radius: 8px; padding: 8px 14px; }}"
                                   "QToolButton::menu-indicator { image: none; }")
        self.add_menu = QtWidgets.QMenu(self.add_btn)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.add_btn.setMenu(self.add_menu)
        self.add_btn.hide()
        self.edit_btn = QtWidgets.QPushButton("Edit layout")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip("Unlock the page to add, move, resize and remove items")
        self.edit_btn.toggled.connect(self.set_editing)
        lay.addLayout(hbox(self.name_label, None, self.add_btn, self.edit_btn))
        lay.addWidget(self.hint)
        self.empty = muted("This page is empty. Click Edit layout, then + Add item.")
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty)
        self.canvas = GridCanvas(page)
        self.canvas.changed.connect(self._layout_changed)
        self.canvas.edit_requested.connect(self.edit_item)
        self.canvas.remove_requested.connect(self.remove_item)
        self.area = QtWidgets.QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.area.setWidget(self.canvas)
        lay.addWidget(self.area, 1)

    # ---- building ------------------------------------------------------------------------
    @property
    def editing(self) -> bool:
        return self.edit_btn.isChecked()

    def build(self) -> None:
        """Make the widgets (once, the first time the page is shown: every panel starts a page)."""
        if self._built:
            return
        self._built = True
        for item in self.page_.items:
            self._mount(item)
        self._show_empty()

    def teardown(self) -> None:
        """Drop every widget and controller (before a rebuild, or when the page goes away)."""
        self.canvas.clear()
        for controller in self.controllers:
            controller.host = None
            controller.hide()
            controller.setParent(None)
            controller.deleteLater()
        self.controllers, self._taken, self._exclusive, self.buttons, self._labels = [], {}, set(), {}, {}
        self._built = False

    def rebuild(self) -> None:
        self.teardown()
        self.build()
        self.refresh()

    def _show_empty(self) -> None:
        self.empty.setVisible(not self.page_.items and not self.editing)

    def _new_controller(self, cls) -> BasePage:
        controller = cls(self.ctx, self.window_)
        controller.host = self
        controller.setParent(self)  # hidden, but dialogs it opens belong to this window
        controller.hide()
        self.controllers.append(controller)
        self._taken[id(controller)] = []
        controller.refresh()  # fills in what the page makes as it runs, so those parts can be found
        return controller

    def _free(self, controller: BasePage, widget: QtWidgets.QWidget) -> bool:
        """True when ``widget`` is still whole and in place on this controller: it has not been shown
        already, is not inside something that has, and nothing has been taken out of it."""
        for taken, was_inside in self._taken[id(controller)]:
            if taken is widget or taken.isAncestorOf(widget) or any(w is widget for w in was_inside):
                return False
        return True

    @staticmethod
    def _find(controller: BasePage, key: str):
        """(label, widget) of a panel or control of the page, or None."""
        if key.startswith(CONTROL_PREFIX):
            return next(((label, w) for k, label, w, _card in controller.controls() if k == key), None)
        return next(((label, w) for k, label, w in controller.panels() if k == key), None)

    def _take_panel(self, item: PageItem) -> Optional[QtWidgets.QWidget]:
        """The live widget for a panel item, or None when the page or the panel no longer exists."""
        cls = self.window_.page_class(item.source)
        if cls is None:
            return None
        if item.panel == WHOLE_PAGE:
            controller = self._new_controller(cls)
            self._exclusive.add(id(controller))
            return controller
        alone = item.panel.startswith(TAB_PREFIX)  # a tab holds cards, so it cannot share its page
        found = controller = None
        for candidate in ([] if alone else [c for c in self.controllers
                                            if type(c) is cls and id(c) not in self._exclusive]):
            found = self._find(candidate, item.panel)
            if found is not None and self._free(candidate, found[1]):
                controller = candidate
                break
        if controller is None:
            controller = self._new_controller(cls)
            found = self._find(controller, item.panel)
            if found is None:  # the page has no such part: do not keep a page running for nothing
                self.controllers.remove(controller)
                del self._taken[id(controller)]
                controller.host = None
                controller.setParent(None)
                controller.deleteLater()
                return None
        label, widget = found
        self._labels[item.id] = label
        self._taken[id(controller)].append((widget, _ancestors(widget)))
        if alone:
            self._exclusive.add(id(controller))
            tabs = controller.tabs
            tabs.removeTab(tabs.indexOf(widget))
        if item.panel.startswith(CONTROL_PREFIX) and isinstance(widget, QtWidgets.QAbstractButton):
            widget.setMinimumHeight(min(widget.minimumHeight(), 28))  # a button fills its cells at any size
        return widget

    def _mount(self, item: PageItem, widget: Optional[QtWidgets.QWidget] = None) -> None:
        if item.kind == KIND_BUTTON:
            button = self.buttons[item.id] = ActionButton(self, binding_from(item.button))
            self.canvas.add_frame(item, button, "Button: " + (button.binding.label or "(no label)").replace("\n", " "),
                                  scrolls=False, editable=True)
            return
        widget = widget or self._take_panel(item)
        title = f"{item.source} › {self._labels.get(item.id) or panel_label(item.panel)}"
        if widget is None:
            widget = muted(f"{title}\nis not available in this version of Operator Board.")
            widget.setAlignment(Qt.AlignCenter)
            widget.setWordWrap(True)
        self.canvas.add_frame(item, widget, title, scrolls=True, editable=False)

    # ---- editing -------------------------------------------------------------------------
    def set_editing(self, on: bool) -> None:
        if self.edit_btn.isChecked() != on:
            self.edit_btn.setChecked(on)
            return
        self.build()
        self.edit_btn.setText("Done" if on else "Edit layout")
        self.add_btn.setVisible(on)
        self.hint.setVisible(on)
        self.canvas.set_editing(on)
        self._show_empty()

    def _fill_add_menu(self) -> None:
        menu = self.add_menu
        menu.clear()
        menu.addAction("Button…").triggered.connect(lambda _=False: self.add_button())
        menu.addSeparator()
        setup = QtWidgets.QMenu("Setup pages", menu)
        for title, is_setup, panels, controls in self.window_.panel_catalogue():
            sub = QtWidgets.QMenu(title.replace("&", "&&"), menu)
            for key, label in [(WHOLE_PAGE, "Whole page")] + panels:
                action = sub.addAction(label.replace("&", "&&"))
                action.triggered.connect(lambda _=False, t=title, k=key: self.add_panel(t, k))
                if key == WHOLE_PAGE and panels:
                    sub.addSeparator()
            if controls:  # single buttons, drop-downs and readouts, under the card they belong to
                sub.addSeparator()
                singles = sub.addMenu("Individual controls")
                by_card: Dict[str, QtWidgets.QMenu] = {}
                for card, key, label in controls:
                    if card not in by_card:
                        by_card[card] = singles.addMenu(card.replace("&", "&&"))
                    action = by_card[card].addAction(label.replace("&", "&&"))
                    action.triggered.connect(lambda _=False, t=title, k=key: self.add_panel(t, k))
            (setup if is_setup else menu).addMenu(sub)
        menu.addSeparator()
        menu.addMenu(setup)

    def _save(self) -> None:
        self.ctx.custom_pages.changed(layout_only=True)
        self._show_empty()

    def _layout_changed(self) -> None:
        self._save()

    def add_button(self, binding=None) -> Optional[PageItem]:
        binding = binding or edit_button(self, self.ctx)
        if binding is None:
            return None
        from operator_board.core.serde import to_dict
        w, h = DEFAULT_SIZE[KIND_BUTTON]
        item = self.page_.add(PageItem(kind=KIND_BUTTON, w=w, h=h, button=to_dict(binding)))
        self.build()
        self._mount(item)
        self._save()
        self._reveal(item)
        return item

    def add_panel(self, source: str, panel: str) -> Optional[PageItem]:
        self.build()
        item = PageItem(id=new_id(), kind=KIND_PANEL, source=source, panel=panel)
        widget = self._take_panel(item)
        if widget is None:
            self.notify(f"{source} › {panel_label(panel)} is not available", False)
            return None
        smallest = MIN_CONTROL if panel.startswith(CONTROL_PREFIX) else MIN_PANEL
        item.w, item.h = smallest
        self.page_.add(item)
        self._mount(item, widget)
        # Now that it is in the window and filled in, ask for its real size and start it at that, within limits.
        self._refresh_controllers()
        widget.ensurePolished()
        if widget.layout() is not None:
            widget.layout().activate()
        hint = widget.sizeHint().expandedTo(widget.minimumSizeHint())
        w, h = self.canvas.size_in_cells(hint + QtCore.QSize(0, 12))  # a little slack, so no scroll bar at first
        if not isinstance(widget, (Card, BasePage)) and not panel.startswith((TAB_PREFIX, CONTROL_PREFIX)):
            w = max(w, BAR_WIDTH)
        self.page_.items.remove(item)
        item.w = max(smallest[0], min(MAX_PANEL[0], w))
        item.h = max(smallest[1], min(MAX_PANEL[1], h))
        item.col, item.row = self.page_.free_spot(item.w, item.h)
        self.page_.items.append(item)
        self.canvas.relayout()
        self._save()
        self._reveal(item)
        return item

    def _reveal(self, item: PageItem) -> None:
        frame = self.canvas.frames.get(item.id)
        if frame is not None:
            QtCore.QTimer.singleShot(0, lambda: self.area.ensureWidgetVisible(frame))

    def edit_item(self, item_id: str) -> None:
        item = self.page_.item(item_id)
        if item is None or item.kind != KIND_BUTTON:
            return
        binding = edit_button(self, self.ctx, binding_from(item.button))
        if binding is None:
            return
        from operator_board.core.serde import to_dict
        item.button = to_dict(binding)
        self._save()
        self.rebuild()

    def remove_item(self, item_id: str) -> None:
        item = self.page_.item(item_id)
        if item is None:
            return
        self.page_.items.remove(item)
        self._save()
        self.rebuild()  # also frees the page behind a removed panel

    # ---- staying up to date -----------------------------------------------------------------
    def _refresh_controllers(self, every_second_only: bool = False) -> None:
        for controller in self.controllers:
            if not every_second_only or controller.refresh_every_second:
                controller.refresh()

    def refresh(self) -> None:
        if not self._built:
            return
        self._refresh_controllers()
        for button in self.buttons.values():
            button.refresh()

    def tick(self) -> None:
        """Once a second while this page is on screen."""
        self._refresh_controllers(every_second_only=True)
        for button in self.buttons.values():
            button.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.build()
        super().showEvent(event)  # refreshes

    def on_event(self, topic: str, payload) -> None:
        if not self.isVisible():  # only the editor's news reaches a page that is not on screen
            for controller in self.controllers:
                if topic in getattr(controller, "EDITOR_EVENTS", ()):
                    controller.on_event(topic, payload)
            return
        for controller in self.controllers:
            controller.on_event(topic, payload)
        for button in self.buttons.values():
            button.refresh()

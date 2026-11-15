"""Custom Pages: make, rename, reorder, duplicate, delete, import and export the operator's own pages.

The pages themselves are laid out on the page (``ui/pages/custom.py``): open one and click Edit layout.
"""
from __future__ import annotations

from operator_board.core import custom_pages
from operator_board.qt import QtWidgets, Qt
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, muted

FILE_FILTER = "Operator Board custom pages (*.json)"


class CustomPagesPage(BasePage):
    title = "Custom Pages"
    setup_page = True
    refresh_topics = ("custom_pages.",)
    in_catalogue = False  # not offered as a panel for a custom page

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        self.store = ctx.custom_pages
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        card = Card("Custom pages")
        card.add(muted("Your own pages, built from buttons and from the panels of the other pages. They appear in the "
                       "Custom group at the top of the side menu, in this order, and are saved with the project."))
        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(220)
        self.list.itemDoubleClicked.connect(lambda _item: self._open())
        self.list.itemSelectionChanged.connect(self._update_buttons)
        card.add(self.list)
        self.new_btn = QtWidgets.QPushButton("+ New page")
        self.open_btn = QtWidgets.QPushButton("Open and edit")
        self.rename_btn = QtWidgets.QPushButton("Rename")
        self.duplicate_btn = QtWidgets.QPushButton("Duplicate")
        self.up_btn = QtWidgets.QPushButton("▲ Move up")
        self.down_btn = QtWidgets.QPushButton("▼ Move down")
        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.new_btn.clicked.connect(lambda: self._new())
        self.open_btn.clicked.connect(self._open)
        self.rename_btn.clicked.connect(lambda: self._rename())
        self.duplicate_btn.clicked.connect(self._duplicate)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.delete_btn.clicked.connect(lambda: self._delete())
        card.add_layout(hbox(self.new_btn, self.open_btn, self.rename_btn, self.duplicate_btn, self.up_btn,
                             self.down_btn, self.delete_btn, None))
        self.import_btn = QtWidgets.QPushButton("Import…")
        self.import_btn.setToolTip("Add the pages from a custom pages file. Nothing is replaced: a page whose name is "
                                   "taken gets a number.")
        self.export_btn = QtWidgets.QPushButton("Export selected…")
        self.export_all_btn = QtWidgets.QPushButton("Export all…")
        self.import_btn.clicked.connect(lambda: self._import())
        self.export_btn.clicked.connect(lambda: self._export(True))
        self.export_all_btn.clicked.connect(lambda: self._export(False))
        card.add_layout(hbox(self.import_btn, self.export_btn, self.export_all_btn, None))
        card.add(muted("Project exports, templates and new projects made from this one carry these pages too. "
                       "A saved settings preset can hold them (Saved Settings > Custom pages), and the action "
                       "'Show custom page' opens one from a control surface key."))
        lay.addWidget(card)
        lay.addStretch(1)

    # ---- selection ------------------------------------------------------------------------
    def selected(self):
        rows = sorted(self.list.row(i) for i in self.list.selectedItems())
        return [self.store.pages[r] for r in rows if r < len(self.store.pages)]

    def _one(self):
        pages = self.selected()
        return pages[0] if pages else None

    def _select(self, page) -> None:
        self.refresh()
        if page in self.store.pages:
            self.list.clearSelection()
            self.list.setCurrentRow(self.store.pages.index(page))

    def _update_buttons(self) -> None:
        count = len(self.selected())
        for button in (self.open_btn, self.rename_btn, self.duplicate_btn, self.up_btn, self.down_btn):
            button.setEnabled(count == 1)
        self.delete_btn.setEnabled(count >= 1)
        self.export_btn.setEnabled(count >= 1)
        self.export_all_btn.setEnabled(bool(self.store.pages))

    # ---- actions --------------------------------------------------------------------------
    def _ask_name(self, title: str, current: str = "") -> str:
        name, ok = QtWidgets.QInputDialog.getText(self, title, "Page name", text=current)
        return " ".join(name.split()) if ok else ""

    def _new(self, name: str = "") -> None:
        name = name or self._ask_name("New custom page", self.store.free_name("Custom page"))
        if not name:
            return
        page = self.store.add(name)
        if page.name != name:
            self.notify(f"'{name}' is taken, so the page is called '{page.name}'")
        self._select(page)
        self.window_.show_page(page.name)
        view = self.window_.current_page()
        if getattr(view, "is_custom", False):
            view.set_editing(True)  # a new page is empty: go straight to adding items

    def _open(self) -> None:
        page = self._one()
        if page is not None:
            self.window_.show_page(page.name)

    def _rename(self, name: str = "") -> None:
        page = self._one()
        if page is None:
            return
        name = name or self._ask_name("Rename custom page", page.name)
        if not name or name == page.name:
            return
        given = self.store.rename(page, name)
        if given != name:
            self.notify(f"'{name}' is taken, so the page is called '{given}'")
        self._select(page)

    def _duplicate(self) -> None:
        page = self._one()
        if page is not None:
            self._select(self.store.duplicate(page))

    def _move(self, offset: int) -> None:
        page = self._one()
        if page is not None:
            self.store.move(page, offset)
            self._select(page)

    def _delete(self, confirm: bool = True) -> None:
        pages = self.selected()
        if not pages:
            return
        names = ", ".join(p.name for p in pages)
        if confirm and QtWidgets.QMessageBox.question(
                self, "Delete custom pages", f"Delete {names}?\n\nThis cannot be undone. Export first to keep a copy."
                ) != QtWidgets.QMessageBox.Yes:
            return
        for page in pages:
            self.store.remove(page)
        self.refresh()
        self.notify(f"Deleted {names}")

    def _import(self, path: str = "") -> None:
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import custom pages", "", FILE_FILTER)
        if not path:
            return
        try:
            pages = custom_pages.read_file(path)
        except (OSError, ValueError, TypeError) as exc:
            self.notify(f"Could not import {path}: {exc}", False)
            return
        added = self.store.add_pages(pages)
        self.refresh()
        self.notify(f"Imported {', '.join(p.name for p in added)}" if added else "The file holds no pages", bool(added))

    def _export(self, selected: bool, path: str = "") -> None:
        pages = self.selected() if selected else list(self.store.pages)
        if not pages:
            return
        if not path:
            suggested = (pages[0].name if len(pages) == 1 else "custom_pages") + ".json"
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export custom pages", suggested, FILE_FILTER)
        if not path:
            return
        try:
            custom_pages.write_file(path, pages)
        except OSError as exc:
            self.notify(f"Could not export to {path}: {exc}", False)
            return
        self.notify(f"Exported {len(pages)} page{'s' if len(pages) != 1 else ''} to {path}")

    # ---- display --------------------------------------------------------------------------
    def refresh(self) -> None:
        keep = {p.id for p in self.selected()}
        self.list.blockSignals(True)
        self.list.clear()
        for page in self.store.pages:
            count = len(page.items)
            item = QtWidgets.QListWidgetItem(f"{page.name}    ({count} item{'s' if count != 1 else ''})")
            item.setData(Qt.UserRole, page.id)
            self.list.addItem(item)
            if page.id in keep:
                item.setSelected(True)
        self.list.blockSignals(False)
        self._update_buttons()

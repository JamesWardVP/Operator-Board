"""Sidebar: pages in collapsible groups.

Keeps the small list-like interface the rest of the app uses (``count``, ``setCurrentRow``,
``currentRowChanged``), where a row is the page's index in the window's page stack.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple, Union

from operator_board.qt import QtGui, QtWidgets, Qt, Signal

PAGE_ROLE = Qt.UserRole + 1
# A title on its own is an ungrouped page; (group name, [titles]) is a collapsible group.
NavEntry = Union[str, Tuple[str, Sequence[str]]]


class NavTree(QtWidgets.QTreeWidget):
    currentRowChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Nav")
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(14)
        self.setUniformRowHeights(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self._items: Dict[int, QtWidgets.QTreeWidgetItem] = {}
        self._groups: List[QtWidgets.QTreeWidgetItem] = []
        self.itemClicked.connect(self._clicked)
        self.currentItemChanged.connect(self._current_changed)

    def build(self, entries: Sequence[NavEntry], titles: List[str]) -> None:
        """``titles`` are the page titles in stack order; every page must appear once in ``entries``."""
        self.clear()
        self._items.clear()
        self._groups.clear()
        for entry in entries:
            if isinstance(entry, str):
                self.addTopLevelItem(self._page_item(entry, titles))
                continue
            name, members = entry
            group = QtWidgets.QTreeWidgetItem([name.upper()])
            group.setFlags(Qt.ItemIsEnabled)  # a header, not a page: never selected
            group.setData(0, PAGE_ROLE, -1)
            font = QtGui.QFont(self.font())
            font.setBold(True)
            font.setPixelSize(12)
            font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.2)
            group.setFont(0, font)
            group.setForeground(0, QtGui.QBrush(QtGui.QColor("#8b949e")))
            self.addTopLevelItem(group)
            for title in members:
                group.addChild(self._page_item(title, titles))
            group.setExpanded(True)  # all groups open by default
            self._groups.append(group)
            self._set_arrow(group)
        missing = set(range(len(titles))) - set(self._items)
        if missing:
            raise ValueError("Pages missing from the sidebar: " + ", ".join(titles[i] for i in sorted(missing)))

    def _page_item(self, title: str, titles: List[str]) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem([title])
        index = titles.index(title)
        item.setData(0, PAGE_ROLE, index)
        self._items[index] = item
        return item

    def _set_arrow(self, group: QtWidgets.QTreeWidgetItem) -> None:
        name = group.text(0).lstrip("▾▸ ")
        group.setText(0, ("▾ " if group.isExpanded() else "▸ ") + name)

    def _clicked(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        if item in self._groups:
            item.setExpanded(not item.isExpanded())
            self._set_arrow(item)

    def _current_changed(self, current, _previous) -> None:
        index = current.data(0, PAGE_ROLE) if current is not None else -1
        if index is not None and index >= 0:
            self.currentRowChanged.emit(int(index))

    # ---- the list-like interface ---------------------------------------------------------
    def count(self) -> int:
        return len(self._items)

    def setCurrentRow(self, index: int) -> None:  # noqa: N802 - matches QListWidget
        item = self._items.get(index)
        if item is None:
            return
        parent = item.parent()
        if parent is not None and not parent.isExpanded():
            parent.setExpanded(True)
            self._set_arrow(parent)
        self.setCurrentItem(item)

    def currentRow(self) -> int:  # noqa: N802
        item = self.currentItem()
        index = item.data(0, PAGE_ROLE) if item is not None else -1
        return int(index) if index is not None else -1

    def group_names(self) -> List[str]:
        return [g.text(0).lstrip("▾▸ ") for g in self._groups]

    def set_group_expanded(self, name: str, expanded: bool) -> None:
        for g in self._groups:
            if g.text(0).lstrip("▾▸ ").lower() == name.lower():
                g.setExpanded(expanded)
                self._set_arrow(g)

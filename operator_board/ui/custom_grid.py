"""The snapping grid of a custom page.

``GridCanvas`` places one frame per item on a grid ``COLUMNS`` wide whose columns stretch with the
window and whose rows have a fixed height. In edit mode every frame is covered by an overlay: drag
it to move the item, drag its bottom-right corner to resize it; both snap to whole cells and an
item never lands on another one. A panel bigger than its frame scrolls inside it.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from operator_board.core.custom_pages import COLUMNS, CustomPage, PageItem
from operator_board.qt import QtCore, QtGui, QtWidgets, Qt, Signal
from operator_board.ui.theme import COLORS

ROW_HEIGHT = 44
MARGIN = 12
INSET = 4  # space around every item, so neighbours are 8 px apart
GRIP = 22  # size of the resize corner
SPARE_ROWS = 8  # empty rows below the last item while editing, to drag into


def _global_pos(event) -> QtCore.QPoint:
    if hasattr(event, "globalPosition"):
        return event.globalPosition().toPoint()
    return event.globalPos()


def _local_pos(event) -> QtCore.QPoint:
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


class EditOverlay(QtWidgets.QWidget):
    """Covers an item while the page is being edited: move, resize, edit and remove."""

    def __init__(self, frame: "ItemFrame") -> None:
        super().__init__(frame)
        self.frame = frame
        self.setMouseTracking(True)
        self._drag: Optional[Tuple[str, QtCore.QPoint, Tuple[int, int, int, int]]] = None
        self._moved = False
        self.remove_btn = self._tool("✕", "Remove this item from the page")
        self.remove_btn.clicked.connect(lambda: frame.canvas.remove_requested.emit(frame.item.id))
        self.edit_btn = self._tool("✎", "Change what this button does and how it looks")
        self.edit_btn.clicked.connect(lambda: frame.canvas.edit_requested.emit(frame.item.id))
        self.edit_btn.setVisible(frame.editable)

    def _tool(self, text: str, tip: str) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton(self)
        button.setText(text)
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(26, 24)
        button.setStyleSheet(f"QToolButton {{ background: {COLORS['panel2']}; border: 1px solid {COLORS['accent']};"
                             " border-radius: 5px; color: white; font-weight: 700; padding: 0; }"
                             f"QToolButton:hover {{ background: {COLORS['accent']}; }}")
        return button

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.remove_btn.move(self.width() - 32, 6)
        self.edit_btn.move(self.width() - 62, 6)

    def _in_grip(self, pos: QtCore.QPoint) -> bool:
        return pos.x() >= self.width() - GRIP and pos.y() >= self.height() - GRIP

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(18, 20, 23, 150))
        pen = QtGui.QPen(QtGui.QColor(COLORS["accent"]), 2, Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1), 9, 9)
        font = QtGui.QFont(self.font())
        font.setBold(True)
        font.setPixelSize(13)
        p.setFont(font)
        room = self.rect().adjusted(10, 8, -70, -8)
        flags = Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap
        text_rect = p.boundingRect(room, flags, self.frame.title)
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(COLORS["bg"]))  # a solid label, so the item's own text does not show through
        p.drawRoundedRect(QtCore.QRectF(text_rect).adjusted(-5, -3, 5, 3), 5, 5)
        p.setPen(QtGui.QColor("white"))
        p.drawText(room, flags, self.frame.title)
        # the resize corner
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(COLORS["accent"]))
        w, h = self.width(), self.height()
        p.drawPolygon(QtGui.QPolygon([QtCore.QPoint(w - 3, h - GRIP), QtCore.QPoint(w - 3, h - 3),
                                      QtCore.QPoint(w - GRIP, h - 3)]))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.LeftButton:
            return
        mode = "resize" if self._in_grip(_local_pos(event)) else "move"
        self._drag = (mode, _global_pos(event), self.frame.item.rect)
        self._moved = False
        self.frame.raise_()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag is None:
            self.setCursor(Qt.SizeFDiagCursor if self._in_grip(_local_pos(event)) else Qt.SizeAllCursor)
            return
        mode, start, (col, row, w, h) = self._drag
        delta = _global_pos(event) - start
        canvas = self.frame.canvas
        cols = int(round(delta.x() / canvas.cell_width()))
        rows = int(round(delta.y() / float(ROW_HEIGHT)))
        if mode == "move":
            done = canvas.place(self.frame.item.id, col + cols, row + rows, w, h)
        else:
            done = canvas.place(self.frame.item.id, col, row, max(1, min(COLUMNS - col, w + cols)), max(1, h + rows))
        self._moved = self._moved or done

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag is not None and self._moved:
            self.frame.canvas.changed.emit()
        self._drag = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.frame.editable:
            self.frame.canvas.edit_requested.emit(self.frame.item.id)


class ItemFrame(QtWidgets.QFrame):
    """One item on the grid: a button filling the frame, or a panel that scrolls when it is too big."""

    def __init__(self, canvas: "GridCanvas", item: PageItem, widget: QtWidgets.QWidget, title: str,
                 scrolls: bool, editable: bool) -> None:
        super().__init__(canvas)
        self.canvas, self.item, self.widget, self.title, self.editable = canvas, item, widget, title, editable
        self.setObjectName("PlainRow")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if scrolls:
            self.area = QtWidgets.QScrollArea()
            self.area.setWidgetResizable(True)  # fills the frame; scroll bars when it cannot shrink that far
            self.area.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.area.setWidget(widget)
            widget.show()
            lay.addWidget(self.area)
        else:
            self.area = None
            lay.addWidget(widget)
        self.overlay = EditOverlay(self)
        self.overlay.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())

    def set_editing(self, on: bool) -> None:
        self.overlay.setGeometry(self.rect())
        self.overlay.setVisible(on)
        if on:
            self.overlay.raise_()


class GridCanvas(QtWidgets.QWidget):
    changed = Signal()             # an item was moved or resized
    edit_requested = Signal(str)   # item id
    remove_requested = Signal(str)

    def __init__(self, page: CustomPage, parent=None) -> None:
        super().__init__(parent)
        self.page = page
        self.frames: Dict[str, ItemFrame] = {}
        self.editing = False
        self.setObjectName("PlainRow")
        self.setMinimumWidth(480)

    # ---- geometry --------------------------------------------------------------------------
    def cell_width(self) -> float:
        return max(4.0, (self.width() - 2 * MARGIN) / float(COLUMNS))

    def cell_rect(self, col: int, row: int, w: int, h: int) -> QtCore.QRect:
        cell = self.cell_width()
        left, right = MARGIN + col * cell, MARGIN + (col + w) * cell
        return QtCore.QRect(int(round(left)) + INSET, MARGIN + row * ROW_HEIGHT + INSET,
                            int(round(right)) - int(round(left)) - 2 * INSET, h * ROW_HEIGHT - 2 * INSET)

    def size_in_cells(self, size: QtCore.QSize) -> Tuple[int, int]:
        """Columns and rows needed to show something of ``size`` pixels."""
        cols = int(-(-(size.width() + 2 * INSET) // self.cell_width()))
        rows = int(-(-(size.height() + 2 * INSET) // ROW_HEIGHT))
        return max(1, min(COLUMNS, cols)), max(1, rows)

    def relayout(self) -> None:
        for frame in self.frames.values():
            frame.setGeometry(self.cell_rect(*frame.item.rect))
        rows = self.page.rows + (SPARE_ROWS if self.editing else 0)
        self.setMinimumHeight(2 * MARGIN + rows * ROW_HEIGHT)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.relayout()

    # ---- items -----------------------------------------------------------------------------
    def add_frame(self, item: PageItem, widget: QtWidgets.QWidget, title: str, scrolls: bool,
                  editable: bool) -> ItemFrame:
        frame = self.frames[item.id] = ItemFrame(self, item, widget, title, scrolls, editable)
        frame.setGeometry(self.cell_rect(*item.rect))
        frame.show()
        frame.set_editing(self.editing)
        self.relayout()
        return frame

    def clear(self) -> None:
        for frame in self.frames.values():
            frame.hide()
            frame.setParent(None)
            frame.deleteLater()
        self.frames = {}
        self.relayout()

    def place(self, item_id: str, col: int, row: int, w: int, h: int) -> bool:
        """Move and/or resize an item, if the new place is on the grid and free. True when it changed."""
        item = self.page.item(item_id)
        if item is None:
            return False
        w = max(1, min(COLUMNS, w))
        col = max(0, min(COLUMNS - w, col))  # moving to the edge never squeezes the item
        rect = (col, max(0, row), w, max(1, h))
        if rect == item.rect or not self.page.fits(rect, ignore=item):
            return False
        item.col, item.row, item.w, item.h = rect
        self.relayout()
        return True

    def set_editing(self, on: bool) -> None:
        self.editing = on
        for frame in self.frames.values():
            frame.set_editing(on)
        self.relayout()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.editing:
            return
        p = QtGui.QPainter(self)
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["border"]), 1))
        cell = self.cell_width()
        bottom = self.height() - MARGIN
        for col in range(COLUMNS + 1):
            x = int(round(MARGIN + col * cell))
            p.drawLine(x, MARGIN, x, bottom)
        y = MARGIN
        while y <= bottom:
            p.drawLine(MARGIN, y, self.width() - MARGIN, y)
            y += ROW_HEIGHT

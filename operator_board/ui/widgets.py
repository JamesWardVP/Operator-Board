"""Reusable operator widgets."""
from __future__ import annotations

import time
from typing import List, Optional, Sequence, Tuple

from operator_board.qt import QtCore, QtGui, QtWidgets, Qt
from operator_board.ui.theme import COLORS, STATE_COLORS


class Card(QtWidgets.QFrame):
    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout_ = QtWidgets.QVBoxLayout(self)
        self.layout_.setContentsMargins(14, 12, 14, 14)
        self.layout_.setSpacing(10)
        if title:
            label = QtWidgets.QLabel(title.upper())
            label.setObjectName("CardTitle")
            label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            self.layout_.addWidget(label)

    def add(self, widget) -> QtWidgets.QWidget:
        self.layout_.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.layout_.addLayout(layout)


class BigButton(QtWidgets.QPushButton):
    """Large stage button with distinct idle and active colours."""

    def __init__(self, text: str, active_color: str = COLORS["accent"], idle_color: str = COLORS["panel2"],
                 height: int = 84, parent=None) -> None:
        super().__init__(text, parent)
        self.active_color = active_color
        self.idle_color = idle_color
        self.setMinimumHeight(height)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        bg = self.active_color if active else self.idle_color
        border = "#ffffff" if active else COLORS["border"]
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: 2px solid {border}; border-radius: 12px;"
            f" font-size: 18px; font-weight: 800; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ border-color: {self.active_color}; }}")


class StateDot(QtWidgets.QLabel):
    def __init__(self, state: str = "disconnected", parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        color = STATE_COLORS.get(state, state if state.startswith("#") else "#495057")
        self.setStyleSheet(f"background: {color}; border-radius: 7px;")
        self.setToolTip(state)


class HazardOverlay(QtWidgets.QWidget):
    """Greys out a card with crossed hazard tape and a message, e.g. "LED wall unavailable".

    Sits on top of ``target`` (usually a Card) and follows its size. ``show_message`` sets the
    text and buttons; ``hide`` removes it.
    """

    TAPE_YELLOW = QtGui.QColor("#f5c400")
    TAPE_BLACK = QtGui.QColor("#16181c")

    def __init__(self, target: QtWidgets.QWidget) -> None:
        super().__init__(target)
        self.target = target
        target.installEventFilter(self)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.panel = QtWidgets.QFrame(self)
        self.panel.setObjectName("HazardPanel")
        self.panel.setStyleSheet(
            f"QFrame#HazardPanel {{ background: #121417; border: 2px solid {self.TAPE_YELLOW.name()};"
            " border-radius: 10px; }")
        lay = QtWidgets.QVBoxLayout(self.panel)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(6)
        self.title = QtWidgets.QLabel()
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(f"color: {self.TAPE_YELLOW.name()}; font-size: 18px; font-weight: 900;"
                                 " letter-spacing: 1px;")
        self.text = QtWidgets.QLabel()
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setWordWrap(True)
        self.text.setStyleSheet(f"color: {COLORS['text']};")
        self.buttons = QtWidgets.QHBoxLayout()
        self.buttons.setSpacing(8)
        lay.addWidget(self.title)
        lay.addWidget(self.text)
        lay.addLayout(self.buttons)
        self.hide()

    def show_message(self, title: str, text: str, buttons: Sequence[Tuple[str, object]] = ()) -> None:
        self.title.setText(title)
        self.text.setText(text)
        clear_layout(self.buttons)
        self.buttons.addStretch(1)
        for label, callback in buttons:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            self.buttons.addWidget(button)
        self.buttons.addStretch(1)
        self._place()
        self.show()
        self.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if obj is self.target and event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
            self._place()
        return False

    def _place(self) -> None:
        self.setGeometry(self.target.rect())
        hint = self.panel.sizeHint()
        width = min(max(hint.width(), 420), max(200, self.width() - 40))
        height = self.panel.heightForWidth(width) if self.panel.hasHeightForWidth() else hint.height()
        height = max(height, hint.height())
        self.panel.setGeometry((self.width() - width) // 2, (self.height() - height) // 2, width, height)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(self.rect()), 10, 10)
        p.setClipPath(path)
        p.fillRect(self.rect(), QtGui.QColor(10, 11, 13, 190))  # grey the card out
        w, h = self.width(), self.height()
        angle = 18 if w > 2 * h else 30
        for sign in (1, -1):  # two tapes crossing in an X
            p.save()
            p.translate(w / 2, h / 2)
            p.rotate(sign * angle)
            self._tape(p, length=int((w ** 2 + h ** 2) ** 0.5) + 40, thickness=30)
            p.restore()
        p.end()

    def _tape(self, p: QtGui.QPainter, length: int, thickness: int) -> None:
        band = QtCore.QRectF(-length / 2, -thickness / 2, length, thickness)
        p.fillRect(band, self.TAPE_YELLOW)
        p.save()
        p.setClipRect(band)
        p.setPen(Qt.NoPen)
        p.setBrush(self.TAPE_BLACK)
        stripe = thickness
        x = -length / 2 - thickness
        while x < length / 2 + thickness:
            p.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(x, thickness / 2), QtCore.QPointF(x + stripe / 2, thickness / 2),
                QtCore.QPointF(x + stripe / 2 + thickness, -thickness / 2), QtCore.QPointF(x + thickness, -thickness / 2)]))
            x += stripe
        p.restore()


class Banner(QtWidgets.QLabel):
    """Full-width status banner, e.g. 'WALL FROZEN'."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(44)
        self.show_state("", None)

    def show_state(self, text: str, color: Optional[str]) -> None:
        self.setText(text)
        self.setVisible(bool(text))
        if color:
            self.setStyleSheet(f"background: {color}; color: white; font-size: 20px; font-weight: 800;"
                               " border-radius: 8px; letter-spacing: 2px;")


class LineChart(QtWidgets.QWidget):
    """Dependency-free time-series line chart."""

    PALETTE = ["#3d8bfd", "#2f9e44", "#f08c00", "#e03131", "#7950f2", "#15aabf", "#e64980", "#82c91e"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.series: List[Tuple[str, Sequence[Tuple[float, float]]]] = []
        self.window_s = 300.0
        self.setMinimumHeight(220)

    def set_series(self, series: List[Tuple[str, Sequence[Tuple[float, float]]]]) -> None:
        self.series = series
        self.update()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(52, 12, -12, -28)
        p.fillRect(self.rect(), QtGui.QColor(COLORS["panel"]))
        now = time.time()
        t0 = now - self.window_s
        points = [(name, [(t, v) for t, v in data if t >= t0]) for name, data in self.series]
        values = [v for _, d in points for _, v in d]
        if not values:
            p.setPen(QtGui.QColor(COLORS["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "No data yet")
            return
        lo, hi = min(values), max(values)
        if hi - lo < 1e-6:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad
        grid = QtGui.QPen(QtGui.QColor(COLORS["border"]))
        p.setPen(grid)
        for i in range(5):
            y = rect.top() + rect.height() * i / 4
            p.drawLine(rect.left(), int(y), rect.right(), int(y))
            p.setPen(QtGui.QColor(COLORS["muted"]))
            p.drawText(4, int(y) + 4, f"{hi - (hi - lo) * i / 4:.1f}")
            p.setPen(grid)
        p.setPen(QtGui.QColor(COLORS["muted"]))
        p.drawText(rect.left(), self.height() - 8, f"-{int(self.window_s)}s")
        p.drawText(rect.right() - 30, self.height() - 8, "now")
        for idx, (name, data) in enumerate(points):
            if len(data) < 1:
                continue
            color = QtGui.QColor(self.PALETTE[idx % len(self.PALETTE)])
            p.setPen(QtGui.QPen(color, 2))
            path = QtGui.QPainterPath()
            for j, (t, v) in enumerate(data):
                x = rect.left() + rect.width() * (t - t0) / self.window_s
                y = rect.bottom() - rect.height() * (v - lo) / (hi - lo)
                path.moveTo(x, y) if j == 0 else path.lineTo(x, y)
            p.drawPath(path)
            p.drawText(rect.left() + 8 + 150 * idx, rect.top() + 14, name[:22])


def clear_layout(layout) -> None:
    """Remove and destroy everything in a layout (hiding first so nothing ghosts before deletion)."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def hbox(*widgets, spacing: int = 8) -> QtWidgets.QHBoxLayout:
    lay = QtWidgets.QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QtWidgets.QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def muted(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def scroll(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    area = QtWidgets.QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


def make_table(headers: List[str]) -> QtWidgets.QTableWidget:
    t = QtWidgets.QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    header = t.horizontalHeader()
    header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
    header.setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    return t


def set_row(table: QtWidgets.QTableWidget, row: int, values: Sequence) -> None:
    if table.rowCount() <= row:
        table.setRowCount(row + 1)
    for col, value in enumerate(values):
        table.setItem(row, col, QtWidgets.QTableWidgetItem("" if value is None else str(value)))

"""Reusable operator widgets."""
from __future__ import annotations

import math
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
        self._header = None
        if title:
            label = QtWidgets.QLabel(title.upper())
            label.setObjectName("CardTitle")
            label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            self._header = QtWidgets.QHBoxLayout()
            self._header.setContentsMargins(0, 0, 0, 0)
            self._header.addWidget(label, 1)
            self.layout_.addLayout(self._header)

    def set_link(self, tooltip: str, callback) -> QtWidgets.QPushButton:
        """A small button in the card's top-right corner, e.g. to open the page behind the card."""
        button = QtWidgets.QPushButton("\u2197")
        button.setObjectName("CardLink")
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(30, 26)
        button.clicked.connect(callback)
        if self._header is None:
            self._header = QtWidgets.QHBoxLayout()
            self._header.addStretch(1)
            self.layout_.insertLayout(0, self._header)
        self._header.addWidget(button, 0, Qt.AlignRight | Qt.AlignTop)
        return button

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


class RecordButton(BigButton):
    """Record / Stop button that plays Take Recorder's countdown on itself.

    Each number pops in large and shrinks and fades as its second runs out, then "GO!" flashes
    as recording really starts. ``remaining`` returns the seconds left (0 = not counting down).
    """

    GO_S = 0.7

    def __init__(self, remaining, height: int = 60, parent=None) -> None:
        super().__init__("● RECORD", COLORS["err"], height=height, parent=parent)
        self._remaining = remaining
        self._go_until = 0.0
        self._counting = False
        self._recording = False
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    def show_state(self, recording: bool, counting_down: bool) -> None:
        if self._counting and not counting_down and recording:
            self._go_until = time.monotonic() + self.GO_S
        self._counting = counting_down
        self._recording = recording
        busy = counting_down or time.monotonic() < self._go_until
        self.setText("" if busy else ("■ STOP" if recording else "● RECORD"))
        self.setToolTip("Cancel the countdown" if counting_down else "")
        self.set_active(recording and not counting_down)
        if busy and not self._timer.isActive():
            self._timer.start(16)
        self.update()

    def _tick(self) -> None:
        if not self._counting and time.monotonic() >= self._go_until:
            self._timer.stop()
            self.setText("■ STOP" if self._recording else "● RECORD")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        now = time.monotonic()
        if self._counting:
            left = max(0.0, float(self._remaining()))
            number = max(1, int(math.ceil(left)))
            text, phase = str(number), min(1.0, max(0.0, number - left))  # 0 when the number appears, 1 at its end
            colour = QtGui.QColor("#ffd43b")
        elif now < self._go_until:
            text, phase = "GO!", 1.0 - (self._go_until - now) / self.GO_S
            colour = QtGui.QColor("#69db7c")
        else:
            return
        pop = 1.0 - (1.0 - phase) ** 3  # ease out: fast pop, slow settle
        scale = 1.6 - 0.6 * pop
        alpha = 1.0 if phase < 0.6 else max(0.0, 1.0 - (phase - 0.6) / 0.4)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setOpacity(alpha)
        font = QtGui.QFont(self.font())
        font.setPixelSize(max(10, int(self.height() * 0.55 * scale)))
        font.setWeight(QtGui.QFont.Weight.Black if hasattr(QtGui.QFont, "Weight") else QtGui.QFont.Black)
        path = QtGui.QPainterPath()
        metrics = QtGui.QFontMetrics(font)
        width = metrics.horizontalAdvance(text) if hasattr(metrics, "horizontalAdvance") else metrics.width(text)
        path.addText((self.width() - width) / 2.0, (self.height() + metrics.capHeight()) / 2.0, font, text)
        painter.setPen(QtGui.QPen(QtGui.QColor("#000000"), max(2.0, font.pixelSize() / 10.0), Qt.SolidLine,
                                  Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(colour)
        painter.drawPath(path)
        painter.end()


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


class WallStatusBar(QtWidgets.QWidget):
    """Always-visible bar showing what the LED wall is doing.

    Normal: green. Frozen: blue with a moving shimmer. Blacked out: slowly scrolling hazard stripes.
    Test pattern: a picture of the pattern (the colour, colour bars, a grid, lines...).
    Unavailable (no processor reachable): grey.
    """

    SOLID = {"red": "#ff0000", "green": "#00ff00", "blue": "#0000ff", "white": "#ffffff", "black": "#000000"}
    BARS = ["#c0c0c0", "#c0c000", "#00c0c0", "#00c000", "#c000c0", "#c00000", "#0000c0"]  # SMPTE-style bars

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(46)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.state, self.pattern, self._text = "normal", "", "WALL NORMAL"
        self._phase = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    def text(self) -> str:
        return self._text

    def show_wall(self, state: str, pattern: str = "", label: str = "") -> None:
        """state: normal | frozen | blackout | pattern | unavailable"""
        texts = {"normal": "WALL NORMAL", "frozen": "WALL FROZEN", "blackout": "WALL BLACKED OUT",
                 "unavailable": "WALL STATUS UNKNOWN: NO LED PROCESSOR CONNECTED",
                 "pattern": f"TEST PATTERN: {(label or pattern).upper()}"}
        text = texts.get(state, state.upper())
        if (state, pattern, text) == (self.state, self.pattern, self._text):
            return
        self.state, self.pattern, self._text = state, pattern, text
        animated = state in ("frozen", "blackout") or (state == "pattern" and pattern == "aging")
        if animated and not self._timer.isActive():
            self._timer.start(33)
        elif not animated:
            self._timer.stop()
        self.setToolTip(text)
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.033) % 3600.0
        self.update()

    # ---- painting ---------------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(self.rect())
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(rect, 8, 8)
        p.setClipPath(clip)
        dark_text = False
        if self.state == "frozen":
            self._paint_shimmer(p, rect)
        elif self.state == "blackout":
            self._paint_hazard(p, rect)
        elif self.state == "pattern":
            dark_text = self._paint_pattern(p, rect)
        else:
            p.fillRect(rect, QtGui.QColor(COLORS["ok"] if self.state == "normal" else "#495057"))
        p.setClipping(False)
        self._paint_label(p, rect, boxed=self.state in ("blackout", "pattern"), dark=dark_text)
        p.end()

    def _paint_shimmer(self, p, rect) -> None:
        p.fillRect(rect, QtGui.QColor("#1971c2"))
        width = rect.width()
        centre = ((self._phase * 0.45) % 1.6 - 0.3) * width  # a band of light sweeping across, then a pause
        band = QtGui.QLinearGradient(centre - width * 0.18, 0, centre + width * 0.18, rect.height())
        band.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
        band.setColorAt(0.5, QtGui.QColor(190, 235, 255, 150))
        band.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
        p.fillRect(rect, band)
        glow = QtGui.QLinearGradient(0, 0, 0, rect.height())
        glow.setColorAt(0.0, QtGui.QColor(255, 255, 255, 40))
        glow.setColorAt(1.0, QtGui.QColor(0, 0, 0, 40))
        p.fillRect(rect, glow)

    def _paint_hazard(self, p, rect) -> None:
        p.fillRect(rect, QtGui.QColor("#111111"))
        stripe = 22.0
        offset = (self._phase * 18.0) % (stripe * 2)  # slow drift
        h = rect.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor("#f5c400"))
        x = -h - stripe * 2 + offset
        while x < rect.width() + h:
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x, h), QtCore.QPointF(x + stripe, h),
                                           QtCore.QPointF(x + stripe + h, 0), QtCore.QPointF(x + h, 0)]))
            x += stripe * 2

    def _paint_pattern(self, p, rect) -> bool:
        """Draw the test pattern; returns True if the label needs dark text."""
        pattern = (self.pattern or "").lower()
        w, h = rect.width(), rect.height()
        if pattern in self.SOLID:
            p.fillRect(rect, QtGui.QColor(self.SOLID[pattern]))
            return pattern in ("white", "green")
        if pattern == "aging":  # the processor cycles colours
            colours = ["#ff0000", "#00ff00", "#0000ff", "#ffffff"]
            p.fillRect(rect, QtGui.QColor(colours[int(self._phase / 1.5) % len(colours)]))
            return False
        if pattern in ("smpte-bars", "smpte_bars", "bars", "colour_bars", "color_bars", "brompton"):
            n = len(self.BARS)
            for i, colour in enumerate(self.BARS):
                p.fillRect(QtCore.QRectF(w * i / n, 0, w / n + 1, h * 0.72), QtGui.QColor(colour))
            reverse = ["#0000c0", "#111111", "#c000c0", "#111111", "#00c0c0", "#111111", "#c0c0c0"]
            for i, colour in enumerate(reverse):
                p.fillRect(QtCore.QRectF(w * i / n, h * 0.72, w / n + 1, h * 0.28), QtGui.QColor(colour))
            return False
        if pattern == "grayscale":
            steps = 11
            for i in range(steps):
                v = round(255 * i / (steps - 1))
                p.fillRect(QtCore.QRectF(w * i / steps, 0, w / steps + 1, h), QtGui.QColor(v, v, v))
            return False
        p.fillRect(rect, QtGui.QColor("#000000"))
        p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.5))
        spacing = 12.0
        if pattern in ("grid", "vertical"):
            x = 0.0
            while x <= w:
                p.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, h))
                x += spacing
        if pattern in ("grid", "horizontal"):
            y = 0.0
            while y <= h:
                p.drawLine(QtCore.QPointF(0, y), QtCore.QPointF(w, y))
                y += spacing
        if pattern == "diagonal":
            x = -h
            while x <= w:
                p.drawLine(QtCore.QPointF(x, h), QtCore.QPointF(x + h, 0))
                x += spacing
        if pattern not in ("grid", "vertical", "horizontal", "diagonal"):  # unknown pattern: amber
            p.fillRect(rect, QtGui.QColor("#a07400"))
        return False

    def _paint_label(self, p, rect, boxed: bool, dark: bool) -> None:
        font = QtGui.QFont(self.font())
        font.setPixelSize(19)
        font.setBold(True)
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 2)
        p.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        tw = metrics.horizontalAdvance(self._text) if hasattr(metrics, "horizontalAdvance") else metrics.width(self._text)
        if boxed:  # a dark pill keeps the words readable over stripes and patterns
            pill = QtCore.QRectF((rect.width() - tw) / 2 - 16, (rect.height() - metrics.height()) / 2 - 3,
                                 tw + 32, metrics.height() + 6)
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(255, 255, 255, 215) if dark else QtGui.QColor(0, 0, 0, 190))
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        p.setPen(QtGui.QColor("#111111") if dark else QtGui.QColor("#ffffff"))
        p.drawText(rect, Qt.AlignCenter, self._text)


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


class EvenColumnsTable(QtWidgets.QTableWidget):
    """Columns share the width equally; a column whose text needs more gets it, and the table
    then scrolls sideways instead of cutting values off. Call ``refit()`` after filling it."""

    def __init__(self, rows: int, columns: int, parent=None) -> None:
        super().__init__(rows, columns, parent)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)

    def refit(self) -> None:
        columns = self.columnCount()
        if not columns:
            return
        self.resizeColumnsToContents()
        needed = [self.columnWidth(c) + 12 for c in range(columns)]
        share = self.viewport().width() // columns
        widths = [max(n, share) for n in needed]
        spare = self.viewport().width() - sum(widths)
        if spare > 0:  # rounding: give the last few pixels to the last column
            widths[-1] += spare
        for c, w in enumerate(widths):
            self.setColumnWidth(c, w)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.refit()


def make_searchable(combo: QtWidgets.QComboBox, threshold: int = 8) -> None:
    """Once a drop-down has more than ``threshold`` entries, let the operator type to filter it
    (matches anywhere in the name, e.g. "apc" finds "Akai APC mini")."""
    if combo.count() <= threshold:
        return
    combo.setEditable(True)
    combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
    combo.lineEdit().setPlaceholderText("Type to search…")
    completer = combo.completer()
    completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
    completer.setFilterMode(Qt.MatchContains)
    completer.setCaseSensitivity(Qt.CaseInsensitive)


def local_addresses() -> List[str]:
    """This PC's IPv4 addresses on its networks (for "open this on your phone" links)."""
    import socket
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def make_table(headers: List[str], even: bool = False) -> QtWidgets.QTableWidget:
    t = EvenColumnsTable(0, len(headers)) if even else QtWidgets.QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    header = t.horizontalHeader()
    if not even:
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


class ColorField(QtWidgets.QWidget):
    """A colour as a hex code you can type, plus a swatch that opens a colour picker."""
    changed = QtCore.Signal(str)

    def __init__(self, title: str = "Colour", parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("#rrggbb")
        self.edit.setMaxLength(9)
        self.edit.setFixedWidth(96)
        self.edit.textChanged.connect(self._typed)
        self.swatch = QtWidgets.QPushButton()
        self.swatch.setFixedSize(44, 28)
        self.swatch.setToolTip("Pick a colour")
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.clicked.connect(self.pick)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.swatch)
        lay.addWidget(self.edit)
        lay.addStretch(1)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802 - matches QLineEdit
        self.edit.setText(value or "")

    def _typed(self, value: str) -> None:
        color = QtGui.QColor(value.strip())
        shown = color.name() if color.isValid() else "transparent"
        self.swatch.setStyleSheet(f"QPushButton {{ background: {shown}; border: 1px solid {COLORS['border']};"
                                  " border-radius: 6px; }")
        if color.isValid():
            self.changed.emit(color.name())

    def pick(self) -> None:
        current = QtGui.QColor(self.text())
        chosen = QtWidgets.QColorDialog.getColor(current if current.isValid() else QtGui.QColor("#2b2f36"),
                                                 self, self.title)
        if chosen.isValid():
            self.edit.setText(chosen.name())

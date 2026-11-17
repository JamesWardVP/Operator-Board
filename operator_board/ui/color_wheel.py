"""Colour grading controls in the style of a colourist's panel (Assimilate Live FX, Resolve).

``ColorWheel``: a hue wheel with a puck. Dragging the puck tints the image (the r, g, b parts of
Unreal's Vector4); scrolling changes brightness (the 4th part). The puck moves with the drag rather
than jumping to the click, Shift makes it ten times finer, a double-click resets the wheel and the
arrow keys nudge it. ``WheelControl`` adds the colourist title with Unreal's name in italics, a
brightness slider and typed values (RGB numbers or a hex tint colour). ``GradeSlider`` is the
Temperature / Tint control.

Every control reports a drag as ``gestureStarted``, ``changed`` (often), ``gestureFinished``; a single
change (typed value, double-click, key press) is the same three signals at once. ``set_value`` never
emits, so the page can show Unreal's values without echoing them back.

The wheel is the only colour on the page and it is painted at low strength on purpose: the operator
judges colour on the LED wall, not on the screen.
"""
from __future__ import annotations

import math
from typing import List, Tuple

from operator_board.core.grading import NEUTRAL, NEUTRAL_TEMP, NEUTRAL_TINT, Control, hex_to_tint, tint_hex
from operator_board.qt import QtCore, QtGui, QtWidgets, Qt
from operator_board.ui.theme import COLORS

Vec4 = Tuple[float, float, float, float]

# Where each channel points on the wheel, counter-clockwise from the right (red upper left, as on
# colourists' wheels); a third of a turn apart, so the three always balance.
_ANGLES = {"r": math.radians(103.0), "g": math.radians(223.0), "b": math.radians(343.0)}
FINE = 0.1            # Shift: ten times finer
SCROLL_STEP = 0.01    # brightness per scroll notch (Shift: a tenth)
KEY_STEP = 0.02       # puck movement per arrow key press, as a share of the wheel's radius
SCROLL_IDLE_MS = 400  # scrolling counts as one gesture until it pauses this long


def channel_direction(channel: str) -> Tuple[float, float]:
    """The screen direction (x right, y down) that raises ``channel``."""
    a = _ANGLES[channel]
    return math.cos(a), -math.sin(a)


def _neutral(control: Control) -> float:
    return float(NEUTRAL.get(control.part, (1.0,))[0])


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class ColorWheel(QtWidgets.QWidget):
    changed = QtCore.Signal(tuple)
    gestureStarted = QtCore.Signal()
    gestureFinished = QtCore.Signal()

    def __init__(self, control: Control, parent=None) -> None:
        super().__init__(parent)
        self.control = control
        self.neutral = _neutral(control)
        self.scale = (control.hi - control.lo) / 4.0  # how far the rim moves a channel from neutral
        self._px, self._py = 0.0, 0.0                   # the puck, in the unit disc (y up)
        self._common = 0.0                              # r, g, b raised together (kept, not shown)
        self._y = self.neutral
        self._dragging = False
        self._last = None
        self._scroll_timer = QtCore.QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(SCROLL_IDLE_MS)
        self._scroll_timer.timeout.connect(self.gestureFinished.emit)
        self._ring = None
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName(f"{control.title} colour wheel")
        self.setToolTip("Drag to tint. Shift: finer. Scroll: brightness. Double-click: reset. Arrow keys: nudge.")

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(170, 170)

    # ---- value ----------------------------------------------------------------------------------
    def value(self) -> Vec4:
        lo, hi = self.control.lo, self.control.hi
        rgb = []
        for c in "rgb":
            a = _ANGLES[c]
            dev = self._common + self.scale * (self._px * math.cos(a) + self._py * math.sin(a))
            rgb.append(_clamp(self.neutral + dev, lo, hi))
        return (rgb[0], rgb[1], rgb[2], _clamp(self._y, lo, hi))

    def set_value(self, value) -> None:
        r, g, b, y = (float(v) for v in tuple(value)[:4])
        devs = {"r": r - self.neutral, "g": g - self.neutral, "b": b - self.neutral}
        self._common = sum(devs.values()) / 3.0
        px = sum((devs[c] - self._common) * math.cos(_ANGLES[c]) for c in "rgb") * (2.0 / 3.0) / self.scale
        py = sum((devs[c] - self._common) * math.sin(_ANGLES[c]) for c in "rgb") * (2.0 / 3.0) / self.scale
        self._px, self._py = px, py  # exact, even past the rim (a typed value); drawn at the rim
        self._y = y
        self.update()

    def _move_puck(self, dx: float, dy: float) -> None:
        """Move the puck; a drag cannot take it further out than the rim (or than it already was)."""
        limit = max(1.0, math.hypot(self._px, self._py))
        px, py = self._px + dx, self._py + dy
        d = math.hypot(px, py)
        if d > limit:
            px, py = px / d * limit, py / d * limit
        self._px, self._py = px, py
        self.update()
        self.changed.emit(self.value())

    def reset(self) -> None:
        """Back to neutral (no tint, neutral brightness) as one change."""
        self.gestureStarted.emit()
        self._px = self._py = self._common = 0.0
        self._y = self.neutral
        self.update()
        self.changed.emit(self.value())
        self.gestureFinished.emit()

    # ---- geometry ---------------------------------------------------------------------------------
    def _radius(self) -> float:
        return max(10.0, min(self.width(), self.height()) / 2.0 - 8.0)

    def _centre(self) -> QtCore.QPointF:
        return QtCore.QPointF(self.width() / 2.0, self.height() / 2.0)

    # ---- mouse and keys -----------------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._dragging = True
        self._last = event.position() if hasattr(event, "position") else QtCore.QPointF(event.pos())
        self.gestureStarted.emit()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging or self._last is None:
            return
        pos = event.position() if hasattr(event, "position") else QtCore.QPointF(event.pos())
        factor = FINE if event.modifiers() & Qt.ShiftModifier else 1.0
        r = self._radius()
        self._move_puck((pos.x() - self._last.x()) / r * factor, -(pos.y() - self._last.y()) / r * factor)
        self._last = pos

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            self._last = None
            self.gestureFinished.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self.reset()

    def wheelEvent(self, event) -> None:
        notches = event.angleDelta().y() / 120.0
        if not notches:
            return
        step = SCROLL_STEP * (FINE if event.modifiers() & Qt.ShiftModifier else 1.0)
        if not self._scroll_timer.isActive():
            self.gestureStarted.emit()
        self._y = _clamp(self._y + notches * step, self.control.lo, self.control.hi)
        self.update()
        self.changed.emit(self.value())
        self._scroll_timer.start()
        event.accept()

    def keyPressEvent(self, event) -> None:
        moves = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, 1), Qt.Key_Down: (0, -1)}
        if event.key() in moves:
            step = KEY_STEP * (FINE if event.modifiers() & Qt.ShiftModifier else 1.0)
            dx, dy = moves[event.key()]
            self.gestureStarted.emit()
            self._move_puck(dx * step, dy * step)
            self.gestureFinished.emit()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.reset()
        else:
            super().keyPressEvent(event)

    # ---- painting -----------------------------------------------------------------------------------
    def _ring_image(self, size: int) -> QtGui.QImage:
        """The hue disc at low strength, fading to the panel grey at the centre (cached per size)."""
        if self._ring is not None and self._ring.width() == size:
            return self._ring
        img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(0.5, 0.5, size - 1.0, size - 1.0)
        hues = QtGui.QConicalGradient(rect.center(), 0.0)
        for step in range(13):
            a = math.radians(step * 30.0)
            rgb = [0.5 + 0.5 * math.cos(a - _ANGLES[c]) for c in "rgb"]
            # Low strength: mostly grey, a little hue, dim.
            grey = 0.42
            col = [int(255 * (grey + 0.32 * (v - 0.5)) * 0.85) for v in rgb]
            hues.setColorAt(step / 12.0, QtGui.QColor(*col))
        p.setPen(Qt.NoPen)
        p.setBrush(hues)
        p.drawEllipse(rect)
        fade = QtGui.QRadialGradient(rect.center(), size / 2.0)
        centre = QtGui.QColor(COLORS["panel2"])
        fade.setColorAt(0.0, centre)
        edge = QtGui.QColor(centre)
        edge.setAlpha(0)
        fade.setColorAt(1.0, edge)
        p.setBrush(fade)
        p.drawEllipse(rect)
        p.end()
        self._ring = img
        return img

    def paintEvent(self, _event) -> None:
        r = self._radius()
        c = self._centre()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        size = int(r * 2)
        p.drawImage(QtCore.QPointF(c.x() - r, c.y() - r), self._ring_image(size))
        border = QtGui.QColor(COLORS["accent"] if self.hasFocus() else COLORS["border"])
        p.setPen(QtGui.QPen(border, 2 if self.hasFocus() else 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r, r)
        # Crosshair and the neutral point.
        muted = QtGui.QColor(COLORS["muted"])
        muted.setAlpha(110)
        p.setPen(QtGui.QPen(muted, 1))
        p.drawLine(QtCore.QPointF(c.x() - r, c.y()), QtCore.QPointF(c.x() + r, c.y()))
        p.drawLine(QtCore.QPointF(c.x(), c.y() - r), QtCore.QPointF(c.x(), c.y() + r))
        p.drawEllipse(c, 3, 3)
        # The puck, with a line from neutral so a small tint is still easy to read.
        shown = max(1.0, math.hypot(self._px, self._py))
        puck = QtCore.QPointF(c.x() + self._px / shown * r, c.y() - self._py / shown * r)
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["text"]), 1))
        if math.hypot(self._px, self._py) > 0.01:
            p.drawLine(c, puck)
        rgb = self.value()[:3]
        tint = QtGui.QColor(tint_hex(rgb, self.neutral))
        p.setBrush(tint)
        p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 3))
        p.drawEllipse(puck, 7, 7)
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["text"]), 2))
        p.drawEllipse(puck, 7, 7)
        p.end()


# Grey sliders: the wheels are the only colour on the page.
NEUTRAL_SLIDER = (
    "QSlider::groove:horizontal { height: 4px; background: %(border)s; border-radius: 2px; }"
    "QSlider::sub-page:horizontal { background: %(muted)s; border-radius: 2px; }"
    "QSlider::handle:horizontal { background: %(text)s; width: 14px; margin: -6px 0; border-radius: 7px; }"
    "QSlider::handle:horizontal:focus { background: %(accent)s; }"
) % COLORS


def _title_labels(control: Control) -> Tuple[QtWidgets.QLabel, QtWidgets.QLabel]:
    title = QtWidgets.QLabel(control.title)
    font = title.font()
    font.setBold(True)
    font.setPixelSize(15)
    title.setFont(font)
    subtitle = QtWidgets.QLabel(control.subtitle)
    subtitle.setObjectName("Muted")
    sfont = subtitle.font()
    sfont.setItalic(True)
    sfont.setPixelSize(12)
    subtitle.setFont(sfont)
    subtitle.setToolTip("The setting's name in Unreal")
    return title, subtitle


def _parse(text: str):
    try:
        v = float(text.strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


class WheelControl(QtWidgets.QWidget):
    """One wheel with its title, brightness slider and typed values."""
    changed = QtCore.Signal(tuple)
    gestureStarted = QtCore.Signal()
    gestureFinished = QtCore.Signal()
    SLIDER_STEPS = 1000

    def __init__(self, control: Control, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PlainRow")
        self.control = control
        self.mode = "rgb"
        self._setting = False
        self._slider_active = False
        self.title, self.subtitle = _title_labels(control)
        self.wheel = ColorWheel(control)
        self.brightness = QtWidgets.QSlider(Qt.Horizontal)
        self.brightness.setRange(0, self.SLIDER_STEPS)
        self.brightness.setStyleSheet(NEUTRAL_SLIDER)
        self.brightness.setAccessibleName(f"{control.title} brightness")
        self.brightness.setToolTip("Brightness. Double-click the wheel to reset.")
        self.fields: List[QtWidgets.QLineEdit] = []
        for name in ("Red", "Green", "Blue", "Brightness"):
            f = QtWidgets.QLineEdit()
            f.setFixedWidth(58)
            f.setAlignment(Qt.AlignRight)
            f.setAccessibleName(f"{control.title} {name.lower()}")
            f.setToolTip(name)
            f.editingFinished.connect(lambda i=len(self.fields): self._typed(i))
            self.fields.append(f)
        self.hex_field = QtWidgets.QLineEdit()
        self.hex_field.setAccessibleName(f"{control.title} tint colour")
        self.hex_field.setToolTip("Tint colour as #RRGGBB (brightness stays as it is)")
        self.hex_field.setPlaceholderText("#FFE8DD")
        self.hex_field.editingFinished.connect(self._typed_hex)
        self.mode_button = QtWidgets.QPushButton("RGB")
        self.mode_button.setToolTip("Switch between RGB numbers and a hex tint colour")
        self.mode_button.setAccessibleName(f"{control.title}: switch between RGB and hex")
        self.mode_button.setFixedWidth(56)
        self.mode_button.clicked.connect(self._cycle)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.title, 0, Qt.AlignHCenter)
        lay.addWidget(self.subtitle, 0, Qt.AlignHCenter)
        lay.addWidget(self.wheel, 1)
        lay.addWidget(self.brightness)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        for f in self.fields:
            row.addWidget(f)
        row.addWidget(self.hex_field, 1)
        row.addWidget(self.mode_button)
        lay.addLayout(row)
        self.hex_field.hide()

        self.wheel.changed.connect(self._wheel_changed)
        self.wheel.gestureStarted.connect(self.gestureStarted.emit)
        self.wheel.gestureFinished.connect(self.gestureFinished.emit)
        self.brightness.sliderPressed.connect(self._slider_pressed)
        self.brightness.sliderReleased.connect(self._slider_released)
        self.brightness.valueChanged.connect(self._slider_moved)
        self.set_value(NEUTRAL.get(control.part, (1.0, 1.0, 1.0, 1.0)))

    # ---- value ----------------------------------------------------------------------------------
    def value(self) -> Vec4:
        return self.wheel.value()

    def set_value(self, value) -> None:
        self._setting = True
        try:
            self.wheel.set_value(value)
            self._show(self.wheel.value())
        finally:
            self._setting = False

    def _show(self, v: Vec4) -> None:
        self._setting, was = True, self._setting
        try:
            lo, hi = self.control.lo, self.control.hi
            self.brightness.setValue(int(round((v[3] - lo) / (hi - lo) * self.SLIDER_STEPS)))
            for f, x in zip(self.fields, v):
                f.setText(f"{round(x, 3) + 0.0:.3f}")
            self.hex_field.setText(tint_hex(v[:3], self.wheel.neutral))
        finally:
            self._setting = was

    def _emit_one(self, v) -> None:
        """A single change (typed value): the same signals as a very short drag."""
        self.wheel.set_value(v)
        v = self.wheel.value()
        self._show(v)
        self.gestureStarted.emit()
        self.changed.emit(v)
        self.gestureFinished.emit()

    def _wheel_changed(self, v) -> None:
        self._show(v)
        self.changed.emit(v)

    # ---- brightness slider ---------------------------------------------------------------------------
    def _slider_pressed(self) -> None:
        self._slider_active = True
        self.gestureStarted.emit()

    def _slider_released(self) -> None:
        if self._slider_active:
            self._slider_active = False
            self.gestureFinished.emit()

    def _slider_moved(self, step: int) -> None:
        if self._setting:
            return
        lo, hi = self.control.lo, self.control.hi
        r, g, b, _y = self.wheel.value()
        y = lo + (hi - lo) * step / self.SLIDER_STEPS
        if self._slider_active:
            self.wheel.set_value((r, g, b, y))
            v = self.wheel.value()
            self._show(v)
            self.changed.emit(v)
        else:  # keyboard or a click on the groove: one change
            self._emit_one((r, g, b, y))

    # ---- typed values -------------------------------------------------------------------------------
    def _typed(self, index: int) -> None:
        current = list(self.wheel.value())
        typed = _parse(self.fields[index].text())
        if typed is None:
            self._show(tuple(current))
            return
        wanted = list(current)
        wanted[index] = _clamp(typed, self.control.lo, self.control.hi)
        if abs(wanted[index] - current[index]) < 1e-9:
            self._show(tuple(current))
            return
        self._emit_one(tuple(wanted))

    def _typed_hex(self) -> None:
        current = self.wheel.value()
        try:
            r, g, b, y = hex_to_tint(self.hex_field.text(), current[3], self.wheel.neutral)
        except ValueError:
            self._show(current)
            return
        lo, hi = self.control.lo, self.control.hi
        wanted = (_clamp(r, lo, hi), _clamp(g, lo, hi), _clamp(b, lo, hi), current[3])
        if all(abs(a - b_) < 1e-9 for a, b_ in zip(wanted, current)):
            self._show(current)
            return
        self._emit_one(wanted)

    def _cycle(self) -> None:
        self.mode = "hex" if self.mode == "rgb" else "rgb"
        for f in self.fields:
            f.setVisible(self.mode == "rgb")
        self.hex_field.setVisible(self.mode == "hex")
        self.mode_button.setText("Hex" if self.mode == "hex" else "RGB")


class GradeSlider(QtWidgets.QWidget):
    """Temperature or Tint: title, Unreal's name in italics, a slider and a typed value.
    Double-click the slider to reset."""
    changed = QtCore.Signal(float)
    gestureStarted = QtCore.Signal()
    gestureFinished = QtCore.Signal()

    def __init__(self, control: Control, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PlainRow")
        self.control = control
        self.neutral = NEUTRAL_TEMP if control.part == "temp" else NEUTRAL_TINT
        self.per_step = 1.0 if control.part == "temp" else 0.001
        self.decimals = 0 if control.part == "temp" else 3
        self._setting = False
        self._active = False
        self.title, self.subtitle = _title_labels(control)
        self.slider = QtWidgets.QSlider(Qt.Horizontal)
        self.slider.setRange(int(round(control.lo / self.per_step)), int(round(control.hi / self.per_step)))
        self.slider.setSingleStep(int(round(control.nudge / self.per_step)))
        self.slider.setPageStep(int(round(control.nudge / self.per_step)) * 5)
        self.slider.setAccessibleName(control.title)
        self.slider.setStyleSheet(NEUTRAL_SLIDER)
        self.slider.setMinimumWidth(160)
        self.slider.setToolTip("Double-click to reset")
        self.slider.installEventFilter(self)
        self.field = QtWidgets.QLineEdit()
        self.field.setFixedWidth(72)
        self.field.setAlignment(Qt.AlignRight)
        self.field.setAccessibleName(f"{control.title} value")
        self.field.editingFinished.connect(self._typed)
        unit = QtWidgets.QLabel("K" if control.part == "temp" else "")
        unit.setObjectName("Muted")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.slider, 1)
        row.addWidget(self.field)
        row.addWidget(unit)
        lay.addLayout(row)
        lay.addStretch(1)  # keep the title, Unreal name and slider together

        self.slider.sliderPressed.connect(self._pressed)
        self.slider.sliderReleased.connect(self._released)
        self.slider.valueChanged.connect(self._moved)
        self.set_value(self.neutral)

    def value(self) -> float:
        return _clamp(self.slider.value() * self.per_step, self.control.lo, self.control.hi)

    def set_value(self, value: float) -> None:
        self._setting = True
        try:
            self.slider.setValue(int(round(float(value) / self.per_step)))
            self.field.setText(f"{float(value):.{self.decimals}f}")
        finally:
            self._setting = False

    def reset(self) -> None:
        self._one(self.neutral)

    def _one(self, value: float) -> None:
        value = _clamp(value, self.control.lo, self.control.hi)
        self.set_value(value)
        self.gestureStarted.emit()
        self.changed.emit(value)
        self.gestureFinished.emit()

    def _pressed(self) -> None:
        self._active = True
        self.gestureStarted.emit()

    def _released(self) -> None:
        if self._active:
            self._active = False
            self.gestureFinished.emit()

    def _moved(self, _step: int) -> None:
        if self._setting:
            return
        v = self.value()
        if self._active:
            self.field.setText(f"{v:.{self.decimals}f}")
            self.changed.emit(v)
        else:
            self._one(v)

    def _typed(self) -> None:
        typed = _parse(self.field.text())
        if typed is None or abs(_clamp(typed, self.control.lo, self.control.hi) - self.value()) < 1e-9:
            self.set_value(self.value())
            return
        self._one(typed)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.slider and event.type() == QtCore.QEvent.MouseButtonDblClick:
            self.reset()
            return True
        return super().eventFilter(obj, event)

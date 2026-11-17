"""Colour grading controls in the style of a colourist's panel (Assimilate Live FX, Resolve).

``ColorWheel``: a hue wheel with a puck. Dragging the puck tints the image (the r, g, b parts of
Unreal's Vector4); scrolling (with the wheel focused) changes brightness (the 4th part). The puck moves
with the drag rather than jumping to the click, Shift makes it ten times finer, a double-click or
Delete resets the wheel and the arrow keys nudge it. ``WheelControl`` adds the colourist title with
Unreal's name in italics, a brightness slider and typed values (RGB numbers or a hex tint colour).
``GradeSlider`` is the Temperature / Tint control.

**Gestures.** Each control reports a change as ``gestureStarted``, ``changed`` (often), then
``gestureFinished``, never nested and never a change outside them. A drag is one gesture; so is a burst
of scroll notches, held arrow keys or groove clicks (it ends when they pause for ``IDLE_MS``); a typed
value, a double-click or a reset is one on its own. A click with no movement is nothing. Hiding the
control ends an open gesture.

**Showing Unreal's values.** ``set_value`` never emits, is ignored while a gesture is open (Unreal's
value lags the hand; the page re-reads after the gesture), never overwrites a field the operator is
typing in, and keeps values outside the control's range as they are until the operator moves them
(a change never pushes a value further out of range, nor pulls in the parts it did not touch).

The wheel is the only colour on the page and it is painted at low strength on purpose: the operator
judges colour on the LED wall, not on the screen.
"""
from __future__ import annotations

import math
from typing import Callable, List, Set, Tuple

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
IDLE_MS = 400         # scrolls, held keys and groove clicks are one gesture until they pause this long

# Grey sliders: the wheels are the only colour on the page.
NEUTRAL_SLIDER = (
    "QSlider::groove:horizontal { height: 4px; background: %(border)s; border-radius: 2px; }"
    "QSlider::sub-page:horizontal { background: %(muted)s; border-radius: 2px; }"
    "QSlider::handle:horizontal { background: %(text)s; width: 14px; margin: -6px 0; border-radius: 7px; }"
    "QSlider::handle:horizontal:focus { background: %(accent)s; }"
) % COLORS


def channel_direction(channel: str) -> Tuple[float, float]:
    """The screen direction (x right, y down) that raises ``channel``."""
    a = _ANGLES[channel]
    return math.cos(a), -math.sin(a)


def _neutral(control: Control) -> float:
    return float(NEUTRAL.get(control.part, (1.0,))[0])


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _keep_in(new: float, old: float, lo: float, hi: float) -> float:
    """Clamp a moved value without pushing it further out of range, or pulling in an untouched one."""
    return _clamp(new, min(lo, old), max(hi, old))


def _fmt(v: float, decimals: int) -> str:
    return f"{round(v, decimals) + 0.0:.{decimals}f}"


def _parse(text: str):
    try:
        v = float(text.strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


class _Gesture(QtCore.QObject):
    """Keeps a control's gestureStarted / gestureFinished paired. A gesture is open while anything
    holds it: a drag, a slider drag, or an idle group (scrolls, keys, groove clicks)."""

    def __init__(self, started: Callable[[], None], finished: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self._started, self._finished = started, finished
        self.held: Set[str] = set()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.release("idle"))

    @property
    def open(self) -> bool:
        return bool(self.held)

    def hold(self, reason: str) -> None:
        if not self.held:
            self._started()
        self.held.add(reason)

    def release(self, reason: str) -> None:
        if reason in self.held:
            self.held.discard(reason)
            if not self.held:
                self._finished()

    def idle(self) -> None:
        """Part of a burst: open (or keep open) until the input pauses."""
        self.hold("idle")
        self._timer.start(IDLE_MS)

    def finish(self) -> None:
        """End whatever is open (before a one-off change, or when the control is hidden)."""
        self._timer.stop()
        if self.held:
            self.held.clear()
            self._finished()

    def one(self, change: Callable[[], None]) -> None:
        """A single change (typed value, reset): its own gesture."""
        self.finish()
        self.hold("one")
        change()
        self.release("one")


class _Slider(QtWidgets.QSlider):
    """A slider that leaves scrolling to the page unless it has the keyboard focus, so scrolling the
    page past it never changes the grade."""

    def wheelEvent(self, event) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class ColorWheel(QtWidgets.QWidget):
    changed = QtCore.Signal(tuple)
    gestureStarted = QtCore.Signal()
    gestureFinished = QtCore.Signal()

    def __init__(self, control: Control, parent=None) -> None:
        super().__init__(parent)
        self.control = control
        self.neutral = _neutral(control)
        self.scale = (control.hi - control.lo) / 4.0  # how far the rim moves a channel from neutral
        self._px, self._py = 0.0, 0.0                   # the puck, in the unit disc (y up); may lie past it
        self._common = 0.0                              # r, g, b raised together (kept, not shown)
        self._y = self.neutral
        self._dragging = False
        self._last = None
        self._ring = None
        self.gesture = _Gesture(self.gestureStarted.emit, self.gestureFinished.emit, self)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName(f"{control.title} colour wheel")
        self.setToolTip("Drag to tint (Shift: finer). Click it, then scroll for brightness. "
                        "Double-click or Delete: reset. Arrow keys: nudge.")
        self._describe()

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(170, 170)

    def busy(self) -> bool:
        return self.gesture.open

    # ---- value ----------------------------------------------------------------------------------
    def _vector(self, px: float, py: float, common: float, y: float) -> Vec4:
        rgb = [self.neutral + common + self.scale * (px * math.cos(_ANGLES[c]) + py * math.sin(_ANGLES[c]))
               for c in "rgb"]
        return (rgb[0], rgb[1], rgb[2], y)

    def value(self) -> Vec4:
        return self._vector(self._px, self._py, self._common, self._y)

    def _load(self, value) -> None:
        r, g, b, y = (float(v) for v in tuple(value)[:4])
        devs = {"r": r - self.neutral, "g": g - self.neutral, "b": b - self.neutral}
        self._common = sum(devs.values()) / 3.0
        k = (2.0 / 3.0) / self.scale
        self._px = sum((devs[c] - self._common) * math.cos(_ANGLES[c]) for c in "rgb") * k
        self._py = sum((devs[c] - self._common) * math.sin(_ANGLES[c]) for c in "rgb") * k
        self._y = y
        self._describe()
        self.update()

    def set_value(self, value) -> None:
        """Show Unreal's value (ignored while the operator is changing this wheel)."""
        if not self.busy():
            self._load(value)

    def apply(self, wanted) -> None:
        """Change to ``wanted`` as the operator's change: kept in range the gentle way, then emitted.
        The caller holds a gesture."""
        old = self.value()
        lo, hi = self.control.lo, self.control.hi
        self._load(tuple(_keep_in(float(n), o, lo, hi) for n, o in zip(tuple(wanted)[:4], old)))
        self.changed.emit(self.value())

    def _move_puck(self, dx: float, dy: float) -> None:
        """Move the puck; a drag cannot take it further out than the rim (or than it already was)."""
        limit = max(1.0, math.hypot(self._px, self._py))
        px, py = self._px + dx, self._py + dy
        d = math.hypot(px, py)
        if d > limit:
            px, py = px / d * limit, py / d * limit
        self.apply(self._vector(px, py, self._common, self._y))

    def reset(self) -> None:
        """Back to neutral (no tint, neutral brightness) as one change."""
        n = self.neutral
        self.gesture.one(lambda: self.apply((n, n, n, n)))

    def _describe(self) -> None:
        r, g, b, y = self.value()
        self.setAccessibleDescription(f"red {r:.3f}, green {g:.3f}, blue {b:.3f}, brightness {y:.3f}")

    # ---- geometry ---------------------------------------------------------------------------------
    def _radius(self) -> float:
        return max(10.0, min(self.width(), self.height()) / 2.0 - 8.0)

    def _centre(self) -> QtCore.QPointF:
        return QtCore.QPointF(self.width() / 2.0, self.height() / 2.0)

    # ---- mouse and keys -----------------------------------------------------------------------------
    @staticmethod
    def _pos(event) -> QtCore.QPointF:
        return event.position() if hasattr(event, "position") else QtCore.QPointF(event.pos())

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._dragging:  # a press without its release (the mouse was lost): end that drag first
            self.gesture.release("drag")
        self._dragging = True
        self._last = self._pos(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging or self._last is None:
            return
        pos = self._pos(event)
        if pos == self._last:
            return
        self.gesture.hold("drag")  # the gesture starts with the first real movement
        factor = FINE if event.modifiers() & Qt.ShiftModifier else 1.0
        r = self._radius()
        self._move_puck((pos.x() - self._last.x()) / r * factor, -(pos.y() - self._last.y()) / r * factor)
        self._last = pos

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            self._last = None
            self.gesture.release("drag")

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self.reset()

    def wheelEvent(self, event) -> None:
        if not self.hasFocus():  # scrolling the page past the wheel must not grade the wall
            event.ignore()
            return
        delta = event.angleDelta()
        notches = (delta.y() or delta.x()) / 120.0  # Shift+scroll arrives sideways on some systems
        if not notches:
            event.ignore()
            return
        step = SCROLL_STEP * (FINE if event.modifiers() & Qt.ShiftModifier else 1.0)
        self.gesture.idle()
        r, g, b, y = self.value()
        self.apply((r, g, b, y + notches * step))
        event.accept()

    def keyPressEvent(self, event) -> None:
        moves = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, 1), Qt.Key_Down: (0, -1)}
        if event.key() in moves:
            step = KEY_STEP * (FINE if event.modifiers() & Qt.ShiftModifier else 1.0)
            dx, dy = moves[event.key()]
            self.gesture.idle()
            self._move_puck(dx * step, dy * step)
        elif event.key() == Qt.Key_Delete:
            self.reset()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        self._dragging = False
        self._last = None
        self.gesture.finish()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QtCore.QEvent.EnabledChange and not self.isEnabled():
            self._dragging = False  # greyed mid-drag: its release will never arrive
            self._last = None
            self.gesture.finish()
        super().changeEvent(event)

    # ---- painting -----------------------------------------------------------------------------------
    def _ring_image(self, size: int) -> QtGui.QImage:
        """The hue disc at low strength, fading to the panel grey at the centre (cached per size)."""
        ratio = self.devicePixelRatioF()
        if self._ring is not None and self._ring[0] == (size, ratio):
            return self._ring[1]
        px = max(1, int(size * ratio))
        img = QtGui.QImage(px, px, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(0.5, 0.5, px - 1.0, px - 1.0)
        hues = QtGui.QConicalGradient(rect.center(), 0.0)
        for step in range(13):
            a = math.radians(step * 30.0)
            rgb = [0.5 + 0.5 * math.cos(a - _ANGLES[c]) for c in "rgb"]
            col = [int(255 * (0.42 + 0.32 * (v - 0.5)) * 0.85) for v in rgb]  # mostly grey, a little hue
            hues.setColorAt(step / 12.0, QtGui.QColor(*col))
        p.setPen(Qt.NoPen)
        p.setBrush(hues)
        p.drawEllipse(rect)
        fade = QtGui.QRadialGradient(rect.center(), px / 2.0)
        centre = QtGui.QColor(COLORS["panel2"])
        fade.setColorAt(0.0, centre)
        edge = QtGui.QColor(centre)
        edge.setAlpha(0)
        fade.setColorAt(1.0, edge)
        p.setBrush(fade)
        p.drawEllipse(rect)
        p.end()
        img.setDevicePixelRatio(ratio)
        self._ring = ((size, ratio), img)
        return img

    def paintEvent(self, _event) -> None:
        r = self._radius()
        c = self._centre()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.drawImage(QtCore.QPointF(c.x() - r, c.y() - r), self._ring_image(int(r * 2)))
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
        # The puck (on the rim, drawn heavier, when the value lies past it), with a line from neutral
        # so a small tint is still easy to read.
        shown = max(1.0, math.hypot(self._px, self._py))
        puck = QtCore.QPointF(c.x() + self._px / shown * r, c.y() - self._py / shown * r)
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["text"]), 1))
        if math.hypot(self._px, self._py) > 0.01:
            p.drawLine(c, puck)
        p.setBrush(QtGui.QColor(tint_hex(self.value()[:3], self.neutral)))
        p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 3))
        p.drawEllipse(puck, 7, 7)
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["text"]), 2 if shown <= 1.0 else 3))
        p.drawEllipse(puck, 7, 7)
        p.end()


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
        self.gesture = _Gesture(self.gestureStarted.emit, self.gestureFinished.emit, self)
        self.title, self.subtitle = _title_labels(control)
        self.wheel = ColorWheel(control)
        self.brightness = _Slider(Qt.Horizontal)
        self.brightness.setRange(0, self.SLIDER_STEPS)
        self.brightness.setStyleSheet(NEUTRAL_SLIDER)
        self.brightness.setAccessibleName(f"{control.title} brightness")
        self.brightness.setToolTip("Brightness")
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
        self._label_mode()

        self.wheel.changed.connect(self._wheel_changed)
        self.wheel.gestureStarted.connect(lambda: self.gesture.hold("wheel"))
        self.wheel.gestureFinished.connect(lambda: self.gesture.release("wheel"))
        self.brightness.sliderPressed.connect(lambda: self.gesture.hold("slider"))
        self.brightness.sliderReleased.connect(lambda: self.gesture.release("slider"))
        self.brightness.valueChanged.connect(self._slider_moved)
        self.set_value(NEUTRAL.get(control.part, (1.0, 1.0, 1.0, 1.0)))

    def busy(self) -> bool:
        return self.gesture.open or self.wheel.busy()

    # ---- value ----------------------------------------------------------------------------------
    def value(self) -> Vec4:
        return self.wheel.value()

    def set_value(self, value) -> None:
        """Show Unreal's value (ignored while the operator is changing this control)."""
        if self.busy():
            return
        self.wheel.set_value(value)
        self._show(self.wheel.value())

    def _show(self, v: Vec4) -> None:
        self._setting, was = True, self._setting
        try:
            if "slider" not in self.gesture.held:
                lo, hi = self.control.lo, self.control.hi
                self.brightness.setValue(int(round(_clamp((v[3] - lo) / (hi - lo), 0.0, 1.0) * self.SLIDER_STEPS)))
            for f, x in zip(self.fields, v):
                if not f.isModified():  # never wipe what the operator is typing
                    f.setText(_fmt(x, 3))
            if not self.hex_field.isModified():
                self.hex_field.setText(tint_hex(v[:3], self.wheel.neutral))
        finally:
            self._setting = was

    def _wheel_changed(self, v) -> None:
        self._show(v)
        self.changed.emit(v)

    def _one(self, wanted) -> None:
        """A single change (typed value): its own gesture."""
        self.gesture.one(lambda: self.wheel.apply(wanted))

    # ---- brightness slider ---------------------------------------------------------------------------
    def _slider_moved(self, step: int) -> None:
        if self._setting:
            return
        if "slider" not in self.gesture.held:
            self.gesture.idle()  # a groove click, page step or key: grouped until they pause
        lo, hi = self.control.lo, self.control.hi
        r, g, b, _y = self.wheel.value()
        self.wheel.apply((r, g, b, lo + (hi - lo) * step / self.SLIDER_STEPS))

    # ---- typed values -------------------------------------------------------------------------------
    def _typed(self, index: int) -> None:
        field = self.fields[index]
        if not field.isModified():  # Enter or tabbing away with nothing typed
            return
        field.setModified(False)
        current = self.wheel.value()
        typed = _parse(field.text())
        if typed is None:
            self._show(current)
            return
        wanted = list(current)
        wanted[index] = _clamp(typed, self.control.lo, self.control.hi)
        if abs(wanted[index] - current[index]) < 1e-9:
            self._show(current)
            return
        self._one(tuple(wanted))

    def _typed_hex(self) -> None:
        if not self.hex_field.isModified():
            return
        self.hex_field.setModified(False)
        current = self.wheel.value()
        n = self.wheel.neutral
        try:
            tinted = hex_to_tint(self.hex_field.text(), current[3], n)
        except ValueError:
            self._show(current)
            return
        # Only the tint moves: the typed colour's balance, around the wheel's current overall level.
        devs = [c - n for c in tinted[:3]]
        mean = sum(devs) / 3.0
        level = sum(current[:3]) / 3.0
        lo, hi = self.control.lo, self.control.hi
        wanted = tuple(_clamp(level + d - mean, lo, hi) for d in devs) + (current[3],)
        if all(abs(a - b) < 1e-9 for a, b in zip(wanted, current)):
            self._show(current)
            return
        self._one(wanted)

    def _cycle(self) -> None:
        self.mode = "hex" if self.mode == "rgb" else "rgb"
        for f in self.fields:
            f.setVisible(self.mode == "rgb")
        self.hex_field.setVisible(self.mode == "hex")
        self._label_mode()

    def _label_mode(self) -> None:
        hex_mode = self.mode == "hex"
        self.mode_button.setText("Hex" if hex_mode else "RGB")
        self.mode_button.setToolTip("Showing a hex tint colour: click to show RGB numbers" if hex_mode
                                    else "Showing RGB numbers: click to show a hex tint colour")
        self.mode_button.setAccessibleName(
            f"{self.control.title}: show {'RGB numbers' if hex_mode else 'hex tint colour'}")

    def hideEvent(self, event) -> None:
        self.gesture.finish()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QtCore.QEvent.EnabledChange and not self.isEnabled():
            self.gesture.finish()
        super().changeEvent(event)


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
        self._value = self.neutral
        self._setting = False
        self.gesture = _Gesture(self.gestureStarted.emit, self.gestureFinished.emit, self)
        self.title, self.subtitle = _title_labels(control)
        self.slider = _Slider(Qt.Horizontal)
        self.slider.setRange(int(round(control.lo / self.per_step)), int(round(control.hi / self.per_step)))
        self.slider.setSingleStep(int(round(control.nudge / self.per_step)))
        self.slider.setPageStep(int(round(control.nudge / self.per_step)) * 5)
        self.slider.setStyleSheet(NEUTRAL_SLIDER)
        self.slider.setMinimumWidth(160)
        self.slider.setAccessibleName(control.title)
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

        self.slider.sliderPressed.connect(lambda: self.gesture.hold("slider"))
        self.slider.sliderReleased.connect(lambda: self.gesture.release("slider"))
        self.slider.valueChanged.connect(self._moved)
        self._show()

    def busy(self) -> bool:
        return self.gesture.open

    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        """Show Unreal's value (ignored while the operator is changing it)."""
        if not self.busy():
            self._value = float(value)
            self._show()

    def _show(self) -> None:
        self._setting = True
        try:
            if "slider" not in self.gesture.held:
                self.slider.setValue(int(round(self._value / self.per_step)))
            if not self.field.isModified():
                self.field.setText(_fmt(self._value, self.decimals))
        finally:
            self._setting = False

    def _set(self, value: float) -> None:
        self._value = _keep_in(float(value), self._value, self.control.lo, self.control.hi)
        self._show()
        self.changed.emit(self._value)

    def reset(self) -> None:
        self.gesture.one(lambda: self._set(self.neutral))

    def _moved(self, step: int) -> None:
        if self._setting:
            return
        if "slider" not in self.gesture.held:
            self.gesture.idle()  # a groove click, page step or key: grouped until they pause
        self._set(step * self.per_step)

    def _typed(self) -> None:
        if not self.field.isModified():  # Enter or tabbing away with nothing typed
            return
        self.field.setModified(False)
        typed = _parse(self.field.text())
        if typed is None or abs(_clamp(typed, self.control.lo, self.control.hi) - self._value) < 1e-9:
            self._show()
            return
        self.gesture.one(lambda: self._set(_clamp(typed, self.control.lo, self.control.hi)))

    def eventFilter(self, obj, event) -> bool:
        if obj is self.slider and event.type() == QtCore.QEvent.MouseButtonDblClick:
            self.reset()
            return True
        return super().eventFilter(obj, event)

    def hideEvent(self, event) -> None:
        self.gesture.finish()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QtCore.QEvent.EnabledChange and not self.isEnabled():
            self.gesture.finish()
        super().changeEvent(event)

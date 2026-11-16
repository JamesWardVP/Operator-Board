"""Button pictures in the app: the icon and emoji libraries, the picker, the editor row and drawing.

A picture is always turned into a small image when it is chosen (``store``) and kept in the button
(see ``surfaces/pictures.py``), so the Stream Deck and the web panel need neither this library nor
the Windows emoji font to show it.

- **Icons** are Tabler Icons (MIT), kept in ``icon_library.json`` (``packaging/make_icon_library.py``).
  They are white line drawings, made to be recoloured.
- **Emojis** are drawn with the PC's own emoji font, so none are bundled.
- **Custom** pictures are the operator's own files.
"""
from __future__ import annotations

import json
import os
import unicodedata
from typing import Dict, List, Optional, Tuple

from operator_board.qt import QtCore, QtGui, QtWidgets, Qt, exec_
from operator_board.surfaces import pictures
from operator_board.ui.widgets import ColorField, hbox, muted

try:
    from PySide6 import QtSvg
except ImportError:  # pragma: no cover - PySide2
    from PySide2 import QtSvg

LIBRARY_PATH = os.path.join(os.path.dirname(__file__), "icon_library.json")
EMOJI_FONT = "Segoe UI Emoji"
FILE_FILTER = "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg);;All files (*)"
SUGGESTED = "Suggested for stage work"
ALL = "All"
SHOWN_AT_ONCE = 400  # icons drawn in the picker at a time; searching narrows them down

# Icons an operator is likely to want first (names from the library; missing ones are skipped).
SUGGESTED_ICONS = (
    "player-record", "player-play", "player-pause", "player-stop", "player-skip-back", "player-skip-forward",
    "player-track-prev", "player-track-next", "camera", "camera-off", "video", "video-off", "movie", "clapperboard",
    "slideshow", "photo", "aperture", "focus", "focus-2", "focus-centered", "zoom-in", "zoom-out", "snowflake",
    "snowflake-off", "eye", "eye-off", "device-tv", "device-tv-off", "device-desktop", "devices", "screen-share",
    "screen-share-off", "wall", "grid-dots", "grid-pattern", "layout-grid", "border-all", "color-swatch", "palette",
    "color-filter", "brightness", "brightness-up", "brightness-down", "contrast", "sun", "sun-off", "moon", "bulb",
    "bulb-off", "lamp", "flare", "sparkles", "bolt", "flame", "cloud", "wind", "droplet", "power", "plug",
    "plug-connected", "refresh", "reload", "rotate", "restore", "lock", "lock-open", "shield", "alert-triangle",
    "alert-circle", "info-circle", "circle-check", "circle-x", "check", "x", "plus", "minus", "ban", "flag", "star",
    "heart", "bookmark", "pin", "map-pin", "target", "crosshair", "world", "map", "layers-intersect", "stack",
    "stack-2", "box", "cube", "cube-3d-sphere", "users", "user", "server", "server-2", "network", "wifi",
    "broadcast", "antenna", "microphone", "microphone-off", "volume", "volume-off", "headphones", "music",
    "clock", "hourglass", "stopwatch", "calendar", "device-floppy", "download", "upload", "folder", "file",
    "trash", "edit", "settings", "adjustments", "tool", "home", "arrow-up", "arrow-down", "arrow-left",
    "arrow-right", "arrows-move", "arrows-maximize", "arrows-minimize", "chevron-up", "chevron-down",
    "chevron-left", "chevron-right", "number-1", "number-2", "number-3", "number-4", "number-5", "number-6",
    "number-7", "number-8", "number-9", "number-0",
)

# Emoji groups by where the characters sit in Unicode.
EMOJI_GROUPS: Tuple[Tuple[str, Tuple[Tuple[int, int], ...]], ...] = (
    ("Faces and people", ((0x1F600, 0x1F64F), (0x1F900, 0x1F9FF), (0x1F440, 0x1F4AF))),
    ("Nature, food and places", ((0x1F300, 0x1F43F),)),
    ("Objects and symbols", ((0x1F4B0, 0x1F5FF), (0x1FA70, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
                             (0x1F100, 0x1F2FF))),
    ("Travel and signs", ((0x1F680, 0x1F6FF),)),
)

_library: Optional[Dict[str, List[str]]] = None
_emojis: Optional[List[Tuple[str, str, str]]] = None
_pixmaps: Dict[tuple, QtGui.QPixmap] = {}


# ---- icons --------------------------------------------------------------------------------
def icon_library() -> Dict[str, List[str]]:
    """name -> [category, search words, drawing]; empty when the library file is missing."""
    global _library
    if _library is None:
        try:
            with open(LIBRARY_PATH, "r", encoding="utf-8") as fh:
                _library = json.load(fh).get("icons", {})
        except (OSError, ValueError):
            _library = {}
    return _library


def icon_categories() -> List[str]:
    return [SUGGESTED, ALL] + sorted({entry[0] for entry in icon_library().values()})


def find_icons(text: str = "", category: str = SUGGESTED) -> List[str]:
    """Icon names in a category, or (with ``text``) every icon whose name or search words hold all
    the typed words."""
    library = icon_library()
    words = text.lower().replace("-", " ").split()
    if words:
        return [name for name, (_cat, tags, _body) in library.items()
                if all(w in name.replace("-", " ") or w in tags for w in words)]
    if category == SUGGESTED:
        return [name for name in SUGGESTED_ICONS if name in library]
    return [name for name, entry in library.items() if category == ALL or entry[0] == category]


def icon_image(name: str, side: int = pictures.MAX_SIDE, color: str = "#ffffff") -> QtGui.QImage:
    """A library icon as a line drawing on a clear background; a null image for an unknown name."""
    entry = icon_library().get(name)
    if not entry:
        return QtGui.QImage()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{}" stroke-width="1.75" '
           'stroke-linecap="round" stroke-linejoin="round">{}</svg>').format(color, entry[2])
    return _render_svg(QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8"))), side)


def _render_svg(renderer, side: int) -> QtGui.QImage:
    if not renderer.isValid():
        return QtGui.QImage()
    size = renderer.defaultSize()
    size.scale(side, side, Qt.KeepAspectRatio)
    image = QtGui.QImage(max(1, size.width()), max(1, size.height()), QtGui.QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


# ---- emojis -------------------------------------------------------------------------------
def emoji_list() -> List[Tuple[str, str, str]]:
    """(character, name, group) for every emoji this PC's emoji font can draw."""
    global _emojis
    if _emojis is None:
        metrics = QtGui.QFontMetrics(QtGui.QFont(EMOJI_FONT))
        found, seen = [], set()
        for group, ranges in EMOJI_GROUPS:
            for first, last in ranges:
                for code in range(first, last + 1):
                    if code in seen or 0x1F3FB <= code <= 0x1F3FF:  # skin tone modifiers are not pictures
                        continue
                    char = chr(code)
                    name = unicodedata.name(char, "")
                    if not name or unicodedata.category(char) != "So" or not metrics.inFontUcs4(code):
                        continue
                    seen.add(code)
                    found.append((char, name.lower(), group))
        _emojis = found
    return _emojis


def find_emojis(text: str = "", group: str = ALL) -> List[Tuple[str, str, str]]:
    words = text.lower().split()
    return [e for e in emoji_list()
            if (all(w in e[1] for w in words) if words else group in (ALL, e[2]))]


def emoji_image(char: str, side: int = pictures.MAX_SIDE) -> QtGui.QImage:
    """An emoji drawn in colour on a clear background, trimmed to the drawing."""
    image = QtGui.QImage(side, side, QtGui.QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QtGui.QPainter(image)
    font = QtGui.QFont(EMOJI_FONT)
    font.setPixelSize(int(side * 0.72))
    painter.setFont(font)
    painter.setPen(QtGui.QColor("#ffffff"))
    painter.drawText(image.rect(), Qt.AlignCenter, char + "️")  # ask for the colour form
    painter.end()
    return image


# ---- storing ------------------------------------------------------------------------------
def load_file(path: str) -> QtGui.QImage:
    """A picture file as an image (a null image when it cannot be read)."""
    if path.lower().endswith(".svg"):
        return _render_svg(QtSvg.QSvgRenderer(path), pictures.MAX_SIDE)
    reader = QtGui.QImageReader(path)
    reader.setAutoTransform(True)  # phone photos carry their rotation separately
    return reader.read()


def store(image: QtGui.QImage) -> str:
    """The image as a button keeps it: scaled to fit ``MAX_SIDE``, PNG when it has see-through
    parts, otherwise the much smaller JPEG. Empty for a null image."""
    if image.isNull():
        return ""
    if max(image.width(), image.height()) > pictures.MAX_SIDE:
        image = image.scaled(pictures.MAX_SIDE, pictures.MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.WriteOnly)
    if image.hasAlphaChannel():
        image.save(buffer, "PNG")
    else:
        image.save(buffer, "JPG", 88)
    buffer.close()
    return pictures.encode(bytes(data))


def image_from(image: str) -> QtGui.QImage:
    return QtGui.QImage.fromData(pictures.decode(image)) if image else QtGui.QImage()


def is_single_colour(image: QtGui.QImage) -> bool:
    """True for a logo or icon in one colour on a clear background: the kind worth recolouring."""
    if image.isNull() or not image.hasAlphaChannel():
        return False
    small = image.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.FastTransformation).convertToFormat(QtGui.QImage.Format_ARGB32)
    first = None
    for y in range(small.height()):
        for x in range(small.width()):
            pixel = small.pixelColor(x, y)
            if pixel.alpha() < 128:
                continue
            rgb = (pixel.red(), pixel.green(), pixel.blue())
            if first is None:
                first = rgb
            elif max(abs(a - b) for a, b in zip(rgb, first)) > 40:
                return False
    return first is not None


# ---- drawing ------------------------------------------------------------------------------
def pixmap(image: str, color: str, width: int, height: int, cover: bool = False, radius: int = 0) -> QtGui.QPixmap:
    """A stored picture drawn into ``width`` x ``height``: all of it showing, or (``cover``)
    filling the area with rounded corners; repainted in ``color`` when one is given."""
    key = (pictures.picture_id(image), color, width, height, cover, radius)
    if key not in _pixmaps:
        if len(_pixmaps) > 600:
            _pixmaps.clear()
        source = image_from(image)
        target = QtGui.QPixmap(max(1, width), max(1, height))
        target.fill(Qt.transparent)
        if not source.isNull():
            if color and QtGui.QColor(color).isValid():
                source = source.convertToFormat(QtGui.QImage.Format_ARGB32_Premultiplied)
                tint = QtGui.QPainter(source)
                tint.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
                tint.fillRect(source.rect(), QtGui.QColor(color))
                tint.end()
            x, y, w, h = pictures.place(source.width(), source.height(), (0, 0, width, height), cover)
            painter = QtGui.QPainter(target)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            if radius:
                path = QtGui.QPainterPath()
                path.addRoundedRect(QtCore.QRectF(0, 0, width, height), radius, radius)
                painter.setClipPath(path)
            painter.drawImage(QtCore.QRect(x, y, w, h), source)
            painter.end()
        _pixmaps[key] = target
    return _pixmaps[key]


def show_on(button, picture_label, text_label, binding, pad: int, inset: int = 2, radius: int = 8):
    """Lay a button's picture and label out on an on-screen button (``picture_label`` under
    ``text_label``, both children of ``button``). Returns the label's (width, height), or None when
    the label is hidden."""
    picture_box, label_box = pictures.areas(button.width(), button.height(), pad, binding)
    if picture_box:
        fill = pictures.layout_of(binding) == pictures.LAYOUT_FILL
        if fill:  # inside the button's border
            picture_box = (inset, inset, max(1, button.width() - 2 * inset), max(1, button.height() - 2 * inset))
        picture_label.setGeometry(*picture_box)
        picture_label.setPixmap(pixmap(binding.image, binding.image_color, picture_box[2], picture_box[3], fill,
                                       radius if fill else 0))
        picture_label.show()
    else:
        picture_label.hide()
    if label_box:
        text_label.setGeometry(*label_box)
        text_label.show()
        text_label.raise_()
        return label_box[2], label_box[3]
    text_label.hide()
    return None


def new_picture_label(button) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(button)
    label.setAttribute(Qt.WA_TransparentForMouseEvents)
    label.setStyleSheet("background: transparent; border: none;")
    label.hide()
    return label


# ---- the picker ---------------------------------------------------------------------------
class PicturePicker(QtWidgets.QDialog):
    """Choose an icon or an emoji from the library: search by name, or browse a group."""

    def __init__(self, parent, kind: str, current: str = "") -> None:
        super().__init__(parent)
        self.kind = kind
        self.choice = ""  # the chosen icon name or emoji character
        icons = kind == pictures.SOURCE_ICON
        self.setWindowTitle("Choose an icon" if icons else "Choose an emoji")
        self.resize(640, 520)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search, e.g. camera, light, arrow, record" if icons
                                       else "Search, e.g. fire, star, clapper, warning")
        self.search.setClearButtonEnabled(True)
        self.group = QtWidgets.QComboBox()
        self.group.addItems(icon_categories() if icons else [ALL] + [g for g, _ in EMOJI_GROUPS])
        self.list = QtWidgets.QListWidget()
        self.list.setViewMode(QtWidgets.QListView.IconMode)
        self.list.setResizeMode(QtWidgets.QListView.Adjust)
        self.list.setMovement(QtWidgets.QListView.Static)
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(QtCore.QSize(40, 40))
        self.list.setGridSize(QtCore.QSize(60, 60))
        self.list.setWordWrap(False)
        self.emoji_font = QtGui.QFont(EMOJI_FONT)
        self.emoji_font.setPixelSize(32)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        self.count = muted("")
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(hbox(self.search, self.group))
        lay.addWidget(self.list, 1)
        lay.addWidget(self.count)
        if icons:
            lay.addWidget(muted("Icons are white line drawings. Give one any colour with Recolour."))
        lay.addWidget(buttons)
        self.search.textChanged.connect(self.fill)
        self.group.currentIndexChanged.connect(self.fill)
        self.fill()
        self.select(current)
        self.search.setFocus()

    def fill(self, *_args) -> None:
        text, group = self.search.text().strip(), self.group.currentText()
        self.list.clear()
        if self.kind == pictures.SOURCE_ICON:
            names = find_icons(text, group)
            for name in names[:SHOWN_AT_ONCE]:
                item = QtWidgets.QListWidgetItem(QtGui.QIcon(QtGui.QPixmap.fromImage(icon_image(name, 80))), "")
                item.setData(Qt.UserRole, name)
                item.setToolTip(name.replace("-", " "))
                self.list.addItem(item)
            total = len(names)
        else:
            found = find_emojis(text, group)
            for char, name, _group in found:
                item = QtWidgets.QListWidgetItem(char)
                item.setData(Qt.UserRole, char)
                item.setToolTip(name)
                item.setTextAlignment(Qt.AlignCenter)
                item.setFont(self.emoji_font)  # per item: the app's style sheet sets the list's own font
                self.list.addItem(item)
            total = len(found)
        shown = self.list.count()
        if not total:
            self.count.setText("Nothing matches. Try another word.")
        elif shown < total:
            self.count.setText(f"Showing the first {shown} of {total}. Search to narrow them down.")
        else:
            self.count.setText(f"{total} to choose from.")

    def select(self, value: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.UserRole) == value:
                self.list.setCurrentRow(row)
                return

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.choice = item.data(Qt.UserRole)
        self.accept()


# ---- the editor row -----------------------------------------------------------------------
class PictureField(QtWidgets.QWidget):
    """The picture part of a button editor: where it comes from, the picture, its colour and place."""
    changed = QtCore.Signal()

    KINDS = (("", "No picture"), (pictures.SOURCE_ICON, "Icon (built-in library)"),
             (pictures.SOURCE_EMOJI, "Emoji"), (pictures.SOURCE_FILE, "Custom picture (your own file)"))
    BUTTON_TEXT = {pictures.SOURCE_ICON: "Choose icon…", pictures.SOURCE_EMOJI: "Choose emoji…",
                   pictures.SOURCE_FILE: "Upload…"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.image = ""
        self.source = ""
        self.can_recolour = False
        self.kind = QtWidgets.QComboBox()
        for value, label in self.KINDS:
            self.kind.addItem(label, value)
        self.preview = QtWidgets.QLabel()
        self.preview.setFixedSize(52, 52)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background: #2b2f36; border: 1px solid #30353d; border-radius: 8px;")
        self.choose = QtWidgets.QPushButton()
        self.choose.clicked.connect(lambda: self.pick())
        self.name = muted("")
        self.color = ColorField("Picture colour")
        self.color.setToolTip("Repaints a single-colour picture (an icon, a logo) in this colour. "
                              "Leave empty to keep the picture's own colours.")
        self.original = QtWidgets.QPushButton("Own colours")
        self.original.setToolTip("Show the picture in its own colours again")
        self.original.clicked.connect(lambda: self.color.setText(""))
        self.layout_box = QtWidgets.QComboBox()
        for value, label in pictures.LAYOUTS.items():
            self.layout_box.addItem(label, value)
        self.recolour_row = QtWidgets.QWidget()
        self.recolour_row.setObjectName("PlainRow")
        recolour = QtWidgets.QHBoxLayout(self.recolour_row)
        recolour.setContentsMargins(0, 0, 0, 0)
        recolour.addWidget(QtWidgets.QLabel("Recolour"))
        recolour.addWidget(self.color)
        recolour.addWidget(self.original)
        recolour.addStretch(1)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.kind)
        self.detail = QtWidgets.QWidget()
        self.detail.setObjectName("PlainRow")
        detail = QtWidgets.QVBoxLayout(self.detail)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(6)
        detail.addLayout(hbox(self.preview, self.choose, self.name, None))
        detail.addWidget(self.recolour_row)
        detail.addWidget(self.layout_box)
        lay.addWidget(self.detail)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.color.edit.textChanged.connect(lambda _text: self._show())
        self.layout_box.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self._show()

    # ---- values ---------------------------------------------------------------------------
    def set_binding(self, binding) -> None:
        self.image, self.source = binding.image, binding.image_source if binding.image else ""
        kind = pictures.source_kind(self.source) or (pictures.SOURCE_FILE if self.image else "")
        for widget, call in ((self.kind, lambda: self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))),
                             (self.layout_box, lambda: self.layout_box.setCurrentIndex(
                                 max(0, self.layout_box.findData(pictures.layout_of(binding)))))):
            widget.blockSignals(True)
            call()
            widget.blockSignals(False)
        self.color.setText(binding.image_color if binding.image else "")
        self._show()

    def values(self) -> Dict[str, str]:
        """The picture fields of a ``ButtonBinding``; raises ValueError with what is wrong."""
        if not self.image or not self.kind.currentData():
            return {"image": "", "image_source": "", "image_color": "", "image_layout": pictures.LAYOUT_ABOVE}
        color = self.color.text() if self.can_recolour else ""
        if color and not QtGui.QColor(color).isValid():
            raise ValueError("Picture colour is not a colour: use #rrggbb or the colour picker")
        return {"image": self.image, "image_source": self.source, "image_color": color,
                "image_layout": self.layout_box.currentData() or pictures.LAYOUT_ABOVE}

    # ---- choosing -------------------------------------------------------------------------
    def _kind_changed(self) -> None:
        if pictures.source_kind(self.source) != self.kind.currentData():
            self.image, self.source = "", ""  # the picture belonged to the other kind
            self.color.setText("")
        self._show()
        self.changed.emit()

    def pick(self, value: str = "") -> bool:
        """Choose the picture for the current kind: ``value`` is an icon name, an emoji or a file
        path; without one the operator is asked."""
        kind = self.kind.currentData()
        if kind == pictures.SOURCE_FILE:
            return self.upload(value)
        if kind not in (pictures.SOURCE_ICON, pictures.SOURCE_EMOJI):
            return False
        if not value:
            dialog = PicturePicker(self, kind, pictures.source_name(self.source))
            exec_(dialog)
            value = dialog.choice
        if not value:
            return False
        image = icon_image(value) if kind == pictures.SOURCE_ICON else emoji_image(value)
        return self._set(store(image), f"{kind}:{value}", "That picture could not be drawn")

    def upload(self, path: str = "") -> bool:
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose a picture", "", FILE_FILTER)
        if not path:
            return False
        return self._set(store(load_file(path)), f"{pictures.SOURCE_FILE}:{os.path.basename(path)}",
                         f"{os.path.basename(path)} is not a picture this PC can read")

    def _set(self, image: str, source: str, problem: str) -> bool:
        if not image:
            self.name.setText(problem)
            return False
        self.image, self.source = image, source
        if pictures.source_kind(source) == pictures.SOURCE_EMOJI:
            self.color.setText("")  # emojis keep their colours
        self._show()
        self.changed.emit()
        return True

    # ---- display --------------------------------------------------------------------------
    def _show(self) -> None:
        kind = self.kind.currentData() or ""
        self.detail.setVisible(bool(kind))
        self.choose.setText(self.BUTTON_TEXT.get(kind, ""))
        has = bool(self.image)
        source = image_from(self.image) if has else QtGui.QImage()
        # Recolouring only makes sense for one-colour pictures: icons, and logos that are one colour.
        can_recolour = has and kind != pictures.SOURCE_EMOJI and (kind == pictures.SOURCE_ICON
                                                                  or is_single_colour(source))
        self.can_recolour = can_recolour
        self.recolour_row.setVisible(can_recolour)
        self.layout_box.setVisible(has)
        if has:
            self.preview.setPixmap(pixmap(self.image, self.color.text() if can_recolour else "", 44, 44))
            self.name.setText(pictures.source_name(self.source).replace("-", " ")
                              if kind != pictures.SOURCE_EMOJI else "")
        else:
            self.preview.setPixmap(QtGui.QPixmap())
            self.name.setText("No picture chosen yet" if kind else "")

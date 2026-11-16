"""Pictures on buttons: how a picture is kept in a button, and where it sits beside the label.

A button's picture is a small image (PNG or JPEG, at most ``MAX_SIDE`` pixels a side) held in the
button itself as base64 text (``ButtonBinding.image``). So it goes wherever the button goes: surface
exports, custom page files, presets and project exports, with no separate files to copy.

``ButtonBinding.image_source`` remembers where it came from (``icon:<name>``, ``emoji:<character>``
or ``file:<file name>``), ``image_color`` recolours a single-colour picture and ``image_layout``
places it. Nothing here needs Qt or Pillow: the Stream Deck, the on-screen keys and the web panel
each draw the picture themselves, all with ``areas`` and ``place`` so it sits the same on each.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Dict, Optional, Tuple

MAX_SIDE = 256  # stored pictures are scaled down to fit this square

LAYOUT_ABOVE = "above"
LAYOUT_ONLY = "only"
LAYOUT_FILL = "fill"
LAYOUTS: Dict[str, str] = {
    LAYOUT_ABOVE: "Picture above the label",
    LAYOUT_ONLY: "Picture only (label hidden)",
    LAYOUT_FILL: "Picture fills the button, label on top",
}

SOURCE_ICON = "icon"
SOURCE_EMOJI = "emoji"
SOURCE_FILE = "file"

PICTURE_SHARE = 0.58  # of the button's height, when the label sits underneath

Box = Tuple[int, int, int, int]  # x, y, width, height


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode(image: str) -> bytes:
    """The picture's file bytes; empty when there is no picture or the text is damaged."""
    if not image:
        return b""
    try:
        return base64.b64decode(image, validate=True)
    except (binascii.Error, ValueError):
        return b""


def mime_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


def picture_id(image: str) -> str:
    """A short name for a picture, the same for the same picture (cache keys, web addresses)."""
    return hashlib.sha1(image.encode("ascii", "ignore")).hexdigest()[:12] if image else ""


def source_kind(source: str) -> str:
    """icon, emoji or file, from an ``image_source``; empty for none."""
    kind = (source or "").split(":", 1)[0]
    return kind if kind in (SOURCE_ICON, SOURCE_EMOJI, SOURCE_FILE) else ""


def source_name(source: str) -> str:
    return (source or "").split(":", 1)[1] if ":" in (source or "") else ""


def layout_of(binding) -> str:
    return binding.image_layout if binding.image_layout in LAYOUTS else LAYOUT_ABOVE


def label_text(binding) -> str:
    """The text a button shows: none when its picture stands alone."""
    if binding.image and layout_of(binding) == LAYOUT_ONLY:
        return ""
    return binding.label or binding.action


def areas(width: int, height: int, pad: int, binding) -> Tuple[Optional[Box], Optional[Box]]:
    """(picture box, label box) on a ``width`` x ``height`` button; None for the one not shown."""
    inner = (pad, pad, max(1, width - 2 * pad), max(1, height - 2 * pad))
    text = label_text(binding)
    if not binding.image:
        return None, inner
    layout = layout_of(binding)
    if layout == LAYOUT_FILL:
        return (0, 0, width, height), (inner if text else None)
    if layout == LAYOUT_ONLY or not text:
        return inner, None
    picture_height = max(1, int(inner[3] * PICTURE_SHARE))
    gap = max(1, pad // 2)
    label_top = pad + picture_height + gap
    return (pad, pad, inner[2], picture_height), (pad, label_top, inner[2], max(1, pad + inner[3] - label_top))


def place(picture_width: int, picture_height: int, box: Box, cover: bool = False) -> Box:
    """Where a picture of that size goes in ``box``, keeping its shape: all of it showing, or
    (``cover``) filling the box with the overflow off the edges."""
    x, y, width, height = box
    if picture_width <= 0 or picture_height <= 0:
        return box
    scale = (max if cover else min)(width / picture_width, height / picture_height)
    w, h = max(1, int(round(picture_width * scale))), max(1, int(round(picture_height * scale)))
    return x + (width - w) // 2, y + (height - h) // 2, w, h

"""Fitting key labels onto a key: word wrap, font size (fixed or as large as fits).

Shared by the Stream Deck renderer (Pillow) and the on-screen deck (Qt), so a label breaks the
same way on both. ``measure(text, size)`` returns a line's (width, height) in pixels.
"""
from __future__ import annotations

from typing import Callable, List, Tuple

from operator_board.surfaces.layout import FONT_WEIGHTS

Measure = Callable[[str, int], Tuple[float, float]]

REFERENCE_KEY = 72  # font sizes are given for a 72-pixel key (Stream Deck MK.2) and scaled to the real key
AUTO_MAX = 22  # largest automatic size on a 72-pixel key
AUTO_MIN = 8
LINE_SPACING = 1.12


def scaled_size(size: int, key_px: float) -> int:
    return max(1, int(round(size * key_px / REFERENCE_KEY)))


def wrap_lines(text: str, width: float, size: int, measure: Measure) -> List[str]:
    """Greedy word wrap of ``text`` (its own line breaks kept); a word wider than the key is
    broken between letters."""
    lines: List[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if measure(candidate, size)[0] <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            while measure(word, size)[0] > width and len(word) > 1:  # break a long word
                cut = len(word) - 1
                while cut > 1 and measure(word[:cut], size)[0] > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def fit_label(text: str, width: float, height: float, measure: Measure, size: int = 0,
              wrap: bool = True, key_px: float = REFERENCE_KEY) -> Tuple[List[str], int]:
    """(lines, pixel size) for ``text`` in a ``width`` x ``height`` area. ``size`` 0 = the largest
    size that fits; otherwise that size (scaled from a 72-pixel key), even if it overflows."""
    text = (text or "").strip("\n")
    if size:
        px = scaled_size(size, key_px)
        return (wrap_lines(text, width, px, measure) if wrap else text.split("\n")), px
    top, bottom = scaled_size(AUTO_MAX, key_px), scaled_size(AUTO_MIN, key_px)
    words = text.split()
    lines: List[str] = text.split("\n")
    # Shrink before breaking a word in the middle; break words only when nothing else fits.
    for whole_words in ((True, False) if wrap else (False,)):
        for px in range(top, bottom - 1, -1):
            if whole_words and any(measure(w, px)[0] > width for w in words):
                continue
            lines = wrap_lines(text, width, px, measure) if wrap else text.split("\n")
            widest = max((measure(line, px)[0] for line in lines), default=0)
            tall = measure("Ag", px)[1] * LINE_SPACING * len(lines)
            if widest <= width and tall <= height:
                return lines, px
    return lines, bottom


def css_weight(name: str) -> int:
    return FONT_WEIGHTS.get(name, 700)

"""Control surface layouts (Stream Deck, MIDI pads, OSC, the phone/tablet web panel and the on-screen deck).

Layouts are plain JSON so they can be saved to an external file and loaded on
another machine.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from operator_board.core.config import atomic_write
from operator_board.core.serde import from_dict, to_dict

DEFAULT_LAYOUT_PATH = os.path.join(os.path.dirname(__file__), "default_layout.json")

# model id -> (label, columns, rows). Every layout also works on the web panel and over OSC.
MODELS: Dict[str, Tuple[str, int, int]] = {
    "streamdeck_mini": ("Stream Deck Mini (6 keys)", 3, 2),
    "streamdeck_15": ("Stream Deck / MK.2 (15 keys)", 5, 3),
    "streamdeck_plus": ("Stream Deck + (8 keys)", 4, 2),
    "streamdeck_neo": ("Stream Deck Neo (8 keys)", 4, 2),
    "streamdeck_xl": ("Stream Deck XL (32 keys)", 8, 4),
    "web_phone": ("Phone (web panel, 3 x 5 buttons)", 3, 5),
    "web_tablet": ("Tablet / iPad (web panel, 6 x 4 buttons)", 6, 4),
    "osc_touchosc": ("TouchOSC / Open Stage Control (OSC, 4 x 4)", 4, 4),
    "midi_pads16": ("MIDI pad controller, 16 pads (notes 36-51, e.g. Akai MPD / MPK)", 4, 4),
    "midi_apc_mini": ("Akai APC mini / mk2 (MIDI, 8 x 8 pads)", 8, 8),
    "midi_launchpad": ("Novation Launchpad, programmer mode (MIDI, 8 x 8 pads)", 8, 8),
    "custom": ("Custom (any size: web panel, OSC or MIDI)", 4, 4),
}

# How each model is driven: streamdeck | web | osc | midi | custom
KINDS: Dict[str, str] = {m: ("streamdeck" if m.startswith("streamdeck") else m.split("_")[0] if m != "custom"
                             else "custom") for m in MODELS}

# MIDI note of key 0 and how notes run across the pads. Keys count from the top-left; these
# controllers number their pads from the bottom-left, row by row.
MIDI_MAPS: Dict[str, Tuple[int, int]] = {  # model -> (note of the bottom-left pad, notes per row)
    "midi_pads16": (36, 4),
    "midi_apc_mini": (0, 8),
    "midi_launchpad": (11, 10),  # 11..18 bottom row, 81..88 top row
}


def midi_note_for_key(model: str, columns: int, rows: int, index: int, base_note: int = 36) -> int:
    """The MIDI note of key ``index`` (top-left = 0). Custom layouts count up from ``base_note``."""
    bottom_left, stride = MIDI_MAPS.get(model, (base_note, columns))
    row, col = divmod(index, columns)
    return bottom_left + (rows - 1 - row) * stride + col


def midi_key_for_note(model: str, columns: int, rows: int, note: int, base_note: int = 36) -> Optional[int]:
    for index in range(columns * rows):
        if midi_note_for_key(model, columns, rows, index, base_note) == note:
            return index
    return None

# Navigation pseudo-actions handled by the surface controller itself.
NAV_NEXT = "surface.next_page"
NAV_PREV = "surface.prev_page"
NAV_GOTO = "surface.goto_page"


@dataclass
class ButtonBinding:
    action: str = ""
    label: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    color: str = "#2b2f36"
    active_color: str = "#d9480f"


@dataclass
class SurfacePage:
    name: str = "Page"
    buttons: Dict[str, ButtonBinding] = field(default_factory=dict)

    def button(self, index: int) -> Optional[ButtonBinding]:
        return self.buttons.get(str(index))


@dataclass
class SurfaceLayout:
    name: str = "Default"
    model: str = "streamdeck_15"
    pages: List[SurfacePage] = field(default_factory=lambda: [SurfacePage(name="Main")])
    custom_columns: int = 4  # size of a "custom" surface
    custom_rows: int = 4
    midi_base_note: int = 36  # note of the bottom-left key on a custom MIDI surface

    @property
    def kind(self) -> str:
        return KINDS.get(self.model, "streamdeck")

    @property
    def columns(self) -> int:
        if self.model == "custom":
            return max(1, min(16, int(self.custom_columns)))
        return MODELS.get(self.model, MODELS["streamdeck_15"])[1]

    @property
    def rows(self) -> int:
        if self.model == "custom":
            return max(1, min(16, int(self.custom_rows)))
        return MODELS.get(self.model, MODELS["streamdeck_15"])[2]

    @property
    def key_count(self) -> int:
        return self.columns * self.rows

    def page_index(self, name: str) -> int:
        return next((i for i, p in enumerate(self.pages) if p.name == name), 0)


def load_layout(path: Optional[str] = None) -> SurfaceLayout:
    path = path or DEFAULT_LAYOUT_PATH
    with open(path, "r", encoding="utf-8") as fh:
        return from_dict(SurfaceLayout, json.load(fh))


def save_layout(layout: SurfaceLayout, path: str) -> None:
    atomic_write(path, json.dumps(to_dict(layout), indent=2))


def default_layout() -> SurfaceLayout:
    return load_layout(DEFAULT_LAYOUT_PATH)

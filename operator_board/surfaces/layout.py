"""Control surface layouts (Stream Deck and the on-screen virtual deck).

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

# model id -> (label, columns, rows)
MODELS: Dict[str, Tuple[str, int, int]] = {
    "streamdeck_mini": ("Stream Deck Mini (6 keys)", 3, 2),
    "streamdeck_15": ("Stream Deck / MK.2 (15 keys)", 5, 3),
    "streamdeck_plus": ("Stream Deck + (8 keys)", 4, 2),
    "streamdeck_neo": ("Stream Deck Neo (8 keys)", 4, 2),
    "streamdeck_xl": ("Stream Deck XL (32 keys)", 8, 4),
}

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

    @property
    def columns(self) -> int:
        return MODELS.get(self.model, MODELS["streamdeck_15"])[1]

    @property
    def rows(self) -> int:
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

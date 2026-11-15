"""Custom pages: pages an operator lays out from buttons and from the other pages' panels.

A page is a grid ``COLUMNS`` wide and as tall as it needs to be. Every item sits on whole cells
(``col``, ``row``, ``w``, ``h``) and items never overlap. An item is either a button (a control
surface ``ButtonBinding``, so it runs any action) or a panel: a named part of another page
(``source`` is that page's title, ``panel`` the part; see ``ui/pages/base.BasePage.panels``).

Pages are kept with the project in ``custom_pages.json``, so they travel in project exports and
templates, and can be exported to / imported from their own ``.json`` files.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from operator_board.core.config import atomic_write
from operator_board.core.serde import from_dict, to_dict

FILE_NAME = "custom_pages.json"
FILE_FORMAT = "operator-board-custom-pages"
COLUMNS = 24
MAX_ROWS = 400

KIND_BUTTON = "button"
KIND_PANEL = "panel"
WHOLE_PAGE = "*"  # the ``panel`` of an item showing a whole page
TAB_PREFIX = "tab:"  # the ``panel`` of an item showing one tab of a page
CONTROL_PREFIX = "ctl:"  # the ``panel`` of an item showing one control (a button, a drop-down, a readout...)

# Size (columns, rows) a new item starts with.
DEFAULT_SIZE = {KIND_BUTTON: (3, 2), KIND_PANEL: (8, 6)}


def new_id() -> str:
    return secrets.token_hex(4)


@dataclass
class PageItem:
    id: str = ""
    kind: str = KIND_BUTTON
    col: int = 0
    row: int = 0
    w: int = 3
    h: int = 2
    source: str = ""  # panel: the title of the page it comes from
    panel: str = ""   # panel: which part of that page
    button: Dict[str, Any] = field(default_factory=dict)  # button: a surfaces.layout.ButtonBinding

    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return self.col, self.row, self.w, self.h


@dataclass
class CustomPage:
    id: str = ""
    name: str = "Custom page"
    items: List[PageItem] = field(default_factory=list)

    def item(self, item_id: str) -> Optional[PageItem]:
        return next((i for i in self.items if i.id == item_id), None)

    @property
    def rows(self) -> int:
        """Rows in use."""
        return max((i.row + i.h for i in self.items), default=0)

    def fits(self, rect: Sequence[int], ignore: Optional[PageItem] = None) -> bool:
        """True when ``rect`` (col, row, w, h) is on the grid and touches no other item."""
        col, row, w, h = rect
        if w < 1 or h < 1 or col < 0 or row < 0 or col + w > COLUMNS or row + h > MAX_ROWS:
            return False
        return not any(overlaps(rect, other.rect) for other in self.items if other is not ignore)

    def free_spot(self, w: int, h: int) -> Tuple[int, int]:
        """The first place (reading left to right, top to bottom) with room for a ``w`` x ``h`` item."""
        w = max(1, min(COLUMNS, w))
        for row in range(self.rows + 1):
            for col in range(COLUMNS - w + 1):
                if self.fits((col, row, w, h)):
                    return col, row
        return 0, self.rows

    def add(self, item: PageItem) -> PageItem:
        """Give the item an id and the first free place of its size."""
        item.id = item.id or new_id()
        item.w = max(1, min(COLUMNS, item.w))
        item.h = max(1, item.h)
        if not self.fits(item.rect):
            item.col, item.row = self.free_spot(item.w, item.h)
        self.items.append(item)
        return item


def overlaps(a: Sequence[int], b: Sequence[int]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def repair(page: CustomPage) -> CustomPage:
    """Make a page read from a file safe to show: ids on everything, sizes on the grid, no overlaps."""
    page.id = page.id or new_id()
    items, page.items = page.items, []
    seen = set()
    for item in items:
        if item.kind not in (KIND_BUTTON, KIND_PANEL):
            continue
        if not item.id or item.id in seen:
            item.id = new_id()
        seen.add(item.id)
        item.w = max(1, min(COLUMNS, int(item.w)))
        item.h = max(1, min(MAX_ROWS, int(item.h)))
        item.col, item.row = max(0, int(item.col)), max(0, int(item.row))
        page.add(item)
    return page


def unique_name(name: str, taken: Iterable[str]) -> str:
    """``name``, or ``name 2``, ``name 3``... when it is taken (compared without case)."""
    name = " ".join((name or "").split()) or "Custom page"
    used = {t.lower() for t in taken}
    if name.lower() not in used:
        return name
    number = 2
    while f"{name} {number}".lower() in used:
        number += 1
    return f"{name} {number}"


# ---- files -----------------------------------------------------------------------------------
def pages_from(data: Any) -> List[CustomPage]:
    """Pages from the contents of a custom pages file."""
    if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
        raise ValueError("not an Operator Board custom pages file")
    return [repair(from_dict(CustomPage, p)) for p in data.get("pages", []) if isinstance(p, dict)]


def read_file(path: str) -> List[CustomPage]:
    with open(path, "r", encoding="utf-8") as fh:
        return pages_from(json.load(fh))


def write_file(path: str, pages: Sequence[CustomPage]) -> None:
    atomic_write(path, json.dumps({"format": FILE_FORMAT, "pages": [to_dict(p) for p in pages]}, indent=2))


class CustomPageStore:
    """The project's custom pages. ``path`` None keeps them in memory only."""

    def __init__(self, path: Optional[str] = None, bus=None) -> None:
        self.path = path
        self.bus = bus
        self.pages: List[CustomPage] = []
        self.load_error = ""
        # Names a custom page may not take (the built-in pages' titles; set by the window).
        self.reserved: List[str] = []
        self.load()

    def load(self) -> None:
        self.pages = []
        if not self.path or not os.path.exists(self.path):
            return
        try:
            self.pages = read_file(self.path)
        except (OSError, ValueError, TypeError) as exc:
            self.load_error = f"Could not read the custom pages ({self.path}): {exc}"

    def save(self) -> None:
        if self.path:
            write_file(self.path, self.pages)

    def changed(self, layout_only: bool = False) -> None:
        """Save and tell the window. ``layout_only``: items moved, added or removed on one page, so
        the side menu is unchanged."""
        error = ""
        try:
            self.save()
        except OSError as exc:
            error = f"Could not save the custom pages: {exc}"
        if self.bus:
            self.bus.publish("custom_pages.changed", layout_only=layout_only, error=error)

    # ---- looking up ----------------------------------------------------------------------
    def names(self) -> List[str]:
        return [p.name for p in self.pages]

    def get(self, name_or_id: str) -> Optional[CustomPage]:
        wanted = (name_or_id or "").strip().lower()
        return next((p for p in self.pages if p.id == name_or_id or p.name.lower() == wanted), None)

    def free_name(self, name: str, ignore: Optional[CustomPage] = None) -> str:
        return unique_name(name, [p.name for p in self.pages if p is not ignore] + list(self.reserved))

    # ---- changing ------------------------------------------------------------------------
    def add(self, name: str = "Custom page") -> CustomPage:
        page = CustomPage(id=new_id(), name=self.free_name(name))
        self.pages.append(page)
        self.changed()
        return page

    def rename(self, page: CustomPage, name: str) -> str:
        page.name = self.free_name(name, ignore=page)
        self.changed()
        return page.name

    def duplicate(self, page: CustomPage) -> CustomPage:
        copy = repair(from_dict(CustomPage, to_dict(page)))
        copy.id = new_id()
        for item in copy.items:
            item.id = new_id()
        copy.name = self.free_name(page.name + " copy")
        self.pages.insert(self.pages.index(page) + 1, copy)
        self.changed()
        return copy

    def remove(self, page: CustomPage) -> None:
        self.pages.remove(page)
        self.changed()

    def move(self, page: CustomPage, offset: int) -> None:
        index = self.pages.index(page)
        target = max(0, min(len(self.pages) - 1, index + offset))
        if target != index:
            self.pages.insert(target, self.pages.pop(index))
            self.changed()

    def add_pages(self, pages: Sequence[CustomPage], replace: bool = False) -> List[CustomPage]:
        """Add pages from a file or a preset. ``replace``: a page of the same name is replaced;
        otherwise the new one gets a free name. Returns the pages as added."""
        added = []
        for page in pages:
            page = repair(from_dict(CustomPage, to_dict(page)))
            existing = self.get(page.name) if replace else None
            if existing is not None:
                page.id = existing.id
                self.pages[self.pages.index(existing)] = page
            else:
                page.id = new_id() if any(p.id == page.id for p in self.pages) else page.id
                page.name = self.free_name(page.name)
                self.pages.append(page)
            added.append(page)
        if added:
            self.changed()
        return added

    def as_dicts(self) -> List[Dict[str, Any]]:
        return [to_dict(p) for p in self.pages]

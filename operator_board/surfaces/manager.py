"""Every control surface set up on this stage: several at once, including several of one type
(two Stream Deck Minis with different buttons, a phone and a tablet...).

Each surface is a SurfaceLayout with its own id, name, pages and the device that drives it. They
are stored in the stage configuration (``surfaces.devices``) and can be exported to / imported
from a file for backup or another machine.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from operator_board.core.actions import ActionRegistry
from operator_board.core.config import AppConfig, atomic_write
from operator_board.core.events import EventBus
from operator_board.core.serde import from_dict, to_dict
from operator_board.surfaces.controller import SurfaceController
from operator_board.surfaces.layout import (KINDS, MODELS, SurfaceLayout, default_layout, ensure_page_navigation,
                                            load_layout, new_id, new_surface)

log = logging.getLogger(__name__)

EXPORT_FORMAT = "operator-board-surfaces"


def unique_surface_name(name: str, taken: Iterable[str]) -> str:
    taken = {t.lower() for t in taken}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


def read_surfaces_file(path: str) -> List[SurfaceLayout]:
    """Surfaces from an export file, or a single layout saved by an older version."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and isinstance(data.get("surfaces"), list):
        return [from_dict(SurfaceLayout, d) for d in data["surfaces"]]
    if isinstance(data, dict) and "pages" in data:
        return [from_dict(SurfaceLayout, data)]
    raise ValueError("not an Operator Board control surface file")


def write_surfaces_file(path: str, layouts: Sequence[SurfaceLayout]) -> None:
    atomic_write(path, json.dumps({"format": EXPORT_FORMAT, "surfaces": [to_dict(l) for l in layouts]}, indent=2))


class SurfaceManager:
    def __init__(self, config: AppConfig, actions: ActionRegistry, bus: EventBus) -> None:
        self.config = config
        self.actions = actions
        self.bus = bus
        self.controllers: List[SurfaceController] = []
        self.load()

    # ---- configuration --------------------------------------------------------------
    def load(self) -> None:
        """(Re)build the surfaces from the configuration (after start-up or a stage switch)."""
        s = self.config.surfaces
        layouts = []
        for data in s.devices or []:
            try:
                layouts.append(from_dict(SurfaceLayout, data))
            except (TypeError, ValueError) as exc:
                log.warning("Skipping a control surface that could not be read: %s", exc)
        if not layouts and not s.devices_migrated:
            layouts = [self._migrated_layout()]
        for layout in layouts:
            layout.id = layout.id or new_id()
        self.controllers = [SurfaceController(l, self.actions, self.bus) for l in layouts]
        self.store()
        self.bus.publish("surface.changed")

    def _migrated_layout(self) -> SurfaceLayout:
        """The single layout older versions used (its file, or the default), as the first surface."""
        s = self.config.surfaces
        layout = None
        if s.layout_path:
            try:
                layout = load_layout(s.layout_path)
            except (OSError, ValueError, TypeError) as exc:
                log.warning("Could not load surface layout %s (%s); using default", s.layout_path, exc)
        if layout is None:
            layout = default_layout()
            layout.name = MODELS[layout.model][0].split(" (")[0]
        if layout.kind == "midi":
            layout.device, layout.enabled = s.midi_port, s.midi_enabled
        return layout

    def store(self) -> None:
        """Write the surfaces back into the configuration (saved with it)."""
        self.config.surfaces.devices = [to_dict(c.layout) for c in self.controllers]
        self.config.surfaces.devices_migrated = True

    # ---- lookup ---------------------------------------------------------------------
    @property
    def layouts(self) -> List[SurfaceLayout]:
        return [c.layout for c in self.controllers]

    @property
    def primary(self) -> Optional[SurfaceController]:
        return self.controllers[0] if self.controllers else None

    def find(self, ref: Any) -> Optional[SurfaceController]:
        """A surface by id, by name (any case) or by number (1 = the first)."""
        if ref is None or ref == "":
            return None
        text = str(ref)
        for c in self.controllers:
            if c.layout.id == text:
                return c
        for c in self.controllers:
            if c.layout.name.lower() == text.lower():
                return c
        if text.isdigit() and 1 <= int(text) <= len(self.controllers):
            return self.controllers[int(text) - 1]
        return None

    def first_of(self, kinds: Sequence[str]) -> Optional[SurfaceController]:
        """The first enabled surface of one of ``kinds`` (in that order of preference), else the first surface."""
        for kind in kinds:
            for c in self.controllers:
                if c.layout.enabled and KINDS.get(c.layout.model) == kind:
                    return c
        return self.primary

    def of_kind(self, *kinds: str) -> List[SurfaceController]:
        return [c for c in self.controllers if c.layout.enabled and KINDS.get(c.layout.model) in kinds]

    # ---- editing --------------------------------------------------------------------
    def _changed(self, devices: bool = True) -> None:
        self.store()
        self.bus.publish("surface.changed")
        if devices:  # surfaces added, removed, renamed, switched on/off or given another device
            self.bus.publish("surface.list_changed")

    def add(self, model: str, name: str = "") -> SurfaceController:
        layout = new_surface(model)
        layout.name = unique_surface_name(name or layout.name, (l.name for l in self.layouts))
        controller = SurfaceController(layout, self.actions, self.bus)
        self.controllers.append(controller)
        self._changed()
        return controller

    def add_layouts(self, layouts: Sequence[SurfaceLayout]) -> List[SurfaceController]:
        """Add imported surfaces. Their names and ids never replace a surface already here."""
        added = []
        for layout in layouts:
            layout = copy.deepcopy(layout)
            if not layout.id or any(l.id == layout.id for l in self.layouts):
                layout.id = new_id()
            layout.name = unique_surface_name(layout.name or "Surface", (l.name for l in self.layouts))
            controller = SurfaceController(layout, self.actions, self.bus)
            self.controllers.append(controller)
            added.append(controller)
        self._changed()
        return added

    def duplicate(self, controller: SurfaceController) -> SurfaceController:
        layout = copy.deepcopy(controller.layout)
        layout.device = ""  # the copy drives a different device of the same type
        return self.add_layouts([layout])[0]

    def remove(self, controller: SurfaceController) -> None:
        if controller in self.controllers:
            self.controllers.remove(controller)
            self._changed()

    def rename(self, controller: SurfaceController, name: str) -> str:
        name = name.strip()
        if name and name != controller.layout.name:
            others = (l.name for l in self.layouts if l is not controller.layout)
            controller.layout.name = unique_surface_name(name, others)
            self._changed()
        return controller.layout.name

    def changed(self, controller: Optional[SurfaceController] = None, devices: bool = False,
                navigation: bool = False) -> None:
        """After editing a surface's keys or pages. ``devices``: its type, device or on/off changed;
        ``navigation``: pages were added or the size changed, so give every page a Next page key
        where it has none (an operator can still clear it afterwards)."""
        if controller is not None and navigation:
            ensure_page_navigation(controller.layout)
        self._changed(devices)

    # ---- files ------------------------------------------------------------------------
    def export(self, path: str, controllers: Optional[Sequence[SurfaceController]] = None) -> int:
        chosen = list(controllers) if controllers else self.controllers
        write_surfaces_file(path, [c.layout for c in chosen])
        return len(chosen)

    def import_file(self, path: str) -> List[SurfaceController]:
        return self.add_layouts(read_surfaces_file(path))

    def state(self) -> List[Dict[str, Any]]:
        return [{"id": c.layout.id, "name": c.layout.name, "model": c.layout.model, "kind": c.layout.kind,
                 "enabled": c.layout.enabled} for c in self.controllers]

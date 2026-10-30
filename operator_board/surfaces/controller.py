"""Maps surface key presses to actions and tracks the current page."""
from __future__ import annotations

from typing import Callable, List, Optional

from operator_board.core.actions import ActionRegistry, ActionResult
from operator_board.core.events import EventBus
from operator_board.surfaces.layout import (NAV_GOTO, NAV_NEXT, NAV_PREV, ButtonBinding, SurfaceLayout)


class SurfaceController:
    def __init__(self, layout: SurfaceLayout, actions: ActionRegistry, bus: EventBus) -> None:
        self.layout = layout
        self.actions = actions
        self.bus = bus
        self.page = 0

    def set_layout(self, layout: SurfaceLayout) -> None:
        self.layout = layout
        self.page = 0
        self.bus.publish("surface.changed")

    @property
    def current_page(self):
        if not self.layout.pages:
            return None
        self.page %= len(self.layout.pages)
        return self.layout.pages[self.page]

    def binding(self, index: int) -> Optional[ButtonBinding]:
        page = self.current_page
        return page.button(index) if page else None

    def is_active(self, index: int) -> bool:
        b = self.binding(index)
        return bool(b and b.action and self.actions.state_of(b.action))

    def press(self, index: int) -> ActionResult:
        b = self.binding(index)
        if b is None or not b.action:
            return ActionResult(False, "Unassigned key")
        if b.action in (NAV_NEXT, NAV_PREV, NAV_GOTO):
            n = max(1, len(self.layout.pages))
            if b.action == NAV_NEXT:
                self.page = (self.page + 1) % n
            elif b.action == NAV_PREV:
                self.page = (self.page - 1) % n
            else:
                self.page = self.layout.page_index(str(b.params.get("page", "")))
            self.bus.publish("surface.changed")
            return ActionResult(True, f"Page: {self.current_page.name}")
        return self.actions.invoke(b.action, **b.params)

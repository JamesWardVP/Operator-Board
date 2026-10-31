"""Central registry of operator actions.

Every operation an operator can trigger (freeze the wall, load a level, start a
group...) is registered here once, then invoked identically from the UI,
control surfaces (Stream Deck), and the local HTTP API used by Bitfocus Companion.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class ActionParam:
    name: str
    label: str
    kind: str = "str"  # str | int | float | bool | choice
    default: Any = None
    choices_provider: Optional[Callable[[], List[str]]] = None

    def choices(self) -> List[str]:
        try:
            return list(self.choices_provider()) if self.choices_provider else []
        except Exception:
            return []


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    data: Any = None


@dataclass
class Action:
    id: str
    label: str
    category: str
    handler: Callable[..., Any]
    description: str = ""
    params: List[ActionParam] = field(default_factory=list)
    # Optional callable returning the current on/off state, used for toggle buttons.
    state: Optional[Callable[[], Optional[bool]]] = None
    # Ask before running from the UI: a bool, or a callable so it follows the current settings.
    confirm: Any = False

    def needs_confirm(self) -> bool:
        return bool(self.confirm() if callable(self.confirm) else self.confirm)


class ActionRegistry:
    def __init__(self, bus=None) -> None:
        self._actions: Dict[str, Action] = {}
        self._lock = threading.Lock()
        self._bus = bus

    def register(self, action: Action) -> Action:
        with self._lock:
            self._actions[action.id] = action
        return action

    def add(self, id: str, label: str, category: str, handler: Callable[..., Any], **kwargs: Any) -> Action:
        return self.register(Action(id=id, label=label, category=category, handler=handler, **kwargs))

    def get(self, action_id: str) -> Optional[Action]:
        return self._actions.get(action_id)

    def all(self) -> List[Action]:
        with self._lock:
            return sorted(self._actions.values(), key=lambda a: (a.category, a.label))

    def categories(self) -> List[str]:
        return sorted({a.category for a in self.all()})

    def state_of(self, action_id: str) -> Optional[bool]:
        action = self.get(action_id)
        if not action or not action.state:
            return None
        try:
            return action.state()
        except Exception:
            return None

    def invoke(self, action_id: str, **params: Any) -> ActionResult:
        action = self.get(action_id)
        if action is None:
            return ActionResult(False, f"Unknown action '{action_id}'")
        try:
            value = action.handler(**params)
        except Exception as exc:
            log.exception("Action %s failed", action_id)
            result = ActionResult(False, f"{action.label}: {exc}")
        else:
            result = value if isinstance(value, ActionResult) else ActionResult(True, action.label, value)
        if self._bus:
            self._bus.publish("action.invoked", action_id=action_id, ok=result.ok, message=result.message)
        return result

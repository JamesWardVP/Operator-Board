"""Tiny thread-safe publish/subscribe bus used to decouple core services from the UI.

Callbacks run on the publishing thread. The Qt layer bridges events onto the GUI
thread (see operator_board.ui.bridge).
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable, Dict, List

log = logging.getLogger(__name__)

Callback = Callable[[str, Dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: Dict[str, List[Callback]] = defaultdict(list)

    def subscribe(self, topic: str, callback: Callback) -> Callable[[], None]:
        """Subscribe to a topic. Use '*' to receive everything. Returns an unsubscribe function."""
        with self._lock:
            self._subs[topic].append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subs[topic]:
                    self._subs[topic].remove(callback)

        return unsubscribe

    def publish(self, topic: str, **payload: Any) -> None:
        with self._lock:
            targets = list(self._subs.get(topic, ())) + list(self._subs.get("*", ()))
        for cb in targets:
            try:
                cb(topic, payload)
            except Exception:  # a broken listener must never take down a service
                log.exception("Event handler failed for %s", topic)

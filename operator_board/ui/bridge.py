"""Moves core events and background work results onto the Qt GUI thread."""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from operator_board.qt import QtCore, Signal


class EventBridge(QtCore.QObject):
    event = Signal(str, dict)

    def __init__(self, bus) -> None:
        super().__init__()
        bus.subscribe("*", lambda topic, payload: self.event.emit(topic, dict(payload)))


class _Invoker(QtCore.QObject):
    done = Signal(object, object)  # (callback, value)

    def __init__(self) -> None:
        super().__init__()
        self.done.connect(lambda cb, value: cb(value))


_invoker: Optional[_Invoker] = None


def run_async(fn: Callable[[], Any], on_done: Optional[Callable[[Any], None]] = None,
              on_error: Optional[Callable[[Exception], None]] = None) -> None:
    """Run fn on a worker thread; deliver its result (or exception) back on the GUI thread."""
    global _invoker
    if _invoker is None:
        _invoker = _Invoker()
    inv = _invoker

    def work() -> None:
        try:
            value = fn()
        except Exception as exc:  # delivered to the UI, never swallowed silently
            if on_error:
                inv.done.emit(on_error, exc)
            return
        if on_done:
            inv.done.emit(on_done, value)

    threading.Thread(target=work, daemon=True).start()

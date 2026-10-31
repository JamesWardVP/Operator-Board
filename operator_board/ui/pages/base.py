from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from operator_board.core.actions import ActionResult
from operator_board.qt import QtWidgets
from operator_board.ui.bridge import run_async


class BasePage(QtWidgets.QWidget):
    title = "Page"
    icon = ""
    # topics (prefixes) that trigger refresh(); empty = never auto refresh on events
    refresh_topics: tuple = ()

    def __init__(self, ctx, window) -> None:
        super().__init__()
        self.ctx = ctx
        self.window_ = window

    # ---- helpers ---------------------------------------------------------------
    def notify(self, message: str, ok: bool = True) -> None:
        self.window_.notify(message, ok)

    def invoke(self, action_id: str, done: Optional[Callable[[ActionResult], None]] = None, **params: Any) -> None:
        action = self.ctx.actions.get(action_id)
        if action and action.needs_confirm():
            if QtWidgets.QMessageBox.question(self, "Confirm", f"{action.label}?") != QtWidgets.QMessageBox.Yes:
                return

        def finished(result: ActionResult) -> None:
            if result.message:
                self.notify(result.message, result.ok)
            if done:
                done(result)
        run_async(lambda: self.ctx.actions.invoke(action_id, **params), finished)

    def background(self, fn: Callable[[], Any], done: Optional[Callable[[Any], None]] = None,
                   what: str = "Operation") -> None:
        run_async(fn, done, lambda exc: self.notify(f"{what} failed: {exc}", False))

    def report(self, label: str):
        """Callback for background calls returning a list of error strings."""
        def cb(errors) -> None:
            if errors:
                self.notify(f"{label}: " + "; ".join(errors), False)
            else:
                self.notify(label, True)
        return cb

    def on_event(self, topic: str, payload: Dict[str, Any]) -> None:
        if any(topic.startswith(t) for t in self.refresh_topics) and self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def save_config(self) -> None:
        try:
            self.ctx.save()
        except OSError as exc:
            self.notify(f"Could not save configuration: {exc}", False)

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from operator_board.core.actions import ActionResult
from operator_board.core.custom_pages import CONTROL_PREFIX, TAB_PREFIX
from operator_board.qt import QtWidgets
from operator_board.ui.bridge import run_async
from operator_board.ui.widgets import Card


class BasePage(QtWidgets.QWidget):
    title = "Page"
    icon = ""
    # topics (prefixes) that trigger refresh(); empty = never auto refresh on events
    refresh_topics: tuple = ()
    # Refreshed every second while on screen (the main window's clock tick).
    refresh_every_second = False
    # Listed under "Setup" when choosing panels for a custom page.
    setup_page = False
    # False: the page's parts are made as it runs, so a custom page can only show the whole page.
    panels_from_cards = True
    # False: never offered as a panel for a custom page.
    in_catalogue = True

    def __init__(self, ctx, window) -> None:
        super().__init__()
        self.ctx = ctx
        self.window_ = window
        # Set when this page is the hidden owner of panels shown on a custom page.
        self.host: Optional[QtWidgets.QWidget] = None

    # ---- panels for custom pages -------------------------------------------------
    def is_live(self) -> bool:
        """On screen: as the page itself, or through its panels on a custom page."""
        return self.isVisible() or (self.host is not None and self.host.isVisible())

    def extra_panels(self) -> List[Tuple[str, str, QtWidgets.QWidget]]:
        """(key, label, widget) for parts of the page that are not cards."""
        return []

    def panels(self) -> List[Tuple[str, str, QtWidgets.QWidget]]:
        """(key, label, widget) for every part of this page a custom page can show on its own: each
        tab (key ``tab:<name>``), each titled card (key = its title) and ``extra_panels``."""
        found: List[Tuple[str, str, QtWidgets.QWidget]] = []
        tabs = getattr(self, "tabs", None)
        if isinstance(tabs, QtWidgets.QTabWidget):
            for index in range(tabs.count()):
                name = tabs.tabText(index).replace("&&", "&")
                found.append((TAB_PREFIX + name, f"{name} (whole tab)", tabs.widget(index)))
        found += self.extra_panels()
        if self.panels_from_cards:
            keys = {key for key, _label, _widget in found}
            for card in self.findChildren(Card):
                if card.title and card.title not in keys:
                    keys.add(card.title)
                    found.append((card.title, card.title, card))
        return found

    # Plain-language names for controls whose own text does not say what they are: attribute -> label.
    control_labels: Dict[str, str] = {}
    # Named widgets that are not controls (notes, overlays): attribute names never offered.
    control_skip: Tuple[str, ...] = ()
    # Parts of other widgets, never offered on their own.
    _INNER = (QtWidgets.QAbstractItemView, QtWidgets.QTabBar, QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox,
              QtWidgets.QDialogButtonBox, QtWidgets.QScrollBar, QtWidgets.QMenu)

    def controls(self) -> List[Tuple[str, str, QtWidgets.QWidget, str]]:
        """(key, label, widget, card title) for the single controls inside this page's cards that a
        custom page can show on their own: every widget the page keeps by name (key ``ctl:<name>``, so
        it survives a change of wording) and every other button with text of its own
        (key ``ctl:<card>/<text>``)."""
        def card_of(widget) -> Optional[Card]:
            parent = widget.parentWidget()
            while parent is not None and parent is not self:
                if isinstance(parent, self._INNER) or type(parent).__name__ == "ColorField":
                    return None
                if isinstance(parent, Card) and parent.title:
                    return parent
                parent = parent.parentWidget()
            return None

        found: List[Tuple[str, str, QtWidgets.QWidget, str]] = []
        seen = set()
        for name, value in vars(self).items():
            if name.startswith("_") or name in self.control_skip or not isinstance(value, QtWidgets.QWidget) \
                    or isinstance(value, Card) or type(value).__name__ in ("HazardOverlay", "UnsupportedTape"):
                continue
            try:
                card = card_of(value)
            except RuntimeError:  # the widget has been deleted
                continue
            if card is not None:
                seen.add(id(value))
                found.append((CONTROL_PREFIX + name, self._control_label(name, value), value, card.title))
        if not self.panels_from_cards:
            return found
        buttons: Dict[str, Tuple[str, QtWidgets.QWidget, str]] = {}
        repeated = set()
        for card in self.findChildren(Card):
            if not card.title:
                continue
            for button in card.findChildren(QtWidgets.QAbstractButton):
                text = " ".join(button.text().replace("&&", "&").split())
                if id(button) in seen or not text or button.objectName() == "CardLink" or card_of(button) is not card:
                    continue
                key = f"{CONTROL_PREFIX}{card.title}/{text}"
                if key in buttons:
                    repeated.add(key)  # one per row of a list (Start, Edit...): not a control of its own
                buttons[key] = (text, button, card.title)
        found += [(key, text, widget, title) for key, (text, widget, title) in buttons.items() if key not in repeated]
        return found

    def _control_label(self, name: str, widget: QtWidgets.QWidget) -> str:
        if name in self.control_labels:
            return self.control_labels[name]
        kind = ("drop-down" if isinstance(widget, QtWidgets.QComboBox)
                else "text box" if isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit,
                                                       QtWidgets.QAbstractSpinBox))
                else "list" if isinstance(widget, QtWidgets.QAbstractItemView)
                else "readout" if isinstance(widget, QtWidgets.QLabel) else "")
        text = ""
        if isinstance(widget, QtWidgets.QAbstractButton):
            text = " ".join(widget.text().replace("&&", "&").split())
        elif isinstance(widget, QtWidgets.QLineEdit):
            text = widget.placeholderText()
        if not text:
            words = [w for w in name.split("_") if w not in ("btn", "combo", "edit", "label", "box", "value")]
            text = " ".join(words).capitalize() or name
        return f"{text} ({kind})" if kind else text

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
                   what: str = "Operation", quiet: bool = False,
                   failed: Optional[Callable[[Exception], None]] = None) -> None:
        """Run ``fn`` off the UI thread. ``quiet``: no failure message (automatic refreshes);
        ``failed`` is called with the error instead or as well."""
        def on_error(exc) -> None:
            if failed:
                failed(exc)
            if not quiet:
                self.notify(f"{what} failed: {exc}", False)
        run_async(fn, done, on_error)

    def report(self, label: str):
        """Callback for background calls returning a list of error strings."""
        def cb(errors) -> None:
            if errors:
                self.notify(f"{label}: " + "; ".join(errors), False)
            else:
                self.notify(label, True)
        return cb

    def on_event(self, topic: str, payload: Dict[str, Any]) -> None:
        if any(topic.startswith(t) for t in self.refresh_topics) and self.is_live():
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

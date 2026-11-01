"""Elgato Stream Deck hardware backend (optional: pip install streamdeck pillow).

Uses the open-source python-elgato-streamdeck library, which talks to the
hardware directly over HID. Quit the Elgato Stream Deck app first, because only
one program can own the device. Operators who prefer Bitfocus Companion can
instead point Companion buttons at the local HTTP API (see api_server.py).

Several decks work at once. Each surface of a Stream Deck type drives one deck: the deck whose
serial number is the surface's ``device``, otherwise the next free deck with the same number of
keys. ``scan()`` opens decks plugged in since the last scan and drops unplugged ones (the app
calls it when Windows reports a device change, and from the Refresh devices button).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Dict, List, Optional

from operator_board.core.events import EventBus
from operator_board.surfaces.controller import SurfaceController
from operator_board.surfaces.layout import MODELS
from operator_board.surfaces.render import fit_label, LINE_SPACING

log = logging.getLogger(__name__)

try:  # optional dependency
    from StreamDeck.DeviceManager import DeviceManager as _SDManager
    from StreamDeck.ImageHelpers import PILHelper
    from PIL import ImageDraw, ImageFont
    AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional packages / hidapi
    AVAILABLE = False

# Windows fonts by weight (Segoe UI), else Pillow's built-in font.
_FONT_FILES = {"light": "segoeuil.ttf", "normal": "segoeui.ttf", "semibold": "seguisb.ttf", "bold": "segoeuib.ttf",
               "black": "seguibl.ttf"}
_fonts: Dict[tuple, object] = {}


def _hex_to_rgb(color: str, fallback=(40, 40, 40)):
    color = (color or "").lstrip("#")
    try:
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def _font(weight: str, size: int):
    key = (weight, size)
    if key not in _fonts:
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", _FONT_FILES.get(weight, "segoeuib.ttf"))
        try:
            _fonts[key] = ImageFont.truetype(path, size)
        except OSError:
            try:
                _fonts[key] = ImageFont.load_default(size)
            except TypeError:  # Pillow older than 10.1
                _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def model_for(key_count: int, deck_type: str = "") -> str:
    """The surface model of a deck with ``key_count`` keys."""
    if key_count == 8:
        return "streamdeck_neo" if "neo" in deck_type.lower() else "streamdeck_plus"
    return {6: "streamdeck_mini", 15: "streamdeck_15", 32: "streamdeck_xl"}.get(key_count, "streamdeck_15")


class _Deck:
    def __init__(self, device) -> None:
        self.device = device
        self.path = device.id()
        self.serial = ""
        self.deck_type = ""
        self.key_count = 0
        self.surface: Optional[SurfaceController] = None


class StreamDeckBackend:
    def __init__(self, surfaces, bus: EventBus, brightness: int = 60) -> None:
        self.surfaces = surfaces  # SurfaceManager
        self.bus = bus
        self.brightness = brightness
        self.decks: List[_Deck] = []
        self._lock = threading.RLock()
        self._unsubs = []

    @staticmethod
    def available() -> bool:
        return AVAILABLE

    def start(self) -> int:
        if not AVAILABLE:
            return 0
        for topic in ("surface.changed", "led.state", "takes.state", "multiuser.state", "device.state", "action.invoked"):
            self._unsubs.append(self.bus.subscribe(topic, lambda *_: self.refresh()))
        self._unsubs.append(self.bus.subscribe("surface.list_changed", lambda *_: self.assign()))
        return self.scan()

    def scan(self) -> int:
        """Open newly plugged-in decks, forget unplugged ones, then match decks to surfaces."""
        if not AVAILABLE:
            return 0
        try:
            found = [d for d in _SDManager().enumerate() if d.is_visual()]
        except Exception as exc:  # noqa: BLE001
            log.warning("Stream Deck enumeration failed: %s", exc)
            return len(self.decks)
        with self._lock:
            paths = {d.id() for d in found}
            for deck in [d for d in self.decks if d.path not in paths or not self._connected(d)]:
                self._close(deck)
                self.decks.remove(deck)
            known = {d.path for d in self.decks}
            for device in found:
                if device.id() in known:
                    continue
                deck = _Deck(device)
                try:
                    device.open()
                    device.reset()
                    device.set_brightness(self.brightness)
                    deck.serial = str(device.get_serial_number() or "").strip("\x00 ")
                    deck.deck_type = device.deck_type()
                    deck.key_count = device.key_count()
                    device.set_key_callback(self._on_key)
                    self.decks.append(deck)
                except Exception as exc:  # noqa: BLE001 - e.g. the Elgato app still owns it
                    log.warning("Could not open Stream Deck %s: %s", device.id(), exc)
                    self._close(deck)
        self.assign()
        return len(self.decks)

    @staticmethod
    def _connected(deck: _Deck) -> bool:
        try:
            return bool(deck.device.connected()) if hasattr(deck.device, "connected") else True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _close(deck: _Deck) -> None:
        try:
            deck.device.reset()
        except Exception:  # noqa: BLE001
            pass
        try:
            deck.device.close()
        except Exception:  # noqa: BLE001
            pass

    def assign(self) -> None:
        """Give every Stream Deck surface its deck: by serial number first, then the next free deck
        with the same number of keys."""
        with self._lock:
            for deck in self.decks:
                deck.surface = None
            wanted = self.surfaces.of_kind("streamdeck")
            for surface in wanted:
                serial = surface.layout.device
                deck = next((d for d in self.decks if serial and d.serial == serial and d.surface is None), None)
                if deck:
                    deck.surface = surface
            for surface in wanted:
                if any(d.surface is surface for d in self.decks):
                    continue
                serial = surface.layout.device
                if serial and any(d.serial == serial for d in self.decks):
                    continue
                keys = MODELS.get(surface.layout.model, ("", 0, 0))
                deck = next((d for d in self.decks if d.surface is None and d.key_count == keys[1] * keys[2]), None)
                if deck:
                    deck.surface = surface
        self.refresh()
        self.bus.publish("surface.devices")

    def deck_for(self, surface: SurfaceController) -> Optional[_Deck]:
        return next((d for d in self.decks if d.surface is surface), None)

    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        with self._lock:
            for deck in self.decks:
                self._close(deck)
            self.decks = []

    def _on_key(self, device, key: int, pressed: bool) -> None:
        if not pressed:
            return
        deck = next((d for d in self.decks if d.device is device), None)
        if deck and deck.surface:
            threading.Thread(target=deck.surface.press, args=(key,), daemon=True).start()

    def refresh(self) -> None:
        with self._lock:
            for deck in list(self.decks):
                try:
                    for key in range(deck.key_count):
                        self._render(deck, key)
                except Exception as exc:  # noqa: BLE001 - unplugged mid-update; the next scan drops it
                    log.debug("Stream Deck %s update failed: %s", deck.serial or deck.path, exc)

    def _render(self, deck: _Deck, key: int) -> None:
        device = deck.device
        image = PILHelper.create_key_image(device)
        draw = ImageDraw.Draw(image)
        surface = deck.surface
        if surface is None:
            if key == 0:  # say why the deck is blank
                self._draw_text(draw, image, "Set up in\nOperator\nBoard", "#868e96", 0, "semibold", True)
            device.set_key_image(key, PILHelper.to_native_key_format(device, image))
            return
        binding = surface.binding(key)
        if binding and binding.action:
            color = binding.active_color if surface.is_active(key) else binding.color
            draw.rectangle((0, 0, image.width, image.height), fill=_hex_to_rgb(color))
            self._draw_text(draw, image, binding.label or binding.action, binding.text_color, binding.font_size,
                            binding.font_weight, binding.word_wrap)
        device.set_key_image(key, PILHelper.to_native_key_format(device, image))

    @staticmethod
    def _draw_text(draw, image, text: str, color: str, size: int, weight: str, wrap: bool) -> None:
        pad = max(3, image.width // 14)
        width, height = image.width - 2 * pad, image.height - 2 * pad

        def measure(line: str, px: int):
            box = draw.textbbox((0, 0), line or " ", font=_font(weight, px))
            return box[2] - box[0], _font(weight, px).size

        lines, px = fit_label(text, width, height, measure, size, wrap, key_px=image.width)
        font = _font(weight, px)
        step = font.size * LINE_SPACING
        top = image.height / 2 - step * (len(lines) - 1) / 2
        for n, line in enumerate(lines):
            draw.text((image.width / 2, top + n * step), line, font=font, anchor="mm",
                      fill=_hex_to_rgb(color, (255, 255, 255)))

"""Elgato Stream Deck hardware backend (optional: pip install streamdeck pillow).

Uses the open-source python-elgato-streamdeck library, which talks to the
hardware directly over HID. Quit the Elgato Stream Deck app first, because only
one program can own the device. Operators who prefer Bitfocus Companion can
instead point Companion buttons at the local HTTP API (see api_server.py).
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

from operator_board.core.events import EventBus
from operator_board.surfaces.controller import SurfaceController

log = logging.getLogger(__name__)

try:  # optional dependency
    from StreamDeck.DeviceManager import DeviceManager as _SDManager
    from StreamDeck.ImageHelpers import PILHelper
    from PIL import Image, ImageDraw, ImageFont
    AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional packages / hidapi
    AVAILABLE = False


def _hex_to_rgb(color: str):
    color = color.lstrip("#")
    try:
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (40, 40, 40)


class StreamDeckBackend:
    def __init__(self, controller: SurfaceController, bus: EventBus, brightness: int = 60) -> None:
        self.controller = controller
        self.bus = bus
        self.brightness = brightness
        self.decks: List = []
        self._lock = threading.Lock()
        self._unsubs = []

    @staticmethod
    def available() -> bool:
        return AVAILABLE

    def start(self) -> int:
        if not AVAILABLE:
            return 0
        try:
            decks = _SDManager().enumerate()
        except Exception as exc:
            log.warning("Stream Deck enumeration failed: %s", exc)
            return 0
        for deck in decks:
            if not deck.is_visual():
                continue
            try:
                deck.open()
                deck.reset()
                deck.set_brightness(self.brightness)
                deck.set_key_callback(self._on_key)
                self.decks.append(deck)
            except Exception as exc:
                log.warning("Could not open Stream Deck: %s", exc)
        for topic in ("surface.changed", "led.state", "takes.state", "multiuser.state", "device.state", "action.invoked"):
            self._unsubs.append(self.bus.subscribe(topic, lambda *_: self.refresh()))
        self.refresh()
        return len(self.decks)

    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        for deck in self.decks:
            try:
                deck.reset()
                deck.close()
            except Exception:
                pass
        self.decks = []

    def _on_key(self, deck, key: int, pressed: bool) -> None:
        if pressed:
            threading.Thread(target=self.controller.press, args=(key,), daemon=True).start()

    def refresh(self) -> None:
        with self._lock:
            for deck in self.decks:
                for key in range(deck.key_count()):
                    self._render(deck, key)

    def _render(self, deck, key: int) -> None:
        binding = self.controller.binding(key)
        image = PILHelper.create_image(deck)
        draw = ImageDraw.Draw(image)
        if binding and binding.action:
            color = binding.active_color if self.controller.is_active(key) else binding.color
            draw.rectangle((0, 0, image.width, image.height), fill=_hex_to_rgb(color))
            font = ImageFont.load_default()
            draw.multiline_text((image.width / 2, image.height / 2), binding.label or binding.action,
                                font=font, anchor="mm", align="center", fill=(255, 255, 255))
        deck.set_key_image(key, PILHelper.to_native_format(deck, image))

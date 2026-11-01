"""MIDI pad controllers as control surfaces (optional: pip install mido python-rtmidi).

A pad's note-on presses the key at that position (see layout.MIDI_MAPS for each controller's
pad numbering). Pad lights follow the keys where the controller supports it: assigned keys
light up, keys whose action is active (wall frozen, recording...) light brighter or red.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional, Sequence

from operator_board.core.events import EventBus
from operator_board.surfaces.controller import SurfaceController
from operator_board.surfaces.layout import midi_key_for_note, midi_note_for_key

log = logging.getLogger(__name__)

try:  # optional dependency
    import mido
    AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional packages
    mido = None
    AVAILABLE = False

# Pad light colours by velocity: (assigned, active). APC mini: 1 green, 3 red; Launchpad: palette.
FEEDBACK = {"midi_apc_mini": (1, 3), "midi_launchpad": (21, 5)}
DEFAULT_FEEDBACK = (0, 127)


def input_names() -> List[str]:
    if not AVAILABLE:
        return []
    try:
        return list(mido.get_input_names())
    except Exception:  # noqa: BLE001 - no MIDI backend on this machine
        return []


class MidiBackend:
    def __init__(self, controller: SurfaceController, bus: EventBus, port_name: str = "",
                 taken: Sequence[str] = ()) -> None:
        self.controller = controller
        self.bus = bus
        self.port_name = port_name
        self.taken = set(taken)  # inputs other MIDI surfaces already use
        self.input = None
        self.output = None
        self.opened = ""
        self._unsubs = []

    @staticmethod
    def available() -> bool:
        return AVAILABLE

    def _match(self, names: List[str]) -> Optional[str]:
        if self.port_name:
            return next((n for n in names if self.port_name.lower() in n.lower()), None)
        return next((n for n in names if n not in self.taken), None)

    def start(self) -> bool:
        if not AVAILABLE:
            return False
        name = self._match(input_names())
        if name is None:
            log.info("No MIDI input%s found", f" matching '{self.port_name}'" if self.port_name else "")
            return False
        try:
            self.input = mido.open_input(name, callback=self._on_message)
            self.opened = name
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not open MIDI input %s: %s", name, exc)
            return False
        try:  # same device's output, for the pad lights (best effort)
            outputs = mido.get_output_names()
            out = next((n for n in outputs if n.split(" ")[0] in name), None) or (
                next((n for n in outputs if self.port_name.lower() in n.lower()), None) if self.port_name else None)
            self.output = mido.open_output(out) if out else None
        except Exception:  # noqa: BLE001
            self.output = None
        for topic in ("surface.changed", "led.state", "takes.state", "multiuser.state", "device.state",
                      "action.invoked"):
            self._unsubs.append(self.bus.subscribe(topic, lambda *_: self.refresh_lights()))
        self.refresh_lights()
        return True

    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        for port in (self.input, self.output):
            try:
                if port:
                    port.close()
            except Exception:  # noqa: BLE001
                pass
        self.input = self.output = None

    def _layout_args(self):
        layout = self.controller.layout
        return layout.model, layout.columns, layout.rows

    def key_for(self, note: int) -> Optional[int]:
        model, cols, rows = self._layout_args()
        return midi_key_for_note(model, cols, rows, note, self.controller.layout.midi_base_note)

    def _on_message(self, message) -> None:
        if message.type == "note_on" and message.velocity > 0:
            key = self.key_for(message.note)
            if key is not None:
                threading.Thread(target=self.controller.press, args=(key,), daemon=True).start()

    def refresh_lights(self) -> None:
        if self.output is None:
            return
        model, cols, rows = self._layout_args()
        assigned_v, active_v = FEEDBACK.get(model, DEFAULT_FEEDBACK)
        for key in range(cols * rows):
            binding = self.controller.binding(key)
            velocity = 0
            if binding and binding.action:
                velocity = active_v if self.controller.is_active(key) else assigned_v
            note = midi_note_for_key(model, cols, rows, key, self.controller.layout.midi_base_note)
            try:
                self.output.send(mido.Message("note_on", note=max(0, min(127, note)), velocity=velocity))
            except Exception:  # noqa: BLE001 - lights are a nicety
                return

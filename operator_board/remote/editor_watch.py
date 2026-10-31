"""Notices when the primary editor has finished loading (or changed level) so lists can fill themselves.

Publishes ``editor.ready`` (and ``editor.level_changed`` when a different level is opened, ``editor.lost``
when it goes away). Pages listen and pull sub-levels, data layers, cameras, presets and actors, so the
operator does not have to press every Refresh button after launching Unreal.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from operator_board.core.events import EventBus
from operator_board.remote.rc_client import RemoteControlClient, python_result_wrapper

log = logging.getLogger(__name__)

# Ready = an editor world exists and the asset registry has finished its initial scan.
READY_SCRIPT = """
sub = getattr(unreal, 'UnrealEditorSubsystem', None)
world = unreal.get_editor_subsystem(sub).get_editor_world() if sub else unreal.EditorLevelLibrary.get_editor_world()
registry = unreal.AssetRegistryHelpers.get_asset_registry()
loading = registry.is_loading_assets() if hasattr(registry, 'is_loading_assets') else False
result = {'ready': bool(world) and not loading, 'level': world.get_path_name() if world else ''}
"""


class EditorWatcher:
    WAIT_S = 3.0    # while no editor is ready
    READY_S = 5.0   # while one is (to notice a level change or the editor closing)
    TIMEOUT_S = 3.0

    def __init__(self, targets, bus: EventBus) -> None:
        self.targets = targets
        self.bus = bus
        self.ready = False
        self.level = ""
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="editor-watch").start()

    def stop(self) -> None:
        self._stop.set()

    def check(self) -> Optional[dict]:
        """One look at the primary editor: its ready state, or None if it cannot be reached."""
        name = self.targets.primary_name()
        dev = self.targets.config.device(name) if name else None
        if dev is None or not dev.rc_enabled:
            return None
        client = RemoteControlClient(dev.address, dev.rc_port, self.TIMEOUT_S)  # short timeout, own client
        try:
            result = client.run_python(python_result_wrapper(READY_SCRIPT))
        except Exception as exc:  # noqa: BLE001 - no editor running is normal
            log.debug("editor not ready: %s", exc)
            return None
        return result if isinstance(result, dict) else None

    def update(self, result: Optional[dict]) -> None:
        if result and result.get("ready"):
            level = str(result.get("level") or "")
            if not self.ready:
                self.ready, self.level = True, level
                self.bus.publish("editor.ready", level=level)
            elif level != self.level:
                self.level = level
                self.bus.publish("editor.level_changed", level=level)
        elif self.ready and result is None:
            self.ready, self.level = False, ""
            self.bus.publish("editor.lost")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.update(self.check())
            self._stop.wait(self.READY_S if self.ready else self.WAIT_S)

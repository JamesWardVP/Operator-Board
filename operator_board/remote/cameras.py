"""Saved camera locations: recall onto the editor viewport or any actor (cine camera, nDisplay root...).

Positions are applied in the primary editor (an undoable change that multi-user sends to the nodes).
When no editor answers, for example with only the nDisplay nodes running, they are applied on every
running node directly.
"""
from __future__ import annotations

import csv
import re
from typing import Any, Iterable, List, Tuple

from operator_board.core.config import AppConfig, CameraPreset
from operator_board.remote import scripts
from operator_board.remote.rc_client import python_result_wrapper
from operator_board.remote.targets import TARGET_NDISPLAY, TARGET_PRIMARY, TargetResolver


def unique_name(name: str, taken: Iterable[str]) -> str:
    """``name`` if free, else the next free ``name_2``, ``name_3``... (never overwrite a saved position)."""
    taken = set(taken)
    if name not in taken:
        return name
    base = re.sub(r"_\d+$", "", name) or name
    number = 2
    while f"{base}_{number}" in taken:
        number += 1
    return f"{base}_{number}"


class CameraService:
    def __init__(self, config: AppConfig, targets: TargetResolver) -> None:
        self.config = config
        self.targets = targets

    @property
    def presets(self) -> List[CameraPreset]:
        return self.config.cameras

    def get(self, name: str) -> CameraPreset:
        preset = next((c for c in self.config.cameras if c.name == name), None)
        if preset is None:
            raise KeyError(f"No camera preset named '{name}'")
        return preset

    def _run_on_nodes(self, script: str, first_only: bool = False) -> Tuple[List[Any], List[str]]:
        results, errors = [], []
        for client in self.targets.resolve(TARGET_NDISPLAY):
            try:
                results.append(client.run_python(script))
                if first_only:
                    break
            except Exception as exc:  # noqa: BLE001 - a node that is not running
                errors.append(f"{client.host}:{client.port}: {exc}")
        return results, errors

    def _editor_or_nodes(self, script: str, first_only: bool = False) -> List[Any]:
        """Run ``script`` in the primary editor; if no editor answers, on the running nDisplay nodes."""
        try:
            return [self.targets.primary().run_python(script)]
        except Exception as exc:  # noqa: BLE001 - no editor configured, not running, or not ready
            editor_error = str(exc)
        results, errors = self._run_on_nodes(script, first_only)
        if not results:
            raise RuntimeError(f"No editor or nDisplay node answered (editor: {editor_error}"
                               + (f"; nodes: {'; '.join(errors)}" if errors else "; no nDisplay nodes set up") + ")")
        return results

    def recall(self, name: str, target: str = TARGET_PRIMARY) -> List[str]:
        """Move the preset's actor (or the editor viewport) to the saved position.

        Python, not a Remote Control function call: UE 5.8 blocks calls such as
        K2_SetActorLocationAndRotation unless the instance was started with the unlock switch."""
        preset = self.get(name)
        if preset.target == "viewport":
            try:
                self.targets.primary().run_python(
                    python_result_wrapper(scripts.set_viewport_camera(preset.location, preset.rotation)))
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"'{name}' is an editor viewport position, which needs the editor running: "
                                   f"{exc}") from exc
            return []
        script = python_result_wrapper(scripts.set_actor_transform(preset.target, preset.location, preset.rotation))
        if target == TARGET_PRIMARY:
            self._editor_or_nodes(script)
            return []
        errors = []
        for client in self.targets.resolve(target):
            try:
                client.run_python(script)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{client.host}: {exc}")
        return errors

    def capture(self, name: str, target_actor: str = "viewport") -> CameraPreset:
        """Save the viewport's or an actor's position as a new preset (``name_2``... if the name is taken).
        Without an editor, an actor's position is read from the first running nDisplay node."""
        if target_actor == "viewport":
            data = self.targets.primary().run_python(python_result_wrapper(scripts.get_viewport_camera()))
        else:
            data = self._editor_or_nodes(python_result_wrapper(scripts.get_actor_transform(target_actor)),
                                         first_only=True)[0]
        if not isinstance(data, dict) or "location" not in data:
            raise RuntimeError(f"Could not read the position of {target_actor}: {data}")
        preset = CameraPreset(name=unique_name(name, (c.name for c in self.config.cameras)), target=target_actor,
                              location=list(data["location"]), rotation=list(data["rotation"]))
        self.config.cameras.append(preset)
        return preset

    CSV_HEADER = ["name", "target", "x", "y", "z", "pitch", "yaw", "roll"]

    def export_csv(self, path: str) -> int:
        """Back up every saved position (name, target, location, rotation) to a CSV file."""
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(self.CSV_HEADER)
            for c in self.config.cameras:
                w.writerow([c.name, c.target] + [f"{v:.4f}" for v in list(c.location) + list(c.rotation)])
        return len(self.config.cameras)

    def import_csv(self, path: str) -> List[CameraPreset]:
        """Add the positions in a CSV file (as written by export_csv); a name in use gets _2, _3..."""
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        added = []
        for n, row in enumerate(rows, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("name"):
                continue
            try:
                loc = [float(row.get(k) or 0) for k in ("x", "y", "z")]
                rot = [float(row.get(k) or 0) for k in ("pitch", "yaw", "roll")]
            except ValueError as exc:
                raise ValueError(f"line {n}: {exc}") from exc
            preset = CameraPreset(name=unique_name(row["name"], (c.name for c in self.config.cameras)),
                                  target=row.get("target") or "viewport", location=loc, rotation=rot)
            self.config.cameras.append(preset)
            added.append(preset)
        return added

    def list_camera_actors(self) -> List[dict]:
        """Camera actors (cine cameras included) and nDisplay setups (their Blueprint classes, e.g. nDisplayConfig_C)."""
        result = self._editor_or_nodes(python_result_wrapper(scripts.list_camera_targets()), first_only=True)[0]
        return result.get("actors", []) if isinstance(result, dict) else []

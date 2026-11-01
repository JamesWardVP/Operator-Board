"""Instance-editable objects: Remote Control Presets, actor browsing and favourite properties."""
from __future__ import annotations

from typing import Any, Dict, List

from operator_board.core.config import AppConfig
from operator_board.remote import scripts
from operator_board.remote.rc_client import python_result_wrapper
from operator_board.remote.targets import TargetResolver


def flatten_preset(preset: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn a /remote/preset/<name> response into a flat list of exposed properties/functions."""
    rows: List[Dict[str, Any]] = []
    for group in preset.get("Groups", []) or []:
        gname = group.get("Name", "")
        for prop in group.get("ExposedProperties", []) or []:
            underlying = prop.get("UnderlyingProperty", {}) or {}
            rows.append({"kind": "property", "group": gname, "label": prop.get("DisplayName", ""),
                         "type": underlying.get("Type", ""), "id": prop.get("ID", ""),
                         "owners": [o.get("Path", "") for o in prop.get("OwnerObjects", []) or []]})
        for fn in group.get("ExposedFunctions", []) or []:
            rows.append({"kind": "function", "group": gname, "label": fn.get("DisplayName", ""),
                         "type": "function", "id": fn.get("ID", ""), "owners": []})
    return rows


def extract_value(response: Any) -> Any:
    """Pull the value out of a property GET response (shape differs between endpoints)."""
    if isinstance(response, dict):
        if "PropertyValues" in response and response["PropertyValues"]:
            return response["PropertyValues"][0].get("PropertyValue")
        if len(response) == 1:
            return next(iter(response.values()))
    return response


def _each(targets: TargetResolver, target: str, fn) -> List[str]:
    """Run ``fn`` on ``target``; errors per instance. "primary" falls back to the running nDisplay
    nodes (all of them) when no editor is running."""
    errors = []
    if target == "primary":
        try:
            targets.editor_or_nodes(fn, all_nodes=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        return errors
    for client in targets.resolve(target):
        try:
            fn(client)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{client.host}: {exc}")
    return errors


class ObjectService:
    """Reads go to the primary editor, or with no editor running to the first nDisplay node that
    answers; quick-control changes then go to every running node."""

    def __init__(self, config: AppConfig, targets: TargetResolver) -> None:
        self.config = config
        self.targets = targets

    def _read(self, fn):
        return self.targets.editor_or_nodes(fn)[0]

    def presets(self, target: str = "primary") -> List[str]:
        if target == "primary":
            return self._read(lambda c: [p.get("Name", "") for p in c.presets()])
        return [p.get("Name", "") for p in self.targets.resolve(target)[0].presets()]

    def preset_rows(self, preset: str, target: str = "primary") -> List[Dict[str, Any]]:
        if target == "primary":
            return flatten_preset(self._read(lambda c: c.preset(preset)))
        return flatten_preset(self.targets.resolve(target)[0].preset(preset))

    def get_preset_value(self, preset: str, label: str, target: str = "primary") -> Any:
        if target == "primary":
            return extract_value(self._read(lambda c: c.get_preset_property(preset, label)))
        return extract_value(self.targets.resolve(target)[0].get_preset_property(preset, label))

    def set_preset_value(self, preset: str, label: str, value: Any, target: str = "all") -> List[str]:
        return _each(self.targets, target, lambda c: c.set_preset_property(preset, label, value))

    def call_preset_function(self, preset: str, label: str, target: str = "all") -> List[str]:
        return _each(self.targets, target, lambda c: c.call_preset_function(preset, label))

    def actors(self, class_filter: str = "") -> List[Dict[str, str]]:
        result = self._read(lambda c: c.run_python(python_result_wrapper(scripts.list_actors(class_filter))))
        return list(result.get("actors", [])) if isinstance(result, dict) else []

    def quick_controls(self, actor_path: str) -> Dict[str, Any]:
        """Light / cine camera controls with current values (empty ``kind`` for other actors)."""
        result = self._read(lambda c: c.run_python(python_result_wrapper(scripts.actor_quick_controls(actor_path))))
        return result if isinstance(result, dict) else {"kind": "", "controls": []}

    def set_quick_control(self, actor_path: str, control: str, value: Any) -> Dict[str, Any]:
        script = python_result_wrapper(scripts.set_actor_quick_control(actor_path, control, value))
        results = self.targets.editor_or_nodes(lambda c: c.run_python(script), all_nodes=True)
        return results[0] if results and isinstance(results[0], dict) else {}

    def property_values(self, object_path: str) -> Dict[str, Any]:
        """Every readable property's current value in one request (empty if Unreal refuses)."""
        try:
            result = self._read(lambda c: c.request("PUT", "/remote/object/property", {
                "objectPath": object_path, "access": "READ_ACCESS"}))
        except Exception:  # noqa: BLE001 - shown as blank values; Read still works per property
            return {}
        return result if isinstance(result, dict) else {}

    def describe(self, object_path: str) -> Dict[str, Any]:
        return self._read(lambda c: c.describe(object_path)) or {}

    def get_property(self, object_path: str, prop: str) -> Any:
        return extract_value(self._read(lambda c: c.get_property(object_path, prop)))

    def set_property(self, object_path: str, prop: str, value: Any, target: str = "all") -> List[str]:
        return _each(self.targets, target, lambda c: c.set_property(object_path, prop, value))

"""Projects, levels, sub-levels and data layers."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import List, Optional

from operator_board.core.config import AppConfig
from operator_board.remote import scripts
from operator_board.remote.rc_client import RemoteControlClient, python_result_wrapper
from operator_board.remote.targets import TargetResolver


@dataclass
class LevelInfo:
    name: str
    path: str  # long package name, e.g. /Game/Maps/Stage


def discover_projects(search_dirs: List[str], max_depth: int = 3) -> List[str]:
    """Find .uproject files below the given directories."""
    found: List[str] = []
    for root in search_dirs:
        if not root or not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in ("Intermediate", "Saved", "Binaries", "DerivedDataCache", ".git")]
            found += [os.path.join(dirpath, f) for f in filenames if f.lower().endswith(".uproject")]
    return sorted(found)


def scan_maps(uproject_path: str) -> List[LevelInfo]:
    """List .umap files in a project's Content folder (works without Unreal running)."""
    content = os.path.join(os.path.dirname(uproject_path), "Content")
    maps: List[LevelInfo] = []
    for path in glob.glob(os.path.join(content, "**", "*.umap"), recursive=True):
        rel = os.path.relpath(path, content)[: -len(".umap")].replace(os.sep, "/")
        if "__ExternalActors__" in rel or "__ExternalObjects__" in rel:
            continue
        maps.append(LevelInfo(name=os.path.basename(rel), path=f"/Game/{rel}"))
    return sorted(maps, key=lambda m: m.path.lower())


class LevelService:
    def __init__(self, config: AppConfig, targets: TargetResolver) -> None:
        self.config = config
        self.targets = targets

    def _py(self, client: RemoteControlClient, body: str):
        result = client.run_python(python_result_wrapper(body))
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(result["error"])
        return result

    def list_levels(self) -> List[LevelInfo]:
        project = self.config.project()
        if project.uproject_path and os.path.exists(project.uproject_path):
            return scan_maps(project.uproject_path)
        assets = self.targets.primary().search_assets(class_names=["World"])
        out = []
        for a in assets:
            path = str(a.get("Path", "")).split(".")[0]
            if path:
                out.append(LevelInfo(name=str(a.get("Name") or path.split("/")[-1]), path=path))
        return sorted(out, key=lambda m: m.path.lower())

    def current_level(self) -> Optional[str]:
        result = self._py(self.targets.primary(), scripts.current_level())
        return result.get("path") if isinstance(result, dict) else None

    def load_in_editor(self, level_path: str) -> None:
        self._py(self.targets.primary(), scripts.load_level(level_path))

    def open_on_nodes(self, level_path: str) -> List[str]:
        """Runtime level change on -game / nDisplay nodes via the 'open' console command."""
        errors = []
        for client in self.targets.resolve("ndisplay"):
            try:
                client.console_command(f"open {level_path}")
            except Exception as exc:
                errors.append(f"{client.host}: {exc}")
        return errors

    # ---- sub-levels -------------------------------------------------------------
    def sublevels(self) -> List[dict]:
        result = self._py(self.targets.primary(), scripts.list_sublevels())
        return list(result.get("levels", [])) if isinstance(result, dict) else []

    def set_sublevel_visible(self, package: str, visible: bool, include_nodes: bool = True) -> List[str]:
        errors: List[str] = []
        try:
            self._py(self.targets.primary(), scripts.set_sublevel_visibility(package, visible))
        except Exception as exc:
            errors.append(f"editor: {exc}")
        if include_nodes:
            errors += self._runtime_streaming(package, visible)
        return errors

    def _runtime_streaming(self, package: str, visible: bool) -> List[str]:
        """Toggle a streaming level on render nodes via GameplayStatics.GetStreamingLevel."""
        persistent = self.config.project().default_map
        if not persistent:
            return []
        world_ctx = f"{persistent}.{persistent.split('/')[-1]}:PersistentLevel.WorldSettings"
        errors = []
        for client in self.targets.resolve("ndisplay"):
            try:
                res = client.call("/Script/Engine.Default__GameplayStatics", "GetStreamingLevel",
                                  {"WorldContextObject": world_ctx, "PackageName": package.split("/")[-1]})
                streaming = (res or {}).get("ReturnValue") if isinstance(res, dict) else None
                if not streaming:
                    errors.append(f"{client.host}: streaming level {package} not found")
                    continue
                client.call(streaming, "SetShouldBeLoaded", {"bInShouldBeLoaded": True})
                client.call(streaming, "SetShouldBeVisible", {"bInShouldBeVisible": visible})
            except Exception as exc:
                errors.append(f"{client.host}: {exc}")
        return errors

    def data_layers(self) -> List[dict]:
        result = self._py(self.targets.primary(), scripts.list_data_layers())
        return list(result.get("layers", [])) if isinstance(result, dict) else []

    def set_data_layer_visible(self, path: str, visible: bool) -> None:
        self._py(self.targets.primary(), scripts.set_data_layer_visibility(path, visible))

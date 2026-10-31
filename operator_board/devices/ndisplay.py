"""nDisplay config handling for launching render nodes, as Switchboard does.

nDisplay nodes need a ``.ndisplay`` (JSON) cluster config on the node machine. A
project usually holds it as an nDisplay Config *asset* (``.uasset``); the JSON is
stored in that asset's ``ConfigExport`` asset-registry tag. Switchboard extracts it,
applies local overrides (each node's host = the device's address, only the nodes
being used, the chosen primary node), sends it to the node through the listener
(``send file`` to ``%TEMP%/ndisplay/%RANDOM%.ndisplay``) and passes the path it was
written to as ``-dc_cfg``. Passing the ``.uasset`` itself makes the node exit at start.

The ConfigExport tag is an Unreal FString: an int32 length (including the
terminator; negative means UTF-16) followed by the text. It is found by looking
for the JSON's ``"nDisplay"`` key, so no full .uasset parser is needed and the
layout differences between engine versions do not matter.
"""
from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

CONFIG_DESTINATION = "%TEMP%/ndisplay/%RANDOM%.ndisplay"


@dataclass
class NodeWindow:
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    fullscreen: bool = False
    headless: bool = False


def _fstrings_around(data: bytes, marker: bytes, brace: bytes, encoding: str) -> List[str]:
    out = []
    pos = data.find(marker)
    while pos >= 0:
        start = data.rfind(brace, 0, pos)
        if start >= 4:
            length = struct.unpack("<i", data[start - 4:start])[0]
            size = (length - 1) if length > 0 else (-length - 1) * 2
            if 0 < size <= len(data) - start:
                try:
                    out.append(data[start:start + size].decode(encoding))
                except UnicodeDecodeError:
                    pass
        pos = data.find(marker, pos + 1)
    return out


def extract_config_json(path: str) -> str:
    """The nDisplay cluster config JSON from a .ndisplay file or an nDisplay Config .uasset."""
    if os.path.splitext(path)[1].lower() != ".uasset":
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    with open(path, "rb") as fh:
        data = fh.read()
    candidates = (_fstrings_around(data, b'"nDisplay"', b"{", "utf-8")
                  + _fstrings_around(data, '"nDisplay"'.encode("utf-16-le"), "{".encode("utf-16-le"), "utf-16-le"))
    for text in candidates:
        try:
            cfg = json.loads(text)
        except ValueError:
            continue
        if isinstance(cfg, dict) and "cluster" in cfg.get("nDisplay", {}):
            return text
    raise ValueError(f"{os.path.basename(path)} does not contain an nDisplay cluster config "
                     "(is it an nDisplay Config asset saved in the editor?)")


def _primary_key(cfg: Dict[str, Any]) -> str:
    try:
        version = float(cfg["nDisplay"].get("version", 5))
    except (TypeError, ValueError):
        version = 5.0
    return "masterNode" if version < 5.0 else "primaryNode"


def node_names(config_json: str) -> List[str]:
    return list(json.loads(config_json)["nDisplay"]["cluster"]["nodes"])


def node_window(config_json: str, node: str) -> Optional[NodeWindow]:
    cnode = json.loads(config_json)["nDisplay"]["cluster"]["nodes"].get(node)
    if cnode is None:
        return None
    win = cnode.get("window", {}) or {}
    return NodeWindow(int(win.get("x", 0)), int(win.get("y", 0)), int(win.get("w", 0)), int(win.get("h", 0)),
                      bool(cnode.get("fullScreen", False)), bool(cnode.get("renderHeadless", False)))


def apply_local_overrides(config_json: str, nodes: Dict[str, str], primary: str = "") -> str:
    """Switchboard's local overrides: keep only ``nodes`` (node name -> device address),
    set each node's host to its device address, and make ``primary`` (or the config's
    primary if it is kept, else the first kept node) the primary node."""
    cfg = json.loads(config_json)
    cluster = cfg["nDisplay"]["cluster"]
    key = _primary_key(cfg)
    all_nodes = cluster.get("nodes", {})
    active = {}
    for name, address in nodes.items():
        node = all_nodes.get(name)
        if node is None:
            continue
        node["host"] = address
        active[name] = node
    if active:
        cluster["nodes"] = active
        current = cluster.get(key, {}).get("id", "")
        chosen = primary if primary in active else current if current in active else next(iter(active))
        cluster.setdefault(key, {})["id"] = chosen
    return json.dumps(cfg)


def missing_nodes(config_json: str, names: Sequence[str]) -> List[str]:
    known = set(node_names(config_json))
    return [n for n in names if n not in known]

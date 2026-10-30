"""File sync / revision control providers (FreeFileSync, Perforce, Git, Robocopy, custom).

Jobs run either locally or on render nodes through their SwitchboardListener, so
every machine on the stage can be brought up to date from the operator seat.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple


@dataclass
class ProviderField:
    name: str
    label: str
    default: str = ""
    kind: str = "str"  # str | path | file


@dataclass
class Provider:
    id: str
    label: str
    fields: List[ProviderField]
    build: Callable[[Dict[str, Any]], Tuple[str, List[str]]]
    interpret: Callable[[int], Tuple[bool, str]]


def _require(params: Dict[str, Any], *names: str) -> None:
    missing = [n for n in names if not str(params.get(n, "")).strip()]
    if missing:
        raise ValueError(f"Missing required setting(s): {', '.join(missing)}")


def _ffs_build(p):
    _require(p, "batch_file")
    return p.get("exe") or "FreeFileSync", [p["batch_file"]]


def _ffs_interpret(code):
    return {0: (True, "Synchronization completed successfully"),
            1: (True, "Synchronization completed with warnings"),
            2: (False, "Synchronization completed with errors"),
            3: (False, "Synchronization was aborted")}.get(code, (False, f"Unexpected exit code {code}"))


def _p4_build(p):
    _require(p, "depot_path")
    args = []
    for flag, key in (("-p", "port"), ("-u", "user"), ("-c", "client")):
        if p.get(key):
            args += [flag, str(p[key])]
    args.append("sync")
    if str(p.get("force", "")).lower() in ("1", "true", "yes"):
        args.append("-f")
    args.append(p["depot_path"])
    return p.get("exe") or "p4", args


def _git_build(p):
    _require(p, "repo_dir")
    args = ["-C", p["repo_dir"], "pull"]
    if p.get("remote"):
        args.append(p["remote"])
        if p.get("branch"):
            args.append(p["branch"])
    return p.get("exe") or "git", args


def _robocopy_build(p):
    _require(p, "source", "destination")
    flags = str(p.get("flags") or "/MIR /R:1 /W:1 /NP").split()
    return "robocopy", [p["source"], p["destination"]] + flags


def _robocopy_interpret(code):
    return (code < 8, "Copy completed" if code < 8 else f"Robocopy reported failures (code {code})")


def _custom_build(p):
    _require(p, "command")
    import shlex
    parts = shlex.split(p["command"], posix=False)
    return parts[0], parts[1:]


def _zero_ok(code):
    return (code == 0, "Completed" if code == 0 else f"Failed with exit code {code}")


PROVIDERS: Dict[str, Provider] = {p.id: p for p in [
    Provider("freefilesync", "FreeFileSync (batch job)",
             [ProviderField("exe", "FreeFileSync executable", r"C:\Program Files\FreeFileSync\FreeFileSync.exe", "file"),
              ProviderField("batch_file", "Batch job (.ffs_batch)", "", "file")], _ffs_build, _ffs_interpret),
    Provider("perforce", "Perforce (p4 sync)",
             [ProviderField("exe", "p4 executable", "p4", "file"), ProviderField("port", "P4PORT"),
              ProviderField("user", "P4USER"), ProviderField("client", "Workspace (P4CLIENT)"),
              ProviderField("depot_path", "Depot path", "//depot/Project/...#head"),
              ProviderField("force", "Force sync (true/false)", "false")], _p4_build, _zero_ok),
    Provider("git", "Git (pull)",
             [ProviderField("exe", "git executable", "git", "file"), ProviderField("repo_dir", "Repository folder", "", "path"),
              ProviderField("remote", "Remote", "origin"), ProviderField("branch", "Branch", "")], _git_build, _zero_ok),
    Provider("robocopy", "Robocopy mirror (Windows)",
             [ProviderField("source", "Source folder", "", "path"), ProviderField("destination", "Destination folder", "", "path"),
              ProviderField("flags", "Flags", "/MIR /R:1 /W:1 /NP")], _robocopy_build, _robocopy_interpret),
    Provider("custom", "Custom command", [ProviderField("command", "Command line")], _custom_build, _zero_ok),
]}

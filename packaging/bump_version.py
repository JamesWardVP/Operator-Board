"""Set Operator Board's version everywhere it appears, before a release.

    python packaging/bump_version.py 0.2.4

The version in operator_board/__init__.py names the build output (OperatorBoard-Setup-<version>.exe
and the zip), sets the installer's version, and must match the release tag (v<version>): the GitHub
Actions release build refuses a tag that does not match.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# file -> (pattern with the version as group 2, replacement template)
PLACES = {
    "operator_board/__init__.py": (r'(__version__ = ")([0-9.]+)(")', r"\g<1>{v}\g<3>"),
    "README.md": (r"(\*\*Version:\*\* )([0-9.]+)( )", r"\g<1>{v}\g<3>"),
    "CLAUDE.md": (r"(## Current state \(v)([0-9.]+)(\))", r"\g<1>{v}\g<3>"),
}


def current_version() -> str:
    with open(os.path.join(ROOT, "operator_board", "__init__.py"), encoding="utf-8") as fh:
        return re.search(r'__version__ = "([0-9.]+)"', fh.read()).group(1)


def as_tuple(version: str):
    return tuple(int(p) for p in version.split("."))


def main(argv) -> int:
    if len(argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", argv[1]):
        print(__doc__)
        return 2
    new, old = argv[1], current_version()
    if as_tuple(new) <= as_tuple(old):
        print(f"{new} is not newer than the current version {old}")
        return 1
    for rel, (pattern, template) in PLACES.items():
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        updated, count = re.subn(pattern, template.format(v=new), text, count=1)
        if count != 1:
            print(f"Could not find the version in {rel}")
            return 1
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
        print(f"{rel}: {old} -> {new}")
    print(f"Done. Commit, then tag v{new} after the pull request is merged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

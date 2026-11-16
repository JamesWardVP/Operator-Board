"""Build the button picture icon library (operator_board/ui/icon_library.json) from Tabler Icons.

Tabler Icons (https://github.com/tabler/tabler-icons, MIT licence) are single-colour outline icons.
Get the package and point this script at the unpacked folder:

    npm pack @tabler/icons
    tar -xzf tabler-icons-<version>.tgz
    python packaging/make_icon_library.py path/to/package

The library keeps each icon's drawing (the inside of its 24 x 24 SVG), its category and its search
words in one file, so the app ships one small file instead of thousands.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "operator_board", "ui", "icon_library.json")
SKIP_CATEGORIES = ("Brand",)  # company logos are other people's trade marks
BACKDROP = re.compile(r'<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*/>')


def main(package: str) -> None:
    with open(os.path.join(package, "icons.json"), "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    with open(os.path.join(package, "package.json"), "r", encoding="utf-8") as fh:
        version = json.load(fh)["version"]
    icons = {}
    for name in sorted(meta):
        path = os.path.join(package, "icons", "outline", name + ".svg")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            svg = fh.read()
        body = svg[svg.index(">") + 1:svg.rindex("</svg>")]
        body = " ".join(BACKDROP.sub("", body).split())
        info = meta[name]
        if info.get("category") in SKIP_CATEGORIES:
            continue
        tags = " ".join(str(t) for t in info.get("tags", []) if str(t).strip())
        icons[name] = [info.get("category") or "Other", tags, body]
    data = {"source": "Tabler Icons", "version": version, "licence": "MIT", "icons": icons}
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(icons)} icons from Tabler Icons {version} -> {os.path.normpath(OUT)} "
          f"({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])

"""Build the Windows release: dist/OperatorBoard/ and dist/OperatorBoard-<version>-win64.zip.

    python -m pip install -e .[ui,build]       (add streamdeck pillow for Stream Deck support)
    python packaging/build.py [--hidapi]

--hidapi downloads hidapi.dll (libusb/hidapi release), which the Stream Deck library
needs on Windows, and bundles it next to the executables.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
HIDAPI_URL = "https://github.com/libusb/hidapi/releases/download/hidapi-0.14.0/hidapi-win.zip"

sys.path.insert(0, ROOT)
from operator_board import __version__  # noqa: E402

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0), prodvers=({v}, 0), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'JamesWardVP'),
      StringStruct('FileDescription', 'Operator Board for Unreal Engine Switchboard'),
      StringStruct('FileVersion', '{s}'),
      StringStruct('ProductName', 'Operator Board'),
      StringStruct('ProductVersion', '{s}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""

QUICK_START = """Operator Board {version}
=====================

Double-click OperatorBoard.exe to start. Python does not need to be installed.

  OperatorBoard.exe          the operator interface
  OperatorBoardConsole.exe   the same program with a console window, for example:
                               OperatorBoardConsole.exe --headless
                               OperatorBoardConsole.exe --switchboard-config "C:\\path\\Stage.json"
                               OperatorBoardConsole.exe --help

Switchboard does not need to be open. Each render node needs SwitchboardListener
running (Engine\\Binaries\\Win64\\SwitchboardListener.exe), as it does for Switchboard.

Settings: %USERPROFILE%\\.operator_board\\   Log: %USERPROFILE%\\.operator_board\\logs\\
Documentation: https://github.com/JamesWardVP/Operator-Board
"""


def write_version_info() -> str:
    parts = [int(p) for p in __version__.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    path = os.path.join(HERE, "version_info.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(VERSION_INFO.format(v=", ".join(str(p) for p in parts), s=__version__))
    return path


def fetch_hidapi() -> None:
    target = os.path.join(HERE, "hidapi.dll")
    if os.path.exists(target):
        return
    print(f"Downloading {HIDAPI_URL}")
    with urllib.request.urlopen(HIDAPI_URL, timeout=60) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith("x64/hidapi.dll"))
        with zf.open(name) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hidapi", action="store_true", help="bundle hidapi.dll for Stream Deck support")
    args = parser.parse_args()
    if args.hidapi:
        fetch_hidapi()
    write_version_info()
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                           "--distpath", DIST, "--workpath", os.path.join(ROOT, "build"),
                           os.path.join(HERE, "operator_board.spec")])
    out = os.path.join(DIST, "OperatorBoard")
    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write(QUICK_START.format(version=__version__))
    shutil.copytree(os.path.join(ROOT, "config"), os.path.join(out, "examples"), dirs_exist_ok=True)
    archive = shutil.make_archive(os.path.join(DIST, f"OperatorBoard-{__version__}-win64"), "zip", DIST,
                                  "OperatorBoard")
    print(f"Built {out}\nArchive {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

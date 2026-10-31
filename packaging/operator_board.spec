# PyInstaller spec: builds dist/OperatorBoard/ with two executables sharing one runtime.
#   OperatorBoard.exe         the operator UI (no console window)
#   OperatorBoardConsole.exe  same program with a console, for --headless and troubleshooting
# Build with: python packaging/build.py
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(HERE)
ICON = os.path.join(HERE, "operator_board.ico")

datas = collect_data_files("operator_board", includes=["**/*.json", "**/*.png", "**/*.svg"])
hidden = collect_submodules("aioquic") + ["pylsqpack"]  # UE 5.4+ listener connections (QUIC)
try:  # optional Stream Deck support
    import StreamDeck  # noqa: F401
    hidden += ["StreamDeck", "PIL"]
except ImportError:
    pass
try:  # optional MIDI controller support (mido loads its backend by name, so list it)
    import mido  # noqa: F401
    import rtmidi  # noqa: F401
    hidden += collect_submodules("mido") + ["rtmidi"]
except ImportError:
    pass
binaries = []
hidapi = os.path.join(HERE, "hidapi.dll")  # downloaded by build.py; needed by the Stream Deck library
if os.path.exists(hidapi):
    binaries.append((hidapi, "."))

a = Analysis(
    [os.path.join(HERE, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["PySide2", "tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf"],
    noarchive=False,
)
# Drop parts of Qt / Pillow a widgets app never loads (about 45 MB).
_DROP = ("opengl32sw.dll", "qt6quick", "qt6qml", "qt6pdf", "qpdf.dll", "_avif", "pyside6\translations",
         "pyside6/translations")


def _keep(entry):
    name = entry[0].replace("/", "\\").lower()
    return not any(d.replace("/", "\\") in name for d in _DROP)


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)

def exe(name, console):
    return EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, icon=ICON,
               upx=False, version=os.path.join(HERE, "version_info.txt") if os.path.exists(
                   os.path.join(HERE, "version_info.txt")) else None)

gui = exe("OperatorBoard", console=False)
cli = exe("OperatorBoardConsole", console=True)

coll = COLLECT(gui, cli, a.binaries, a.datas, upx=False, name="OperatorBoard")

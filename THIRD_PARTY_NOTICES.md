# Third-party software

Operator Board's own code is under the [MIT License](LICENSE). The Windows release
(`OperatorBoard-<version>-win64.zip`) also bundles the open-source components below,
each under its own license. They are shipped as separate files (in the `_internal`
folder), so they can be replaced with other compatible builds.

| Component | Used for | License | Source |
|---|---|---|---|
| Python | Runtime | Python Software Foundation License 2.0 | https://www.python.org |
| Qt 6 / PySide6 (Qt for Python) | User interface | LGPL-3.0 (also GPL-2.0 / GPL-3.0) | https://www.qt.io/qt-for-python · https://code.qt.io/cgit/pyside/pyside-setup.git |
| Pillow | Stream Deck key images | MIT-CMU (HPND) | https://python-pillow.org |
| python-elgato-streamdeck | Stream Deck hardware | MIT | https://github.com/abcminiuser/python-elgato-streamdeck |
| HIDAPI (`hidapi.dll`) | Stream Deck USB access | BSD-3-Clause (one of its three license options) | https://github.com/libusb/hidapi |
| PyInstaller bootloader | `OperatorBoard.exe` launcher | GPL-2.0-or-later with the PyInstaller exception, which allows distributing programs built with it | https://pyinstaller.org |

Running from source uses the same libraries, installed with `pip`.

Unreal Engine, Switchboard and SwitchboardListener are not included. They belong to
Epic Games and are used only through their documented network interfaces.

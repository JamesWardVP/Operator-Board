# Third-party software

Operator Board's own code is under the [MIT License](LICENSE). The Windows release
(`OperatorBoard-<version>-win64.zip`) also bundles the open-source components below,
each under its own license. They are shipped as separate files (in the `_internal`
folder), so they can be replaced with other compatible builds.

| Component | Used for | License | Source |
|---|---|---|---|
| Python | Runtime | Python Software Foundation License 2.0 | https://www.python.org |
| Qt 6 / PySide6 (Qt for Python) | User interface | LGPL-3.0 (also GPL-2.0 / GPL-3.0) | https://www.qt.io/qt-for-python · https://code.qt.io/cgit/pyside/pyside-setup.git |
| aioquic | QUIC connection to UE 5.4+ SwitchboardListeners | BSD-3-Clause | https://github.com/aiortc/aioquic |
| pylsqpack (with ls-qpack) | Used by aioquic | BSD-3-Clause (ls-qpack: MIT) | https://github.com/aiortc/pylsqpack |
| cryptography (with OpenSSL) | TLS for QUIC | Apache-2.0 OR BSD-3-Clause (OpenSSL: Apache-2.0) | https://github.com/pyca/cryptography |
| pyOpenSSL | Used by aioquic | Apache-2.0 | https://github.com/pyca/pyopenssl |
| service-identity, attrs | Used by aioquic | MIT | https://github.com/pyca/service-identity · https://github.com/python-attrs/attrs |
| cffi, pycparser | Used by cryptography | MIT-0 · BSD-3-Clause | https://github.com/python-cffi/cffi · https://github.com/eliben/pycparser |
| certifi | Used by aioquic | MPL-2.0 | https://github.com/certifi/python-certifi |
| typing_extensions | Used by pyOpenSSL | PSF-2.0 | https://github.com/python/typing_extensions |
| Pillow | Stream Deck key images | MIT-CMU (HPND) | https://python-pillow.org |
| python-elgato-streamdeck | Stream Deck hardware | MIT | https://github.com/abcminiuser/python-elgato-streamdeck |
| HIDAPI (`hidapi.dll`) | Stream Deck USB access | BSD-3-Clause (one of its three license options) | https://github.com/libusb/hidapi |
| mido | MIDI controller support | MIT | https://github.com/mido/mido |
| python-rtmidi (with RtMidi) | MIDI input/output for mido | MIT (RtMidi: MIT-style) | https://github.com/SpotlightKid/python-rtmidi |
| Inno Setup | The setup program (`OperatorBoard-Setup-<version>.exe`) and its uninstaller | Inno Setup License (permissive; copyright Jordan Russell and Martijn Laan) | https://jrsoftware.org/isinfo.php |
| PyInstaller bootloader | `OperatorBoard.exe` launcher | GPL-2.0-or-later with the PyInstaller exception, which allows distributing programs built with it | https://pyinstaller.org |

Running from source uses the same libraries, installed with `pip`.

Unreal Engine, Switchboard and SwitchboardListener are not included. They belong to
Epic Games and are used only through their documented network interfaces.

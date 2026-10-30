"""Qt binding shim: PySide6 preferred, PySide2 fallback (Switchboard in UE 5.2/5.3 ships PySide2)."""
from __future__ import annotations

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
    from PySide6.QtCore import Qt, Signal, Slot  # noqa: F401
    from PySide6.QtGui import QAction, QShortcut  # noqa: F401
    BINDING = "PySide6"
except ImportError:  # pragma: no cover
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
    from PySide2.QtCore import Qt, Signal, Slot  # noqa: F401
    from PySide2.QtWidgets import QAction, QShortcut  # noqa: F401
    BINDING = "PySide2"


def exec_(obj):
    """QApplication/QDialog exec across bindings."""
    return obj.exec() if hasattr(obj, "exec") else obj.exec_()

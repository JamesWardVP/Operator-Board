"""Draw the Operator Board icon (packaging/operator_board.ico and operator_board/ui/icon.png).

Run once after changing the design: python packaging/make_icon.py
"""
import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def draw(size: int) -> QtGui.QImage:
    img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    r = size * 0.18
    p.setBrush(QtGui.QColor("#1b1e23"))
    p.setPen(QtCore.Qt.NoPen)
    p.drawRoundedRect(QtCore.QRectF(0, 0, size, size), r, r)
    # 2x2 grid of stage keys: freeze (blue), blackout (red), pattern (amber), go (green)
    colors = ["#1fa2ff", "#e03131", "#fab005", "#2f9e44"]
    pad, gap = size * 0.16, size * 0.08
    key = (size - 2 * pad - gap) / 2
    for i, c in enumerate(colors):
        x = pad + (i % 2) * (key + gap)
        y = pad + (i // 2) * (key + gap)
        p.setBrush(QtGui.QColor(c))
        p.drawRoundedRect(QtCore.QRectF(x, y, key, key), key * 0.2, key * 0.2)
    p.end()
    return img


def main() -> int:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    draw(256).save(os.path.join(ROOT, "operator_board", "ui", "icon.png"))
    ok = draw(256).save(os.path.join(HERE, "operator_board.ico"), "ICO")
    print("icon written" if ok else "ICO writer unavailable")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

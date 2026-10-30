"""High-contrast dark theme sized for stage use (large targets, readable at a distance)."""
import os

# Qt style sheets need an absolute path (forward slashes) for images.
_CHECK_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check.svg").replace("\\", "/")

COLORS = {
    "bg": "#121417", "panel": "#1b1e23", "panel2": "#23272e", "border": "#30353d", "text": "#e9ecef",
    "muted": "#8b949e", "accent": "#3d8bfd", "ok": "#2f9e44", "warn": "#f08c00", "err": "#e03131",
    "freeze": "#1fa2ff", "blackout": "#e03131", "pattern": "#fab005", "mu": "#7950f2",
}

STATE_COLORS = {
    "disconnected": "#495057", "connecting": "#f08c00", "connected": "#3d8bfd", "starting": "#f08c00",
    "running": "#2f9e44", "stopping": "#f08c00", "error": "#e03131",
}

STYLESHEET = """
QWidget { background: %(bg)s; color: %(text)s; font-size: 14px; }
QFrame#Card { background: %(panel)s; border: 1px solid %(border)s; border-radius: 10px; }
QLabel#CardTitle { font-size: 13px; font-weight: 700; color: %(muted)s; letter-spacing: 1px; background: transparent; }
QLabel#Big { font-size: 26px; font-weight: 700; background: transparent; }
QLabel#Muted { color: %(muted)s; background: transparent; }
QLabel { background: transparent; }
QListWidget#Nav { background: %(panel)s; border: none; font-size: 15px; padding-top: 8px; outline: 0; }
QListWidget#Nav::item { padding: 12px 14px; border-radius: 6px; margin: 2px 6px; }
QListWidget#Nav::item:selected { background: %(accent)s; color: white; }
QPushButton { background: %(panel2)s; border: 1px solid %(border)s; border-radius: 8px; padding: 8px 14px; }
QPushButton:hover { border-color: %(accent)s; }
QPushButton:pressed { background: %(border)s; }
QPushButton:disabled { color: %(muted)s; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
  background: %(panel2)s; border: 1px solid %(border)s; border-radius: 6px; padding: 4px 6px; min-height: 24px; }
QTableWidget, QTreeWidget, QListWidget { background: %(panel)s; border: 1px solid %(border)s; gridline-color: %(border)s;
  alternate-background-color: %(panel2)s; selection-background-color: %(accent)s; selection-color: white; }
QHeaderView::section { background: %(panel2)s; border: none; padding: 6px; font-weight: 600; }
QTabWidget::pane { border: 1px solid %(border)s; border-radius: 6px; }
QTabBar::tab { background: %(panel)s; padding: 8px 16px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background: %(accent)s; color: white; }
QStatusBar { background: %(panel)s; color: %(muted)s; }
QCheckBox { background: transparent; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid %(muted)s; border-radius: 4px; background: %(panel2)s; }
QCheckBox::indicator:hover { border-color: %(accent)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(check)s); }
QCheckBox::indicator:disabled { border-color: %(border)s; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:horizontal { background: %(border)s; border-radius: 4px; min-width: 30px; }
QScrollBar::handle:hover { background: %(muted)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QGroupBox { background: %(panel)s; border: 1px solid %(border)s; border-radius: 10px; margin-top: 22px;
  padding: 12px 8px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 4px; padding: 0 4px;
  color: %(muted)s; font-size: 13px; font-weight: 700; letter-spacing: 1px; }
QDialog { background: %(bg)s; }
QStatusBar::item { border: none; }
""" % dict(COLORS, check=_CHECK_ICON)

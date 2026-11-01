"""Operator Board entry point."""
from __future__ import annotations

import argparse
import logging
import os
import sys

from operator_board import __version__


def _setup_logging(verbose: bool) -> None:
    handlers = [logging.StreamHandler()] if sys.stderr is not None else []
    if getattr(sys, "frozen", False):
        # The packaged OperatorBoard.exe has no console, so keep a log file as well.
        from operator_board.core.config import app_home
        log_dir = os.path.join(app_home(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(os.path.join(log_dir, "operator_board.log"), encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("quic").setLevel(logging.WARNING)  # aioquic logs every connection at INFO


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="operator-board", description="Operator Board for Unreal Engine Switchboard")
    parser.add_argument("--config", help="Path to a configuration file (default ~/.operator_board/config.json)")
    parser.add_argument("--project", metavar="NAME",
                        help="Open this Operator Board project straight away (needed with --headless unless one "
                             "was opened before)")
    parser.add_argument("--switchboard-config", metavar="PATH",
                        help="Open this Switchboard config (.json) as the stage")
    parser.add_argument("--no-hardware", action="store_true", help="Do not open Stream Deck hardware")
    parser.add_argument("--headless", action="store_true",
                        help="Run without UI: only the HTTP API and control surfaces (for a stage rack PC)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--version", action="version", version=f"Operator Board {__version__}")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    from operator_board.context import AppContext
    from operator_board.core import projects

    def open_context(project_id: str) -> "AppContext":
        projects.remember(project_id, args.config)
        ctx = AppContext(config_path=args.config, project_dir=projects.project_dir(project_id))
        if args.switchboard_config:
            try:
                ctx.open_switchboard_config(args.switchboard_config)
            except (OSError, ValueError, TypeError) as exc:
                logging.error("Could not open Switchboard config %s: %s", args.switchboard_config, exc)
        elif ctx.store.load_error:
            logging.warning(ctx.store.load_error)
        ctx.start_services(enable_hardware=not args.no_hardware)
        return ctx

    migrated = projects.migrate_existing_setup()  # settings from before projects: a project called "My stage"
    if migrated:
        logging.info("Made the project '%s' from the settings used before projects", migrated.name)

    if args.headless:
        import time
        project_id = _project_by_name(projects, args.project) if args.project else projects.last_project(args.config)
        if not project_id:
            available = ", ".join(p.name for p in projects.list_projects()) or "none"
            logging.error("No project to open: give --project NAME (projects: %s)", available)
            return 1
        ctx = open_context(project_id)
        logging.info("Operator Board headless: project %s, API on %s:%s", ctx.project.name if ctx.project else
                     project_id, ctx.config.surfaces.api_bind, ctx.api.port if ctx.api else "disabled")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            ctx.shutdown()
        return 0

    from operator_board.qt import QtGui, QtWidgets, exec_
    from operator_board.ui.main_window import MainWindow
    from operator_board.ui.start_screen import choose_project
    if sys.platform == "win32":
        # Our own taskbar identity, so Windows shows the window's icon (the logo) instead of an icon it
        # cached for the program or the Python interpreter.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JamesWardVP.OperatorBoard")
        except (AttributeError, OSError):
            pass
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Operator Board")
    app.setQuitOnLastWindowClosed(False)  # closing a project goes back to the start screen
    icon = QtGui.QIcon()
    for name in ("icon_small.png", "icon.png"):  # small sizes: the logo on a white tile, for readability
        icon.addFile(os.path.join(os.path.dirname(__file__), "ui", name))
    app.setWindowIcon(icon)
    from operator_board.ui.theme import STYLESHEET
    app.setStyleSheet(STYLESHEET)

    project_id = _project_by_name(projects, args.project) if args.project else ""
    while True:
        if not project_id:
            project_id = choose_project(current=projects.last_project(args.config))
            if not project_id:
                return 0
        ctx = open_context(project_id)
        window = MainWindow(ctx)
        window.show()
        exec_(app)
        if not window.switch_project_requested:
            return 0
        project_id = ""


def _project_by_name(projects, name: str) -> str:
    """A project's id from its name or id (any case)."""
    for p in projects.list_projects():
        if name in (p.id, p.name) or p.name.lower() == name.lower():
            return p.id
    return ""


if __name__ == "__main__":
    raise SystemExit(main())

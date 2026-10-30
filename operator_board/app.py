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
    ctx = AppContext(config_path=args.config)
    if args.switchboard_config:
        try:
            ctx.open_switchboard_config(args.switchboard_config)
        except (OSError, ValueError, TypeError) as exc:
            logging.error("Could not open Switchboard config %s: %s", args.switchboard_config, exc)
    elif ctx.store.load_error:
        logging.warning(ctx.store.load_error)
    ctx.start_services(enable_hardware=not args.no_hardware)

    if args.headless:
        import time
        logging.info("Operator Board headless: API on %s:%s", ctx.config.surfaces.api_bind,
                     ctx.api.port if ctx.api else "disabled")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            ctx.shutdown()
        return 0

    from operator_board.qt import QtGui, QtWidgets, exec_
    from operator_board.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Operator Board")
    app.setWindowIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), "ui", "icon.png")))
    window = MainWindow(ctx)
    window.show()
    return exec_(app)


if __name__ == "__main__":
    raise SystemExit(main())

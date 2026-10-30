"""Operator Board entry point."""
from __future__ import annotations

import argparse
import logging
import sys

from operator_board import __version__


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
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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

    from operator_board.qt import QtWidgets, exec_
    from operator_board.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Operator Board")
    window = MainWindow(ctx)
    window.show()
    return exec_(app)


if __name__ == "__main__":
    raise SystemExit(main())

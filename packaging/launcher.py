"""PyInstaller entry point for OperatorBoard.exe and OperatorBoardConsole.exe."""
import multiprocessing
import sys

from operator_board.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

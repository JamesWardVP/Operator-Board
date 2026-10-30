import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.operator_board during tests."""
    monkeypatch.setenv("OPERATOR_BOARD_HOME", str(tmp_path / "home"))
    return tmp_path / "home"

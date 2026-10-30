import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.operator_board during tests."""
    monkeypatch.setenv("OPERATOR_BOARD_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture(autouse=True)
def memory_credentials():
    """Never touch Windows Credential Manager (or the token file) during tests."""
    from operator_board.listener import credentials
    from operator_board.listener import client
    store = credentials.CredentialStore()
    store.encrypted_at_rest = True  # behave like the Windows store
    credentials.set_default_store(store)
    client._last_protocol.clear()
    yield store
    credentials.set_default_store(None)
    client._last_protocol.clear()

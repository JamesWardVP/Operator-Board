"""UE 5.4+ listener connection: QUIC, password login, saved credentials, key changes."""
import pytest

pytest.importorskip("aioquic")

from fakes import FakeQuicListener  # noqa: E402

from operator_board.core.config import AppConfig, DeviceConfig  # noqa: E402
from operator_board.core.events import EventBus  # noqa: E402
from operator_board.devices.manager import DeviceManager  # noqa: E402
from operator_board.listener import protocol  # noqa: E402
from operator_board.listener.client import ListenerClient, protocol_order, wait_until  # noqa: E402
from operator_board.listener.credentials import Credential, CredentialStore  # noqa: E402
from operator_board.listener.quic import FingerprintChanged, PasswordRejected, PasswordRequired  # noqa: E402


@pytest.fixture
def listener():
    fake = FakeQuicListener(password="secret")
    yield fake
    fake.close()


def _client(fake, store, **kw):
    return ListenerClient("127.0.0.1", fake.port, protocol_choice="quic", store=store, **kw)


def test_protocol_order_by_engine_version():
    assert protocol_order("auto", "5.2") == ["tcp", "quic"]
    assert protocol_order("auto", "5.3") == ["tcp", "quic"]
    assert protocol_order("auto", "5.4") == ["quic", "tcp"]
    assert protocol_order("auto", "5.8") == ["quic", "tcp"]
    assert protocol_order("tcp", "5.8") == ["tcp"]


def test_authenticate_message_matches_switchboard():
    import json
    _, payload = protocol.authenticate(password="pw")
    msg = json.loads(payload[:-1])
    assert msg["command"] == "authenticate" and msg["password"] == "pw" and msg["token"] == "pw"
    msg = json.loads(protocol.get_sync_status("p")[1][:-1])
    assert msg["request_flags"] == protocol.METRIC_REQUEST_FLAGS


def test_password_flow_saves_credential(listener, memory_credentials):
    store = memory_credentials
    with pytest.raises(PasswordRequired) as info:
        _client(listener, store).connect()
    assert info.value.fingerprint == listener.fingerprint
    assert info.value.subject == "FAKE-NODE"

    client = _client(listener, store)
    client.connect(password="secret")
    assert client.connected and client.transport == "quic"
    saved = store.get("127.0.0.1")
    assert saved.secret == "secret" and saved.fingerprint == listener.fingerprint
    assert wait_until(lambda: client.listener_version == "5.8.0", 3)
    client.disconnect("user disconnect")

    again = _client(listener, store)
    again.connect()  # uses the saved password
    assert again.connected
    again.disconnect("user disconnect")


def test_wrong_password_is_rejected_and_forgotten(listener, memory_credentials):
    store = memory_credentials
    with pytest.raises(PasswordRejected):
        _client(listener, store).connect(password="nope")
    assert store.get("127.0.0.1") is None
    store.set("127.0.0.1", Credential(listener.fingerprint, "old-password"))
    with pytest.raises(PasswordRejected):
        _client(listener, store).connect()
    assert store.get("127.0.0.1") is None  # a saved password that no longer works is removed


def test_changed_security_key(listener, memory_credentials):
    store = memory_credentials
    store.set("127.0.0.1", Credential("00" * 32, "secret"))
    with pytest.raises(FingerprintChanged):
        _client(listener, store).connect()
    client = _client(listener, store)
    client.connect(trust_new_fingerprint=True)
    assert client.connected
    assert store.get("127.0.0.1").fingerprint == listener.fingerprint
    client.disconnect("user disconnect")


def test_token_store_keeps_jwt_not_password(listener):
    store = CredentialStore()  # not encrypted at rest, like Switchboard's non-Windows store
    client = _client(listener, store)
    client.connect(password="secret")
    assert store.get("127.0.0.1").secret == listener.jwt
    client.disconnect("user disconnect")
    again = _client(listener, store)
    again.connect()
    assert listener.auth_attempts[-1].get("jwt") == listener.jwt
    again.disconnect("user disconnect")


def test_device_manager_over_quic(listener, memory_credentials):
    bus = EventBus()
    events = []
    bus.subscribe("device.auth", lambda t, p: events.append(p))
    cfg = AppConfig(devices=[DeviceConfig(name="Node", address="127.0.0.1", listener_port=listener.port,
                                          listener_protocol="quic")])
    cfg.project().engine_dir = "C:/UE_5.8"
    cfg.project().uproject_path = "D:/P/P.uproject"
    mgr = DeviceManager(cfg, bus)

    mgr.connect("Node")
    assert mgr.get("Node").state == "error"
    assert events and events[0]["kind"] == "password" and events[0]["address"] == "127.0.0.1"

    mgr.connect("Node", password="secret")
    rt = mgr.get("Node")
    assert rt.state == "connected" and rt.transport == "quic"
    mgr.start("Node")
    assert wait_until(lambda: rt.state == "running", 5), (rt.state, rt.last_error, listener.received)
    assert any(m["command"] == "start" for m in listener.received)
    import time
    time.sleep(1.3)  # at least one keepalive
    assert listener.unknown == []  # every command is one the real listener understands
    mgr.disconnect_all()


def test_pasted_password_with_line_break_or_spaces(listener, memory_credentials):
    """Copying the password from the listener window can bring a line break or spaces along."""
    crlf = chr(13) + chr(10)
    for pasted in ("secret" + crlf, " secret ", "sec" + chr(0x200B) + "ret"):
        memory_credentials.set("127.0.0.1", None)
        client = _client(listener, memory_credentials)
        client.connect(password=pasted)
        assert client.connected, repr(pasted)
        assert memory_credentials.get("127.0.0.1").secret == "secret"  # the cleaned password is saved
        client.disconnect("user disconnect")
    with pytest.raises(PasswordRejected):
        _client(listener, memory_credentials).connect(password=" wrong " + crlf)

"""Content-blind TCP attribution and narrow dotenv mutation contracts."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
spec = importlib.util.spec_from_file_location(
    "dashboard_proxy_peer", Path(__file__).parents[2] / "scripts/dashboard_proxy_peer.py"
)
peer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peer)


def test_only_one_new_closed_established_connection_can_supply_peer():
    existing = ("020012AC:A0F0", "010012AC:C100", "123")
    probe = ("020012AC:A0F0", "010012AC:C101", "124")
    raw = "header\n0: 020012AC:A0F0 010012AC:C101 01 0 0 0 0 0 124\n"
    assert peer.established_rows(raw) == {probe}
    assert peer.select_peer({existing}, {existing, probe}, {existing}) == "172.18.0.1"
    for after in ({existing, probe}, {probe}, set()):
        with pytest.raises(RuntimeError):
            peer.select_peer(set(), {existing, probe}, after)
    assert peer.established_rows(raw.replace(" 01 ", " 08 ")) == set()
    assert peer.established_rows(raw.replace(":A0F0", ":0050")) == set()
    peer.require_gateway("172.18.0.1", "172.18.0.1\n172.19.0.1\n")
    with pytest.raises(RuntimeError, match="not a container network gateway"):
        peer.require_gateway("172.18.0.5", "172.18.0.1\n")
    with pytest.raises(RuntimeError):
        peer.require_gateway("172.18.0.1", "")


def test_env_update_preserves_other_settings_and_permissions(tmp_path):
    env = tmp_path / ".env.dev"
    env.write_text("UNRELATED='sensitive value'\nexport DASHBOARD_AUTH_TRUSTED_PROXY_PEERS=old\n")
    env.chmod(0o600)
    peer.update_env(env, "172.18.0.1")
    expected = "UNRELATED='sensitive value'\nDASHBOARD_AUTH_TRUSTED_PROXY_PEERS=172.18.0.1\n"
    assert env.read_text() == expected
    assert env.stat().st_mode & 0o777 == 0o600
    peer.update_env(env, "172.18.0.1")
    assert env.read_text() == expected
    env.write_text("UNRELATED=value\n")
    peer.update_env(env, "127.0.0.1")
    assert env.read_text() == "UNRELATED=value\nDASHBOARD_AUTH_TRUSTED_PROXY_PEERS=127.0.0.1\n"
    env.write_text(expected + "DASHBOARD_AUTH_TRUSTED_PROXY_PEERS=other\n")
    with pytest.raises(RuntimeError):
        peer.update_env(env, "172.18.0.2")
    link = tmp_path / "link"
    link.symlink_to(env)
    with pytest.raises(RuntimeError):
        peer.update_env(link, "172.18.0.2")


@pytest.mark.parametrize("response", [None, b"", b"unexpected"])
def test_measurement_closes_probe_and_never_sends_payload(monkeypatch, response):
    row = ("020012AC:A0F0", "010012AC:C101", "124")
    snapshots = iter([set(), {row}, {row}, set()])
    monkeypatch.setattr(peer, "snapshot", lambda container: next(snapshots))
    monkeypatch.setattr(peer.time, "sleep", lambda seconds: None)
    events = []

    class Connection:
        def __enter__(self):
            events.append("open")
            return self

        def setblocking(self, value):
            assert value is False

        def recv(self, size, flags):
            if response is not None:
                return response
            raise BlockingIOError

        def __exit__(self, *args):
            events.append("closed")

    def connect(address, timeout):
        assert address == ("127.0.0.1", 42200)
        return Connection()

    monkeypatch.setattr(peer.socket, "create_connection", connect)
    if response is None:
        assert peer.measure_peer("synthetic-api", 42200) == "172.18.0.1"
    else:
        with pytest.raises(RuntimeError, match="closed or received"):
            peer.measure_peer("synthetic-api", 42200)
    assert events == ["open", "closed"]


def test_real_payload_free_loopback_connection_is_attributed(monkeypatch):
    with peer.socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(
            peer,
            "snapshot",
            lambda container: peer.established_rows(Path("/proc/net/tcp").read_text(), port),
        )
        assert peer.measure_peer("synthetic", port) == "127.0.0.1"

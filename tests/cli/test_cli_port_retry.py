"""Tests for port-conflict and DB-unreachable retry logic in _start_all."""

import asyncio
import errno
import socket
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.cli import (
    _is_db_unreachable,
    _is_port_conflict,
    _ordered_configs,
    _start_all,
)
from butlers.storage import BlobStorageStartupError

pytestmark = pytest.mark.unit


def test_is_port_conflict():
    """EADDRINUSE returns True; other OSError and non-OSError return False."""
    assert _is_port_conflict(OSError(errno.EADDRINUSE, "Address already in use")) is True
    assert _is_port_conflict(OSError(errno.EACCES, "Permission denied")) is False
    assert _is_port_conflict(RuntimeError("boom")) is False


def test_is_db_unreachable_direct():
    """Transient connection errors are detected."""
    assert _is_db_unreachable(ConnectionRefusedError(errno.ECONNREFUSED, "refused")) is True
    assert _is_db_unreachable(OSError(errno.ECONNREFUSED, "Connect call failed")) is True
    assert _is_db_unreachable(OSError(errno.ETIMEDOUT, "timed out")) is True
    assert _is_db_unreachable(OSError(errno.ENETUNREACH, "unreachable")) is True
    assert _is_db_unreachable(socket.gaierror(-2, "nodename nor servname provided")) is True
    assert _is_db_unreachable(TimeoutError("slow")) is True


def test_is_db_unreachable_excludes_eaddrinuse():
    """Port conflicts are the port-retry path, not the DB-retry path."""
    assert _is_db_unreachable(OSError(errno.EADDRINUSE, "Address already in use")) is False
    assert _is_db_unreachable(OSError(errno.EACCES, "Permission denied")) is False
    assert _is_db_unreachable(RuntimeError("unrelated")) is False


def test_is_db_unreachable_walks_cause_chain():
    """Wrapped OSError in __cause__ is still detected."""
    inner = OSError(errno.ECONNREFUSED, "refused")
    try:
        try:
            raise inner
        except OSError as e:
            raise RuntimeError("wrapped") from e
    except RuntimeError as outer:
        assert _is_db_unreachable(outer) is True


def test_is_db_unreachable_excludes_blob_storage_startup_errors():
    """Blob storage network failures are fatal startup errors, not DB retry candidates."""
    try:
        try:
            raise TimeoutError("slow S3 endpoint")
        except TimeoutError as e:
            raise BlobStorageStartupError("Cannot reach S3 endpoint") from e
    except BlobStorageStartupError as outer:
        assert _is_db_unreachable(outer) is False


class TestStartAllPortRetry:
    @pytest.fixture
    def configs(self, tmp_path):
        d = tmp_path / "test_butler"
        d.mkdir()
        (d / "butler.toml").write_text('[butler]\nname = "test_butler"\nport = 19999\n')
        return {"test_butler": d}

    @pytest.mark.asyncio
    async def test_retries_on_eaddrinuse_then_succeeds(self, configs):
        """Butler starts successfully after transient port conflict."""
        call_count = 0

        async def _mock_start(self):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError(errno.EADDRINUSE, "Address already in use")

        with (
            patch("butlers.daemon.ButlerDaemon") as MockDaemon,
            patch("butlers.cli._PORT_RETRY_BASE_DELAY", 0.01),
        ):
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_mock_start.__get__(instance))
            instance.shutdown = AsyncMock()
            MockDaemon.return_value = instance

            loop = asyncio.get_event_loop()

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                event_instance.set = AsyncMock()
                event_instance.is_set = lambda: False
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

            assert call_count == 3

    @pytest.mark.asyncio
    async def test_skips_butler_after_max_retries(self, configs):
        """Butler is skipped after _PORT_RETRY_MAX_ATTEMPTS consecutive failures."""
        call_count = 0

        async def _always_fail(self):
            nonlocal call_count
            call_count += 1
            raise OSError(errno.EADDRINUSE, "Address already in use")

        loop = asyncio.get_event_loop()
        max_attempts = 3

        with (
            patch("butlers.daemon.ButlerDaemon") as MockDaemon,
            patch("butlers.cli._PORT_RETRY_BASE_DELAY", 0.001),
            patch("butlers.cli._PORT_RETRY_MAX_DELAY", 0.01),
            patch("butlers.cli._PORT_RETRY_MAX_ATTEMPTS", max_attempts),
        ):
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_always_fail.__get__(instance))
            instance.shutdown = AsyncMock()
            MockDaemon.return_value = instance

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

            # Initial attempt + max_attempts retries = max_attempts + 1 total calls
            assert call_count == max_attempts + 1

    @pytest.mark.asyncio
    async def test_non_port_error_fails_immediately(self, configs):
        """Non-EADDRINUSE errors are not retried."""
        call_count = 0

        async def _other_error(self):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("something else broke")

        loop = asyncio.get_event_loop()

        with (
            patch("butlers.daemon.ButlerDaemon") as MockDaemon,
            patch("butlers.cli._PORT_RETRY_BASE_DELAY", 0.01),
        ):
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_other_error.__get__(instance))
            instance.shutdown = AsyncMock()
            MockDaemon.return_value = instance

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

            assert call_count == 1


class TestStartAllDbRetry:
    @pytest.fixture
    def configs(self, tmp_path):
        d = tmp_path / "test_butler"
        d.mkdir()
        (d / "butler.toml").write_text('[butler]\nname = "test_butler"\nport = 19999\n')
        return {"test_butler": d}

    @pytest.mark.asyncio
    async def test_retries_on_db_unreachable_then_succeeds(self, configs):
        """Butler starts successfully after transient DB-connect failure."""
        call_count = 0

        async def _mock_start(self):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionRefusedError(
                    errno.ECONNREFUSED, "Connect call failed ('100.105.147.86', 5432)"
                )

        with (
            patch("butlers.daemon.ButlerDaemon") as MockDaemon,
            patch("butlers.cli._DB_RETRY_BASE_DELAY", 0.01),
            patch("butlers.cli._DB_RETRY_MAX_DELAY", 0.05),
        ):
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_mock_start.__get__(instance))
            instance.shutdown = AsyncMock()
            MockDaemon.return_value = instance

            loop = asyncio.get_event_loop()

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                event_instance.set = AsyncMock()
                event_instance.is_set = lambda: False
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

            assert call_count == 3

    @pytest.mark.asyncio
    async def test_fails_db_unreachable_after_max_retries(self, configs):
        """Butler fails after _DB_RETRY_MAX_ATTEMPTS consecutive DB failures."""
        call_count = 0

        async def _always_fail(self):
            nonlocal call_count
            call_count += 1
            raise OSError(errno.ECONNREFUSED, "Connect call failed")

        loop = asyncio.get_event_loop()
        max_attempts = 3

        with (
            patch("butlers.daemon.ButlerDaemon") as MockDaemon,
            patch("butlers.cli._DB_RETRY_BASE_DELAY", 0.001),
            patch("butlers.cli._DB_RETRY_MAX_DELAY", 0.01),
            patch("butlers.cli._DB_RETRY_MAX_ATTEMPTS", max_attempts),
        ):
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_always_fail.__get__(instance))
            instance.shutdown = AsyncMock()
            MockDaemon.return_value = instance

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

            # Initial attempt + max_attempts retries = max_attempts + 1 total calls
            assert call_count == max_attempts + 1


class TestStartAllReleasesFailedDaemon:
    """Regression: a failed daemon.start() must release its prebound MCP socket.

    daemon.start() pre-binds the MCP port (lifecycle step 14) before later
    startup steps run.  If a later step raises, _start_all used to discard the
    daemon without shutting it down, leaking the listening socket so the next
    retry (or the next butler) hit "port still in use".  These tests pin that
    _start_all calls daemon.shutdown() on every failed-start path.
    """

    @pytest.fixture
    def configs(self, tmp_path):
        d = tmp_path / "test_butler"
        d.mkdir()
        (d / "butler.toml").write_text('[butler]\nname = "test_butler"\nport = 19999\n')
        return {"test_butler": d}

    @pytest.mark.asyncio
    async def test_shutdown_called_on_each_failed_attempt_before_skip(self, configs):
        """Every failed start (including retries) releases resources via shutdown()."""
        call_count = 0

        async def _always_fail(self):
            nonlocal call_count
            call_count += 1
            raise OSError(errno.EADDRINUSE, "Address already in use")

        loop = asyncio.get_event_loop()
        max_attempts = 3

        with (
            patch("butlers.daemon.ButlerDaemon") as MockDaemon,
            patch("butlers.cli._PORT_RETRY_BASE_DELAY", 0.001),
            patch("butlers.cli._PORT_RETRY_MAX_DELAY", 0.01),
            patch("butlers.cli._PORT_RETRY_MAX_ATTEMPTS", max_attempts),
        ):
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_always_fail.__get__(instance))
            instance.shutdown = AsyncMock()
            MockDaemon.return_value = instance

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

            # initial attempt + max_attempts retries, each one cleaned up
            assert call_count == max_attempts + 1
            assert instance.shutdown.await_count == max_attempts + 1

    @pytest.mark.asyncio
    async def test_shutdown_called_on_non_port_failure(self, configs):
        """A non-retried failure still releases the partially-started daemon."""

        async def _other_error(self):
            raise RuntimeError("a post-bind startup step blew up")

        loop = asyncio.get_event_loop()

        with patch("butlers.daemon.ButlerDaemon") as MockDaemon:
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_other_error.__get__(instance))
            instance.shutdown = AsyncMock()
            MockDaemon.return_value = instance

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

            instance.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_error_does_not_crash_orchestrator(self, configs):
        """A failing shutdown() during cleanup is swallowed, not propagated."""

        async def _other_error(self):
            raise RuntimeError("start failed")

        loop = asyncio.get_event_loop()

        with patch("butlers.daemon.ButlerDaemon") as MockDaemon:
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_other_error.__get__(instance))
            instance.shutdown = AsyncMock(side_effect=RuntimeError("cleanup also failed"))
            MockDaemon.return_value = instance

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    # Must not raise even though cleanup shutdown() raises.
                    await _start_all(configs)

            instance.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_real_prebound_socket_is_released_no_port_leak(self, configs):
        """End-to-end: a real socket bound during start() is freed on failure.

        Models the actual bug: start() pre-binds a listening socket (as
        _start_mcp_server does) then a later step raises.  shutdown() mirrors
        production by closing that socket.  After _start_all, the port must be
        rebindable -- proving _start_all invoked shutdown() and did not leak the
        listening socket.
        """
        # Bind to an ephemeral port to learn a free port, then release it so the
        # fake daemon can claim it during start().
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 0))
        leaked_port = probe.getsockname()[1]
        probe.close()

        bound_sockets: list[socket.socket] = []

        class FakeDaemon:
            def __init__(self, config_dir):
                self.config_dir = config_dir
                self._mcp_socket: socket.socket | None = None

            async def start(self):
                # Pre-bind the MCP socket (lifecycle step 14)...
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", leaked_port))
                sock.listen(16)
                self._mcp_socket = sock
                bound_sockets.append(sock)
                # ...then a later startup step fails.
                raise RuntimeError("post-bind startup step failed")

            async def shutdown(self):
                if self._mcp_socket is not None:
                    self._mcp_socket.close()
                    self._mcp_socket = None

        loop = asyncio.get_event_loop()

        with patch("butlers.daemon.ButlerDaemon", FakeDaemon):
            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

        # The port must now be free: a fresh bind without SO_REUSEADDR succeeds
        # only if _start_all closed the leaked listening socket via shutdown().
        verify = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            verify.bind(("127.0.0.1", leaked_port))
        finally:
            verify.close()
            for s in bound_sockets:
                try:
                    s.close()
                except OSError:
                    pass


class TestOrderedConfigs:
    def test_switchboard_starts_first(self):
        """Switchboard is prioritized before alphabetical ordering."""
        configs = {
            "education": Path("/a"),
            "switchboard": Path("/b"),
            "general": Path("/c"),
            "travel": Path("/d"),
        }
        result = _ordered_configs(configs)
        names = [n for n, _ in result]
        assert names[0] == "switchboard"
        assert names[1:] == ["education", "general", "travel"]

    def test_no_switchboard_falls_back_to_sorted(self):
        """Without switchboard, order is purely alphabetical."""
        configs = {"general": Path("/a"), "education": Path("/b")}
        result = _ordered_configs(configs)
        names = [n for n, _ in result]
        assert names == ["education", "general"]

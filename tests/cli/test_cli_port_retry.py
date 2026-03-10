"""Tests for port-conflict retry logic in _start_all."""

import asyncio
import errno
from unittest.mock import AsyncMock, patch

import pytest

from butlers.cli import _is_port_conflict, _start_all

pytestmark = pytest.mark.unit


class TestIsPortConflict:
    def test_eaddrinuse_detected(self):
        exc = OSError(errno.EADDRINUSE, "Address already in use")
        assert _is_port_conflict(exc) is True

    def test_other_oserror_not_detected(self):
        exc = OSError(errno.EACCES, "Permission denied")
        assert _is_port_conflict(exc) is False

    def test_non_oserror_not_detected(self):
        assert _is_port_conflict(RuntimeError("boom")) is False


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

            assert call_count == 3  # failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_retries_indefinitely_on_port_conflict(self, configs):
        """Butler retries indefinitely on port conflict (verify 10+ attempts)."""
        call_count = 0
        max_failures = 10

        async def _fail_then_succeed(self):
            nonlocal call_count
            call_count += 1
            if call_count <= max_failures:
                raise OSError(errno.EADDRINUSE, "Address already in use")

        loop = asyncio.get_event_loop()

        with (
            patch("butlers.daemon.ButlerDaemon") as MockDaemon,
            patch("butlers.cli._PORT_RETRY_BASE_DELAY", 0.001),
            patch("butlers.cli._PORT_RETRY_MAX_DELAY", 0.01),
        ):
            instance = AsyncMock()
            instance.start = AsyncMock(side_effect=_fail_then_succeed.__get__(instance))
            instance.shutdown = AsyncMock()
            MockDaemon.return_value = instance

            with patch("asyncio.Event") as MockEvent:
                event_instance = AsyncMock()
                event_instance.wait = AsyncMock()
                MockEvent.return_value = event_instance

                with patch.object(loop, "add_signal_handler"):
                    await _start_all(configs)

            assert call_count == max_failures + 1  # retried past old limit

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

            assert call_count == 1  # no retry

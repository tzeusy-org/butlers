"""Tests for port-conflict retry logic in _start_all."""

import asyncio
import errno
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.cli import _is_port_conflict, _ordered_configs, _start_all

pytestmark = pytest.mark.unit


def test_is_port_conflict():
    """EADDRINUSE returns True; other OSError and non-OSError return False."""
    assert _is_port_conflict(OSError(errno.EADDRINUSE, "Address already in use")) is True
    assert _is_port_conflict(OSError(errno.EACCES, "Permission denied")) is False
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

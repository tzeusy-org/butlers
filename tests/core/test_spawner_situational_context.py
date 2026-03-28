"""Tests for situational context preamble injection in Spawner [bu-0qty]."""

from __future__ import annotations

import logging
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import butlers.core.spawner as _spawner_module
from butlers.config import ButlerConfig
from butlers.context_bus import ContextEntry, format_context_preamble
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner, fetch_situational_context_preamble

pytestmark = pytest.mark.unit


def _make_config(
    name: str = "test-butler",
    port: int = 9100,
) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        env_required=[],
        env_optional=[],
    )


# ---------------------------------------------------------------------------
# Unit tests for fetch_situational_context_preamble()
# ---------------------------------------------------------------------------


class TestFetchSituationalContextPreamble:
    async def test_returns_preamble_when_signals_active(self):
        """Returns formatted preamble string when active signals exist."""
        from datetime import datetime

        now = datetime.now(tz=UTC)
        active_signals = [
            ContextEntry(
                signal_type="traveling",
                value="Paris",
                set_by_butler="travel",
                set_at=now,
                expires_at=now,
                confidence=1.0,
            ),
        ]

        with patch(
            "butlers.context_bus.get_active_context",
            new_callable=AsyncMock,
            return_value=active_signals,
        ):
            result = await fetch_situational_context_preamble(AsyncMock(), "general")

        assert result == "[User Context: traveling (Paris, explicit)]"

    async def test_returns_none_when_no_signals(self):
        """Returns None when get_active_context returns empty list."""
        with patch(
            "butlers.context_bus.get_active_context",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await fetch_situational_context_preamble(AsyncMock(), "general")

        assert result is None

    async def test_returns_none_when_pool_is_none(self):
        """Returns None immediately when pool is None (no DB available)."""
        with patch(
            "butlers.context_bus.get_active_context",
            new_callable=AsyncMock,
        ) as mock_get:
            result = await fetch_situational_context_preamble(None, "general")

        assert result is None
        mock_get.assert_not_called()

    async def test_returns_none_on_query_failure(self, caplog: pytest.LogCaptureFixture):
        """Returns None and logs WARNING when get_active_context raises an exception."""
        with (
            patch(
                "butlers.context_bus.get_active_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("connection refused"),
            ),
            caplog.at_level(logging.WARNING, logger="butlers.core.spawner"),
        ):
            result = await fetch_situational_context_preamble(AsyncMock(), "general")

        assert result is None
        assert any("situational context preamble" in r.getMessage() for r in caplog.records)

    async def test_warning_log_includes_butler_name(self, caplog: pytest.LogCaptureFixture):
        """Warning log message contains the butler name for debuggability."""
        with (
            patch(
                "butlers.context_bus.get_active_context",
                new_callable=AsyncMock,
                side_effect=Exception("boom"),
            ),
            caplog.at_level(logging.WARNING, logger="butlers.core.spawner"),
        ):
            await fetch_situational_context_preamble(AsyncMock(), "my-butler")

        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("my-butler" in m for m in warning_messages)

    async def test_missing_table_logs_warning_once(self, caplog: pytest.LogCaptureFixture):
        """Missing shared.user_context table logs WARNING only on first call per butler."""
        _spawner_module._missing_context_table_logged.discard("once-butler")
        missing_table_err = RuntimeError('relation "shared.user_context" does not exist')

        with (
            patch(
                "butlers.context_bus.get_active_context",
                new_callable=AsyncMock,
                side_effect=missing_table_err,
            ),
            caplog.at_level(logging.DEBUG, logger="butlers.core.spawner"),
        ):
            result1 = await fetch_situational_context_preamble(AsyncMock(), "once-butler")
            result2 = await fetch_situational_context_preamble(AsyncMock(), "once-butler")

        assert result1 is None
        assert result2 is None
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(warning_records) == 1, "Expected exactly one WARNING for missing table"
        assert "user_context" in warning_records[0].getMessage().lower()
        assert any("still missing" in r.getMessage() for r in debug_records)

    async def test_missing_table_different_butlers_each_warn_once(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Each butler gets its own once-per-warning for missing table."""
        _spawner_module._missing_context_table_logged.discard("butler-a")
        _spawner_module._missing_context_table_logged.discard("butler-b")
        missing_table_err = RuntimeError('relation "shared.user_context" does not exist')

        with (
            patch(
                "butlers.context_bus.get_active_context",
                new_callable=AsyncMock,
                side_effect=missing_table_err,
            ),
            caplog.at_level(logging.WARNING, logger="butlers.core.spawner"),
        ):
            await fetch_situational_context_preamble(AsyncMock(), "butler-a")
            await fetch_situational_context_preamble(AsyncMock(), "butler-b")

        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert sum(1 for m in warning_messages if "butler-a" in m) == 1
        assert sum(1 for m in warning_messages if "butler-b" in m) == 1


# ---------------------------------------------------------------------------
# Adapter capturing system_prompt
# ---------------------------------------------------------------------------


class _CapturingAdapter(RuntimeAdapter):
    """Minimal capturing adapter for system_prompt injection tests."""

    def __init__(self) -> None:
        self.captured_system_prompts: list[str] = []

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict,
        env: dict,
        **kwargs: Any,
    ) -> tuple:
        self.captured_system_prompts.append(system_prompt)
        return "Done", [], None

    def build_config_file(self, mcp_servers: dict, tmp_dir: Any) -> Any:
        config_path = tmp_dir / "mock.json"
        config_path.write_text("{}")
        return config_path

    def parse_system_prompt_file(self, config_dir: Any) -> str:
        claude_md = config_dir / "CLAUDE.md"
        if claude_md.exists():
            return claude_md.read_text().strip()
        return ""


# ---------------------------------------------------------------------------
# Integration tests for Spawner context preamble injection
# ---------------------------------------------------------------------------


class TestSpawnerSituationalContextInjection:
    async def test_context_preamble_injected_when_signals_active(self, tmp_path: Path):
        """Context preamble is inserted into system prompt when signals are active."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Base prompt.")
        config = _make_config()

        adapter = _CapturingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_situational_context_preamble",
            new_callable=AsyncMock,
            return_value="[User Context: traveling (Paris, explicit)]",
        ):
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        assert adapter.captured_system_prompts[-1] == (
            "Base prompt.\n\n[User Context: traveling (Paris, explicit)]"
        )

    async def test_no_context_preamble_when_no_signals(self, tmp_path: Path):
        """System prompt is unchanged when no active signals exist."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Base prompt.")
        config = _make_config()

        adapter = _CapturingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_situational_context_preamble",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        assert adapter.captured_system_prompts[-1] == "Base prompt."

    async def test_context_preamble_before_memory_context(self, tmp_path: Path):
        """Context preamble appears after identity but before memory context."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Identity.")
        config = _make_config(
            name="test-butler",
        )
        # enable memory module
        config = ButlerConfig(
            name="test-butler",
            port=9100,
            modules={"memory": {}},
            env_required=[],
            env_optional=[],
        )

        adapter = _CapturingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        with (
            patch(
                "butlers.core.spawner.fetch_situational_context_preamble",
                new_callable=AsyncMock,
                return_value="[User Context: dnd (explicit)]",
            ),
            patch(
                "butlers.core.spawner.fetch_memory_context",
                new_callable=AsyncMock,
                return_value="Memory: user prefers short answers.",
            ),
        ):
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        result = adapter.captured_system_prompts[-1]
        assert result == (
            "Identity.\n\n[User Context: dnd (explicit)]\n\nMemory: user prefers short answers."
        )

    async def test_invocation_proceeds_on_context_query_failure(self, tmp_path: Path):
        """Spawn succeeds even when fetch_situational_context_preamble returns None."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Base prompt.")
        config = _make_config()

        adapter = _CapturingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_situational_context_preamble",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await spawner.trigger(prompt="do task", trigger_source="trigger")

        assert result.success is True
        assert adapter.captured_system_prompts[-1] == "Base prompt."


# ---------------------------------------------------------------------------
# Unit tests for format_context_preamble()
# ---------------------------------------------------------------------------


class TestFormatContextPreamble:
    def test_single_signal_with_value(self):
        from datetime import datetime

        now = datetime.now(tz=UTC)
        signals = [
            ContextEntry(
                signal_type="traveling",
                value="Paris",
                set_by_butler="travel",
                set_at=now,
                expires_at=now,
                confidence=1.0,
            )
        ]
        assert format_context_preamble(signals) == "[User Context: traveling (Paris, explicit)]"

    def test_single_signal_without_value(self):
        from datetime import datetime

        now = datetime.now(tz=UTC)
        signals = [
            ContextEntry(
                signal_type="dnd",
                value=None,
                set_by_butler="general",
                set_at=now,
                expires_at=now,
                confidence=1.0,
            )
        ]
        assert format_context_preamble(signals) == "[User Context: dnd (explicit)]"

    def test_multiple_signals(self):
        from datetime import datetime

        now = datetime.now(tz=UTC)
        signals = [
            ContextEntry(
                signal_type="traveling",
                value="Paris",
                set_by_butler="travel",
                set_at=now,
                expires_at=now,
                confidence=1.0,
            ),
            ContextEntry(
                signal_type="meeting",
                value="standup",
                set_by_butler="general",
                set_at=now,
                expires_at=now,
                confidence=0.8,
            ),
        ]
        expected = "[User Context: traveling (Paris, explicit), meeting (standup, high confidence)]"
        assert format_context_preamble(signals) == expected

    def test_empty_signals_returns_empty_string(self):
        assert format_context_preamble([]) == ""

    def test_confidence_labels(self):
        from datetime import datetime

        now = datetime.now(tz=UTC)

        def _entry(confidence: float) -> ContextEntry:
            return ContextEntry(
                signal_type="focused",
                value=None,
                set_by_butler="general",
                set_at=now,
                expires_at=now,
                confidence=confidence,
            )

        assert "explicit" in format_context_preamble([_entry(1.0)])
        assert "high confidence" in format_context_preamble([_entry(0.9)])
        assert "high confidence" in format_context_preamble([_entry(0.8)])
        assert "medium confidence" in format_context_preamble([_entry(0.7)])
        assert "medium confidence" in format_context_preamble([_entry(0.5)])
        assert "low confidence" in format_context_preamble([_entry(0.4)])
        assert "low confidence" in format_context_preamble([_entry(0.0)])

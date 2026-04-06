"""Tests for situational context preamble injection in Spawner [bu-0qty]."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

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


def _make_entry(
    signal_type: str = "traveling",
    value: str | None = "Paris",
    confidence: float = 1.0,
) -> ContextEntry:
    now = datetime.now(tz=UTC)
    return ContextEntry(
        signal_type=signal_type,
        value=value,
        set_by_butler="travel",
        set_at=now,
        expires_at=now,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Unit tests for fetch_situational_context_preamble()
# ---------------------------------------------------------------------------


class TestFetchSituationalContextPreamble:
    async def test_returns_preamble_or_none(self):
        """Active signals → preamble string; empty/None pool → None."""
        # Active signals → preamble returned
        with patch(
            "butlers.context_bus.get_active_context",
            new_callable=AsyncMock,
            return_value=[_make_entry()],
        ):
            result = await fetch_situational_context_preamble(AsyncMock(), "general")
        assert result == "[User Context: traveling (Paris, explicit)]"

        # Empty signals → None
        with patch(
            "butlers.context_bus.get_active_context",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result2 = await fetch_situational_context_preamble(AsyncMock(), "general")
        assert result2 is None

        # Pool is None → None, get_active_context not called
        with patch(
            "butlers.context_bus.get_active_context", new_callable=AsyncMock
        ) as mock_get:
            result3 = await fetch_situational_context_preamble(None, "general")
        assert result3 is None
        mock_get.assert_not_called()

    async def test_failure_returns_none_with_warning(self, caplog: pytest.LogCaptureFixture):
        """Query failure → None + WARNING log with butler name."""
        with (
            patch(
                "butlers.context_bus.get_active_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("connection refused"),
            ),
            caplog.at_level(logging.WARNING, logger="butlers.core.spawner"),
        ):
            result = await fetch_situational_context_preamble(AsyncMock(), "my-butler")
        assert result is None
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("situational context preamble" in m for m in warning_msgs)
        assert any("my-butler" in m for m in warning_msgs)



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
    async def test_context_preamble_injection(self, tmp_path: Path):
        """Preamble injected into system prompt; absent when None; ordering with memory."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Base prompt.")

        adapter = _CapturingAdapter()
        spawner = Spawner(config=_make_config(), config_dir=config_dir, runtime=adapter)

        # Preamble present
        with patch(
            "butlers.core.spawner.fetch_situational_context_preamble",
            new_callable=AsyncMock,
            return_value="[User Context: traveling (Paris, explicit)]",
        ):
            await spawner.trigger(prompt="do task", trigger_source="trigger")
        assert adapter.captured_system_prompts[-1] == (
            "Base prompt.\n\n[User Context: traveling (Paris, explicit)]"
        )

        # No preamble (None) → prompt unchanged; spawn succeeds
        with patch(
            "butlers.core.spawner.fetch_situational_context_preamble",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await spawner.trigger(prompt="do task", trigger_source="trigger")
        assert result.success is True
        assert adapter.captured_system_prompts[-1] == "Base prompt."

    async def test_context_preamble_before_memory_context(self, tmp_path: Path):
        """Context preamble appears after identity but before memory context."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Identity.")
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

        assert adapter.captured_system_prompts[-1] == (
            "Identity.\n\n[User Context: dnd (explicit)]\n\nMemory: user prefers short answers."
        )


# ---------------------------------------------------------------------------
# Unit tests for format_context_preamble()
# ---------------------------------------------------------------------------


class TestFormatContextPreamble:
    def test_format_preamble_variants(self):
        """Single with/without value; multiple; empty; confidence labels."""
        now = datetime.now(tz=UTC)

        def _e(signal, value=None, conf=1.0):
            return ContextEntry(
                signal_type=signal,
                value=value,
                set_by_butler="general",
                set_at=now,
                expires_at=now,
                confidence=conf,
            )

        # With value
        assert format_context_preamble([_e("traveling", "Paris")]) == (
            "[User Context: traveling (Paris, explicit)]"
        )
        # Without value
        assert format_context_preamble([_e("dnd")]) == "[User Context: dnd (explicit)]"
        # Multiple
        multi = format_context_preamble([_e("traveling", "Paris"), _e("meeting", "standup", 0.8)])
        assert multi == (
            "[User Context: traveling (Paris, explicit), meeting (standup, high confidence)]"
        )
        # Empty
        assert format_context_preamble([]) == ""

        # Confidence labels
        assert "explicit" in format_context_preamble([_e("focused", conf=1.0)])
        assert "high confidence" in format_context_preamble([_e("focused", conf=0.9)])
        assert "high confidence" in format_context_preamble([_e("focused", conf=0.8)])
        assert "medium confidence" in format_context_preamble([_e("focused", conf=0.7)])
        assert "medium confidence" in format_context_preamble([_e("focused", conf=0.5)])
        assert "low confidence" in format_context_preamble([_e("focused", conf=0.4)])
        assert "low confidence" in format_context_preamble([_e("focused", conf=0.0)])

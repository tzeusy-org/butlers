"""Tests for UUID7 request_id minting in the Spawner.

Covers bu-0b7.5: internally-triggered sessions (tick, scheduler dispatch,
manual trigger) must always supply a non-null request_id to session_create().
When the caller does not pass one, the Spawner mints a fresh UUID7.
Connector-sourced sessions pass their own request_id unchanged.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

# UUID pattern for sanity-checking the minted IDs are valid UUIDs
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class _OkAdapter(RuntimeAdapter):
    """Minimal adapter that always succeeds."""

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        return ("ok", [], None)

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        return tmp_dir / "cfg.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        modules={},
        env_required=[],
        env_optional=[],
    )


def _fake_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=uuid.UUID("00000000-0000-0000-0000-000000000001"))
    return pool


class TestSpawnerRequestIdMinting:
    """Spawner mints a UUID7 for internally-triggered sessions."""

    async def test_request_id_minting_passthrough_and_no_pool(self, tmp_path: Path):
        """Internal triggers mint valid unique UUIDs; connector request_id passed unchanged; pool=None works."""
        # Internal triggers mint a valid UUID per session
        pool = _fake_pool()
        spawner = Spawner(
            config=_make_config(), config_dir=tmp_path, pool=pool, runtime=_OkAdapter()
        )
        collected: list[str] = []
        side_effects = [uuid.UUID(f"00000000-0000-0000-0000-{i:012d}") for i in range(1, 4)]
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.side_effect = side_effects
            for trigger_source in ("tick", "schedule:daily", "trigger"):
                await spawner.trigger(prompt="work", trigger_source=trigger_source)
                _, kwargs = mock_create.call_args
                minted = kwargs.get("request_id")
                assert minted is not None, f"request_id must not be None for {trigger_source!r}"
                assert _UUID_RE.match(minted), f"request_id {minted!r} is not a valid UUID"
                collected.append(minted)
        assert len(set(collected)) == 3

        # Connector-supplied request_id passed through unchanged
        pool2 = _fake_pool()
        spawner2 = Spawner(
            config=_make_config(), config_dir=tmp_path, pool=pool2, runtime=_OkAdapter()
        )
        connector_request_id = "0195f3a2-1234-7000-8000-abcdef012345"
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create2,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create2.return_value = uuid.UUID("00000000-0000-0000-0000-000000000004")
            await spawner2.trigger(
                prompt="routed message", trigger_source="route", request_id=connector_request_id
            )
            _, kwargs2 = mock_create2.call_args
            assert kwargs2.get("request_id") == connector_request_id

        # pool=None does not raise
        spawner3 = Spawner(
            config=_make_config(), config_dir=tmp_path, pool=None, runtime=_OkAdapter()
        )
        result = await spawner3.trigger(prompt="no pool tick", trigger_source="tick")
        assert result.success is True

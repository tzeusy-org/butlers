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

from butlers.config import ButlerConfig, RuntimeConfig
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
        runtime=RuntimeConfig(),
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

    async def test_no_request_id_mints_uuid7_for_tick(self, tmp_path: Path):
        """When trigger_source='tick' and request_id=None, Spawner mints a UUID7."""
        pool = _fake_pool()
        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            pool=pool,
            runtime=_OkAdapter(),
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")

            await spawner.trigger(prompt="tick work", trigger_source="tick")

            mock_create.assert_called_once()
            _, kwargs = mock_create.call_args
            minted = kwargs.get("request_id")
            assert minted is not None, "request_id must not be None for tick-triggered sessions"
            assert _UUID_RE.match(minted), f"request_id {minted!r} is not a valid UUID string"

    async def test_no_request_id_mints_uuid7_for_schedule(self, tmp_path: Path):
        """When trigger_source='schedule:daily' and request_id=None, Spawner mints a UUID7."""
        pool = _fake_pool()
        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            pool=pool,
            runtime=_OkAdapter(),
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000002")

            await spawner.trigger(prompt="daily task", trigger_source="schedule:daily")

            mock_create.assert_called_once()
            _, kwargs = mock_create.call_args
            minted = kwargs.get("request_id")
            assert minted is not None, "request_id must not be None for schedule-triggered sessions"
            assert _UUID_RE.match(minted), f"request_id {minted!r} is not a valid UUID string"

    async def test_no_request_id_mints_uuid7_for_manual_trigger(self, tmp_path: Path):
        """When trigger_source='trigger' and request_id=None, Spawner mints a UUID7."""
        pool = _fake_pool()
        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            pool=pool,
            runtime=_OkAdapter(),
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000003")

            await spawner.trigger(prompt="manual run", trigger_source="trigger")

            mock_create.assert_called_once()
            _, kwargs = mock_create.call_args
            minted = kwargs.get("request_id")
            assert minted is not None, "request_id must not be None for trigger-sourced sessions"
            assert _UUID_RE.match(minted), f"request_id {minted!r} is not a valid UUID string"

    async def test_each_internal_session_gets_unique_request_id(self, tmp_path: Path):
        """Two consecutive internal sessions must receive distinct minted UUIDs."""
        pool = _fake_pool()
        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            pool=pool,
            runtime=_OkAdapter(),
        )
        collected: list[str] = []

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.side_effect = [
                uuid.UUID("00000000-0000-0000-0000-000000000010"),
                uuid.UUID("00000000-0000-0000-0000-000000000011"),
            ]

            await spawner.trigger(prompt="first", trigger_source="tick")
            await spawner.trigger(prompt="second", trigger_source="tick")

            assert mock_create.call_count == 2
            for call in mock_create.call_args_list:
                _, kwargs = call
                collected.append(kwargs["request_id"])

        assert len(set(collected)) == 2, "Each session must receive a unique minted request_id"

    async def test_connector_request_id_passed_through_unchanged(self, tmp_path: Path):
        """When the caller provides a request_id (connector-sourced), it is passed unchanged."""
        pool = _fake_pool()
        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            pool=pool,
            runtime=_OkAdapter(),
        )

        connector_request_id = "0195f3a2-1234-7000-8000-abcdef012345"

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000004")

            await spawner.trigger(
                prompt="routed message",
                trigger_source="route",
                request_id=connector_request_id,
            )

            mock_create.assert_called_once()
            _, kwargs = mock_create.call_args
            assert kwargs.get("request_id") == connector_request_id, (
                "Connector-sourced request_id must be forwarded to session_create unchanged"
            )

    async def test_no_pool_does_not_raise_without_request_id(self, tmp_path: Path):
        """When pool=None, internally-triggered sessions should not raise."""
        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            pool=None,
            runtime=_OkAdapter(),
        )

        result = await spawner.trigger(prompt="no pool tick", trigger_source="tick")
        assert result.success is True

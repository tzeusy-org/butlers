"""Tests for dispatch_failures recording in the Spawner.

Verifies that public.dispatch_failures is written (best-effort) whenever
a session fails and a catalog entry was resolved.

Coverage:
- Failure row inserted when adapter raises and catalog_entry_id is available
- No row inserted when catalog_entry_id is None (TOML fallback path)
- Insert failure does not propagate (best-effort write)
- error_code matches the exception class name
- error_message is truncated to 4096 chars
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

_FAKE_CATALOG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_SESSION_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")

_INSERT_SQL_FRAGMENT = "INSERT INTO public.dispatch_failures"


class _FailingAdapter(RuntimeAdapter):
    """Adapter that always raises."""

    def __init__(self, *, error_cls: type[Exception] = RuntimeError, msg: str = "boom") -> None:
        self._error_cls = error_cls
        self._msg = msg

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
        runtime_args: list[str] | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        raise self._error_cls(self._msg)

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


class TestSpawnerDispatchFailures:
    """Spawner records dispatch failures to public.dispatch_failures on runtime error."""

    async def test_failure_row_inserted_with_catalog_entry(self, tmp_path: Path) -> None:
        """A row is inserted into dispatch_failures when catalog_entry_id is resolved."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=(DEFAULT_RUNTIME_TYPE, "claude-haiku", [], _FAKE_CATALOG_ID, 1800),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
            ) as mock_quota,
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            from butlers.core.model_routing import QuotaStatus

            mock_quota.return_value = QuotaStatus(
                allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None
            )
            mock_create.return_value = _SESSION_ID

            result = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter(msg="runtime crashed"),
            ).trigger("hello", "tick")

        assert result.success is False
        # Verify execute was called with the dispatch_failures INSERT
        execute_calls = [str(c) for c in mock_pool.execute.call_args_list]
        insert_calls = [c for c in execute_calls if _INSERT_SQL_FRAGMENT in c]
        assert len(insert_calls) == 1, (
            f"Expected exactly one dispatch_failures INSERT, got: {execute_calls}"
        )

    async def test_failure_row_includes_error_code_and_butler(self, tmp_path: Path) -> None:
        """Failure row carries error_code=exception class name and butler name."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(name="test-butler")
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=(DEFAULT_RUNTIME_TYPE, "claude-haiku", [], _FAKE_CATALOG_ID, 1800),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
            ) as mock_quota,
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            from butlers.core.model_routing import QuotaStatus

            mock_quota.return_value = QuotaStatus(
                allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None
            )
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter(error_cls=ValueError, msg="bad config"),
            ).trigger("hello", "tick")

        # Find the dispatch_failures INSERT call and inspect args
        insert_call = None
        for c in mock_pool.execute.call_args_list:
            args = c[0]
            if args and isinstance(args[0], str) and _INSERT_SQL_FRAGMENT in args[0]:
                insert_call = c
                break

        assert insert_call is not None, "dispatch_failures INSERT not found"
        positional = insert_call[0]
        # positional: (sql, catalog_entry_id, error_code, error_message, butler, session_id)
        assert positional[1] == _FAKE_CATALOG_ID  # catalog_entry_id
        assert positional[2] == "ValueError"  # error_code
        assert "bad config" in positional[3]  # error_message
        assert positional[4] == "test-butler"  # butler
        assert positional[5] == _SESSION_ID  # session_id

    async def test_no_failure_row_on_toml_fallback(self, tmp_path: Path) -> None:
        """No dispatch_failures row when catalog_entry_id is None (TOML fallback)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        with (
            # resolve_model returns None → TOML fallback, no catalog_entry_id
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            result = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter(msg="toml path crash"),
            ).trigger("hello", "tick")

        assert result.success is False
        execute_calls = [str(c) for c in mock_pool.execute.call_args_list]
        insert_calls = [c for c in execute_calls if _INSERT_SQL_FRAGMENT in c]
        assert len(insert_calls) == 0, (
            f"Expected no dispatch_failures INSERT on TOML fallback, got: {execute_calls}"
        )

    async def test_insert_failure_does_not_propagate(self, tmp_path: Path) -> None:
        """If the dispatch_failures INSERT itself fails, the exception is swallowed."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        # Make execute raise only for the dispatch_failures INSERT
        async def _execute_side_effect(sql, *args, **kwargs):
            if _INSERT_SQL_FRAGMENT in sql:
                raise RuntimeError("DB write failed")
            return "OK"

        mock_pool.execute = AsyncMock(side_effect=_execute_side_effect)

        with (
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=(DEFAULT_RUNTIME_TYPE, "claude-haiku", [], _FAKE_CATALOG_ID, 1800),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
            ) as mock_quota,
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            from butlers.core.model_routing import QuotaStatus

            mock_quota.return_value = QuotaStatus(
                allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None
            )
            mock_create.return_value = _SESSION_ID

            # Should not raise — insert failure is best-effort
            result = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter(msg="adapter crash"),
            ).trigger("hello", "tick")

        # The dispatch failure (adapter crash) is the primary result
        assert result.success is False
        assert "adapter crash" in (result.error or "")

    async def test_error_message_truncated_to_4096(self, tmp_path: Path) -> None:
        """error_message is truncated to 4096 characters before DB insert."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        long_msg = "x" * 10_000

        with (
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=(DEFAULT_RUNTIME_TYPE, "claude-haiku", [], _FAKE_CATALOG_ID, 1800),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
            ) as mock_quota,
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            from butlers.core.model_routing import QuotaStatus

            mock_quota.return_value = QuotaStatus(
                allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None
            )
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter(msg=long_msg),
            ).trigger("hello", "tick")

        # Find the INSERT call and check error_message length
        insert_call = None
        for c in mock_pool.execute.call_args_list:
            args = c[0]
            if args and isinstance(args[0], str) and _INSERT_SQL_FRAGMENT in args[0]:
                insert_call = c
                break

        assert insert_call is not None
        error_message = insert_call[0][3]  # 4th positional arg
        assert len(error_message) <= 4096

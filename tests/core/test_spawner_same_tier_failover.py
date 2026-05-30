"""Tests for same-tier model failover orchestration in Spawner.

Covers the 5 acceptance criteria from bu-ojiij.3:

1. AC1: Initial catalog resolution establishes the failover effective tier.
2. AC2: Quota-exhausted primary skips to next same-tier candidate; hard-blocks
         only when none remains.
3. AC3: Eligible runtime failures retry same-tier candidates without repeating
         a catalog entry.
4. AC4: Failures after captured tool calls OR rejected classifier decisions
         preserve existing terminal behavior (no retry).
5. AC5: Final SpawnerResult.model and session model reflect the successful
         fallback (the model that ultimately ran).

Also covers:
- Round-trip: initial → quota-skip → success on next candidate
- Round-trip: initial → runtime failure → eligible classifier → retry → success
- Round-trip: initial → runtime failure → ineligible classifier → terminal (no retry)
- Tool-call captured → no retry regardless of error type
- No candidates remain → hard block / final failure
- Persisted session = final outcome only (one session row)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.failover_classifier import FailoverDecision
from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# UUIDs
# ---------------------------------------------------------------------------

_PRIMARY_CATALOG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_FALLBACK_CATALOG_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_SESSION_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")

_QUOTA_ALLOWED = QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)
_QUOTA_DENIED_24H = QuotaStatus(
    allowed=False, usage_24h=1000, limit_24h=1000, usage_30d=0, limit_30d=None
)


# ---------------------------------------------------------------------------
# Mock adapter helpers
# ---------------------------------------------------------------------------


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


class _SuccessAdapter(RuntimeAdapter):
    """Adapter that always succeeds with a configurable output."""

    def __init__(self, *, result_text: str = "ok", model_echo: str | None = None) -> None:
        self._result_text = result_text
        self._model_echo = model_echo
        self.invoke_calls = 0
        self.last_model: str | None = None

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
        self.invoke_calls += 1
        self.last_model = model
        return self._result_text, [], None

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _FailThenSuccessAdapter(RuntimeAdapter):
    """Adapter that fails on the first N calls then succeeds."""

    def __init__(
        self,
        *,
        fail_count: int = 1,
        error: Exception | None = None,
        result_text: str = "fallback-ok",
    ) -> None:
        self._fail_count = fail_count
        self._error = error or RuntimeError("connection refused: provider unavailable")
        self._result_text = result_text
        self.invoke_calls = 0

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
        self.invoke_calls += 1
        if self.invoke_calls <= self._fail_count:
            raise self._error
        return self._result_text, [], None

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _AlwaysFailAdapter(RuntimeAdapter):
    """Adapter that always raises."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error or RuntimeError("connection refused: provider unavailable")
        self.invoke_calls = 0

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
        self.invoke_calls += 1
        raise self._error

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


# ---------------------------------------------------------------------------
# Common patch context: sessions + resolve_model_with_effective_tier + quota
# ---------------------------------------------------------------------------


def _catalog_primary(
    model: str = "primary-model",
    runtime_type: str = DEFAULT_RUNTIME_TYPE,
    tier: str = "workhorse",
) -> tuple:
    return (runtime_type, model, [], _PRIMARY_CATALOG_ID, 1800, tier)


def _catalog_fallback(
    model: str = "fallback-model",
    runtime_type: str = DEFAULT_RUNTIME_TYPE,
    tier: str = "workhorse",
) -> tuple:
    return (runtime_type, model, [], _FALLBACK_CATALOG_ID, 1800)


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestAC1EffectiveTierFromInitialResolution:
    """AC1: Initial resolution establishes the effective tier for failover."""

    async def test_effective_tier_passed_to_next_same_tier_candidate(self, tmp_path: Path) -> None:
        """When a runtime failure is eligible, next_same_tier_candidate is called with the
        effective tier from initial resolution."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _AlwaysFailAdapter(error=RuntimeError("connection refused: provider unavailable"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(tier="cheap"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,  # exhausted
            ) as mock_next,
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="provider_auth_error: test"),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        # next_same_tier_candidate was called with the effective tier "cheap"
        mock_next.assert_called_once()
        call_args = mock_next.call_args
        assert call_args[0][2] == "cheap"  # effective_tier argument
        # Result is a failure (no candidates)
        assert result.success is False


class TestAC2QuotaSkip:
    """AC2: Quota-exhausted primary skips; hard block when no candidates remain."""

    async def test_quota_skip_to_next_candidate_succeeds(self, tmp_path: Path) -> None:
        """Primary quota exhausted → skip to next candidate → success on fallback."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _SuccessAdapter(result_text="fallback-ran")

        # First call (primary) → denied; second call (fallback) → allowed so the
        # quota-skip loop exits and the fallback is invoked.
        _quota_responses = iter([_QUOTA_DENIED_24H, _QUOTA_ALLOWED])

        async def _quota_side_effect(*_a: Any) -> Any:
            return next(_quota_responses)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                side_effect=_quota_side_effect,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "fallback-model",
                    [],
                    _FALLBACK_CATALOG_ID,
                    1800,
                ),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.output == "fallback-ran"
        # AC5: final result model is the fallback model
        assert result.model == "fallback-model"

    async def test_quota_exhausted_hard_block_when_no_candidates(self, tmp_path: Path) -> None:
        """Primary quota exhausted and no same-tier candidates → hard block."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _SuccessAdapter()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_DENIED_24H,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is False
        assert result.error is not None
        assert "24h" in result.error
        # Adapter never invoked
        assert adapter.invoke_calls == 0

    async def test_attempted_ids_tracked_across_quota_skips(self, tmp_path: Path) -> None:
        """Each quota-skipped catalog ID is excluded from subsequent next_same_tier_candidate calls."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _SuccessAdapter(result_text="third-ran")

        _THIRD_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000099")

        # quota denied → skip primary, get second; quota denied → skip second, get third; quota OK
        quota_responses = [_QUOTA_DENIED_24H, _QUOTA_DENIED_24H, _QUOTA_ALLOWED]
        quota_iter = iter(quota_responses)

        async def _quota_side_effect(pool, catalog_entry_id):
            return next(quota_iter)

        next_candidates = [
            (DEFAULT_RUNTIME_TYPE, "second-model", [], _FALLBACK_CATALOG_ID, 1800),
            (DEFAULT_RUNTIME_TYPE, "third-model", [], _THIRD_ID, 1800),
        ]
        next_iter = iter(next_candidates)

        async def _next_side_effect(pool, butler_name, tier, attempted_ids):
            return next(next_iter)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                side_effect=_quota_side_effect,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                side_effect=_next_side_effect,
            ) as mock_next,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.model == "third-model"
        # next called twice: after primary skip and after second skip
        assert mock_next.call_count == 2
        # Second call must include both primary and second catalog IDs in attempted_ids
        second_call_attempted = mock_next.call_args_list[1][0][3]
        assert _PRIMARY_CATALOG_ID in second_call_attempted
        assert _FALLBACK_CATALOG_ID in second_call_attempted


class TestAC3RuntimeFailureRetry:
    """AC3: Eligible runtime failures retry same-tier candidates."""

    async def test_eligible_failure_retries_next_candidate(self, tmp_path: Path) -> None:
        """Runtime failure with eligible classifier → retry on next same-tier candidate → success."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        # Adapter fails once (eligible error) then succeeds on second call
        adapter = _FailThenSuccessAdapter(
            fail_count=1,
            error=RuntimeError("connection refused: provider unavailable"),
            result_text="fallback-succeeded",
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "fallback-model",
                    [],
                    _FALLBACK_CATALOG_ID,
                    1800,
                ),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.output == "fallback-succeeded"
        # AC5: result model is the fallback
        assert result.model == "fallback-model"
        # Adapter was invoked twice (once for primary, once for fallback)
        assert adapter.invoke_calls == 2

    async def test_eligible_failure_no_candidates_is_terminal(self, tmp_path: Path) -> None:
        """Eligible failure but no more candidates → terminal failure."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _AlwaysFailAdapter(error=RuntimeError("connection refused: provider unavailable"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is False
        assert result.error is not None

    async def test_attempted_ids_exclude_primary_on_retry(self, tmp_path: Path) -> None:
        """The primary catalog ID is in attempted_ids when next_same_tier_candidate is called."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _AlwaysFailAdapter(error=RuntimeError("connection refused: provider unavailable"))
        next_called_with_ids: list[list] = []

        async def _next_side_effect(pool, butler_name, tier, attempted_ids):
            next_called_with_ids.append(list(attempted_ids))
            return None  # exhausted after first call

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                side_effect=_next_side_effect,
            ),
        ):
            mock_create.return_value = _SESSION_ID
            await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert len(next_called_with_ids) == 1
        # Primary catalog ID is excluded
        assert _PRIMARY_CATALOG_ID in next_called_with_ids[0]


class TestAC4SuppressedFailover:
    """AC4: Tool calls or ineligible classifier → no retry, preserve terminal behavior."""

    async def test_ineligible_classifier_no_retry(self, tmp_path: Path) -> None:
        """Classifier returns eligible=False → no retry, session fails as-is."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _AlwaysFailAdapter(error=RuntimeError("degenerate_tool_loop detected"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        # Failover suppressed — next_same_tier_candidate never called
        mock_next.assert_not_called()
        assert result.success is False
        assert "degenerate_tool_loop" in (result.error or "")

    async def test_tool_calls_captured_suppresses_retry(self, tmp_path: Path) -> None:
        """Captured MCP tool calls suppress failover regardless of error type."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        error = RuntimeError("connection refused: provider unavailable")
        tool_call = {"name": "memory_store", "id": "tc1", "input": {}}

        class _FailWithToolCallsAdapter(RuntimeAdapter):
            """Fails but has a recorded tool call in the runtime session capture."""

            @property
            def binary_name(self) -> str:
                return "mock"

            async def invoke(self, *args, **kwargs):
                raise error

            async def reset(self) -> None:
                pass

            def build_config_file(self, mcp_servers, tmp_dir):
                import json

                p = tmp_dir / "cfg.json"
                p.write_text(json.dumps({"mcpServers": mcp_servers}))
                return p

            def parse_system_prompt_file(self, config_dir):
                return ""

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
            # Inject a tool call into the runtime session capture BEFORE the invocation fails
            patch(
                "butlers.core.spawner.consume_runtime_session_tool_calls",
                return_value=[tool_call],
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailWithToolCallsAdapter(),
            ).trigger("hello", "tick")

        # Tool calls captured → no retry
        mock_next.assert_not_called()
        assert result.success is False

    async def test_ineligible_error_class_no_retry(self, tmp_path: Path) -> None:
        """Default-closed classifier (unknown error) → no retry."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        class _WeirdError(Exception):
            pass

        adapter = _AlwaysFailAdapter(error=_WeirdError("something unexpected happened"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        mock_next.assert_not_called()
        assert result.success is False


class TestAC5SessionModelReflectsFallback:
    """AC5: SpawnerResult.model and session row model reflect the successful fallback."""

    async def test_result_model_is_fallback_after_quota_skip(self, tmp_path: Path) -> None:
        """After quota-skip failover, result.model is the fallback model, not the primary."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _SuccessAdapter(result_text="ok")

        # First call (primary) → denied; second call (fallback) → allowed.
        _quota_responses = iter([_QUOTA_DENIED_24H, _QUOTA_ALLOWED])

        async def _quota_side_effect(*_a: Any) -> Any:
            return next(_quota_responses)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                side_effect=_quota_side_effect,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "fallback-model",
                    [],
                    _FALLBACK_CATALOG_ID,
                    1800,
                ),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.model == "fallback-model"

    async def test_session_db_model_updated_after_failover(self, tmp_path: Path) -> None:
        """After failover success, the session row's model is updated to the fallback model."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _FailThenSuccessAdapter(
            fail_count=1,
            error=RuntimeError("connection refused: provider unavailable"),
            result_text="ok",
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "fallback-model",
                    [],
                    _FALLBACK_CATALOG_ID,
                    1800,
                ),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.model == "fallback-model"

        # The pool.execute should have been called with an UPDATE sessions SET model = ...
        execute_calls = [str(c) for c in mock_pool.execute.call_args_list]
        model_update_calls = [c for c in execute_calls if "UPDATE sessions SET model" in c]
        assert len(model_update_calls) == 1

    async def test_no_model_update_when_no_failover_occurred(self, tmp_path: Path) -> None:
        """When no failover occurs, the session row's model is NOT updated."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _SuccessAdapter(result_text="ok")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.model == "primary-model"

        # No UPDATE sessions SET model should have been called
        execute_calls = [str(c) for c in mock_pool.execute.call_args_list]
        model_update_calls = [c for c in execute_calls if "UPDATE sessions SET model" in c]
        assert len(model_update_calls) == 0


class TestOneLogicalSessionOutcome:
    """One logical session row persisted even across multiple failover attempts."""

    async def test_only_one_session_created_across_failover(self, tmp_path: Path) -> None:
        """session_create is called ONCE regardless of how many failover attempts occur."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _FailThenSuccessAdapter(
            fail_count=2,
            error=RuntimeError("connection refused: provider unavailable"),
            result_text="third-succeeded",
        )

        _THIRD_ID = uuid.UUID("dddddddd-0000-0000-0000-000000000004")

        next_candidates = [
            (DEFAULT_RUNTIME_TYPE, "second-model", [], _FALLBACK_CATALOG_ID, 1800),
            (DEFAULT_RUNTIME_TYPE, "third-model", [], _THIRD_ID, 1800),
        ]
        next_iter = iter(next_candidates)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                side_effect=lambda *a, **k: next(next_iter),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        # Only one session_create call — one logical session row
        mock_create.assert_called_once()
        # Only one session_complete call — final outcome
        mock_complete.assert_called_once()
        assert result.success is True
        assert result.model == "third-model"


class TestFailoverMetricsEmission:
    """Failover metrics are emitted for attempts, suppressions, and exhaustions."""

    async def test_failover_attempt_metric_emitted_on_quota_skip(self, tmp_path: Path) -> None:
        """record_failover_attempt metric emitted when quota skip transitions to next candidate."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _SuccessAdapter(result_text="ok")

        # First call (primary) → denied; second call (fallback) → allowed.
        _quota_responses = iter([_QUOTA_DENIED_24H, _QUOTA_ALLOWED])

        async def _quota_side_effect(*_a: Any) -> Any:
            return next(_quota_responses)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                side_effect=_quota_side_effect,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "fallback-model",
                    [],
                    _FALLBACK_CATALOG_ID,
                    1800,
                ),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)
            with patch.object(spawner._metrics, "record_failover_attempt") as mock_metric_attempt:
                result = await spawner.trigger("hello", "tick")

        assert result.success is True
        mock_metric_attempt.assert_called_once()
        call_kwargs = mock_metric_attempt.call_args[1]
        assert call_kwargs["from_model"] == "primary-model"
        assert call_kwargs["to_model"] == "fallback-model"
        assert call_kwargs["reason"] == "quota_exhausted"

    async def test_failover_suppressed_metric_emitted_on_ineligible(self, tmp_path: Path) -> None:
        """record_failover_suppressed metric emitted when classifier rejects failover."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _AlwaysFailAdapter(error=RuntimeError("degenerate_tool_loop detected"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch("butlers.core.spawner.next_same_tier_candidate", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)
            with patch.object(spawner._metrics, "record_failover_suppressed") as mock_suppressed:
                result = await spawner.trigger("hello", "tick")

        assert result.success is False
        mock_suppressed.assert_called_once()

    async def test_failover_exhausted_metric_emitted_when_all_candidates_gone(
        self, tmp_path: Path
    ) -> None:
        """record_failover_exhausted metric emitted when all same-tier candidates exhausted."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _AlwaysFailAdapter(error=RuntimeError("connection refused: provider unavailable"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model", tier="workhorse"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)
            with patch.object(spawner._metrics, "record_failover_exhausted") as mock_exhausted:
                result = await spawner.trigger("hello", "tick")

        assert result.success is False
        mock_exhausted.assert_called_once()
        call_kwargs = mock_exhausted.call_args[1]
        assert call_kwargs["tier"] == "workhorse"

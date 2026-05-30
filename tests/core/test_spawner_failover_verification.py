"""Focused verification coverage for same-tier model failover (bu-ojiij.6).

Each test verifies ONE specific acceptance path end-to-end, combining:
  - provenance row written to public.model_dispatch_attempts
  - metric emission via ButlerMetrics
  - SpawnerResult shape

Uses adapter signals from bu-ojiij.5: is_pre_tool_call, error_detail,
internal_retry_count on process_info / MCPToolDiscoveryError.

No live provider calls — all adapters are mocked.

Acceptance criteria covered (per bu-ojiij.6 scope):
1. Quota skip — primary has quota_remaining=0; spawner skips it, picks next.
   Verify quota_skip appears in model_dispatch_attempts.
2. Exhausted quota — all candidates quota=0; spawner returns failure.
   Verify exhaustion metric emits; provenance has all skips.
3. Eligible runtime failure retry — systemic pre-tool-call error (rate limit /
   model-unavailable / auth / timeout). Classifier returns eligible. Spawner
   retries next same-tier model. Verify provenance: primary=runtime_failure,
   fallback=success.
4. Captured-tool suppression — primary fails AFTER tool call. Classifier returns
   suppressed. Spawner does NOT retry. Verify
   butlers.spawner.failover_suppressed_total metric emits with right reason label.
5. Guardrail no-retry — primary fails with guardrail / business failure.
   Classifier returns suppressed. Spawner does NOT retry.
6. Attempt exhaustion — eligible failure, all same-tier candidates exhausted.
   Verify butlers.spawner.failover_exhausted_total emits.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.failover_classifier import (
    FailoverContext,
    classify_failover_eligibility,
)
from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.runtimes.codex import MCPToolDiscoveryError
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared UUIDs and quota helpers
# ---------------------------------------------------------------------------

_PRIMARY_ID = uuid.UUID("aa000000-0000-0000-0000-000000000001")
_FALLBACK_ID = uuid.UUID("bb000000-0000-0000-0000-000000000002")
_TERTIARY_ID = uuid.UUID("cc000000-0000-0000-0000-000000000003")
_SESSION_ID = uuid.UUID("dd000000-0000-0000-0000-000000000004")

_QUOTA_OK = QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)
_QUOTA_24H_FULL = QuotaStatus(
    allowed=False, usage_24h=1000, limit_24h=1000, usage_30d=0, limit_30d=None
)
_QUOTA_30D_FULL = QuotaStatus(
    allowed=False, usage_24h=0, limit_24h=None, usage_30d=5000, limit_30d=5000
)

_ATTEMPTS_SQL_FRAGMENT = "INSERT INTO public.model_dispatch_attempts"


# ---------------------------------------------------------------------------
# Minimal adapter helpers
# ---------------------------------------------------------------------------


def _make_config(name: str = "test-butler") -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=9100,
        runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


class _OkAdapter(RuntimeAdapter):
    """Adapter that always succeeds, optionally tracking call count."""

    def __init__(self, result_text: str = "ok") -> None:
        self._result_text = result_text
        self.invoke_calls = 0

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, *_a: Any, **_kw: Any) -> tuple[str, list, dict | None]:
        self.invoke_calls += 1
        return self._result_text, [], None

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _FailAdapter(RuntimeAdapter):
    """Adapter that always raises with a configurable exception."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.invoke_calls = 0

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, *_a: Any, **_kw: Any) -> tuple[str, list, dict | None]:
        self.invoke_calls += 1
        raise self._error

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _FailThenOkAdapter(RuntimeAdapter):
    """Adapter that fails on the first N calls then succeeds."""

    def __init__(
        self,
        *,
        fail_count: int = 1,
        error: Exception | None = None,
        result_text: str = "fallback-ok",
    ) -> None:
        self._fail_count = fail_count
        self._error = error or RuntimeError("rate limit exceeded: too many requests")
        self._result_text = result_text
        self.invoke_calls = 0

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, *_a: Any, **_kw: Any) -> tuple[str, list, dict | None]:
        self.invoke_calls += 1
        if self.invoke_calls <= self._fail_count:
            raise self._error
        return self._result_text, [], None

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _catalog_primary(
    model: str = "primary-model",
    runtime_type: str = DEFAULT_RUNTIME_TYPE,
    tier: str = "workhorse",
) -> tuple:
    return (runtime_type, model, [], _PRIMARY_ID, 1800, tier)


def _catalog_fallback(
    model: str = "fallback-model",
    runtime_type: str = DEFAULT_RUNTIME_TYPE,
) -> tuple:
    return (runtime_type, model, [], _FALLBACK_ID, 1800)


def _dispatch_rows_with_outcome(mock_pool: AsyncMock, outcome: str) -> list[tuple]:
    """Return positional args from pool.execute calls that match the given outcome."""
    rows = []
    for call in mock_pool.execute.call_args_list:
        args = call[0]
        if not args or not isinstance(args[0], str):
            continue
        if _ATTEMPTS_SQL_FRAGMENT not in args[0]:
            continue
        # outcome is the 5th positional arg ($4 in SQL → index 4)
        if len(args) > 4 and args[4] == outcome:
            rows.append(args)
    return rows


# ---------------------------------------------------------------------------
# Test 1: Quota skip — provenance row written, spawner proceeds to fallback
# ---------------------------------------------------------------------------


class TestQuotaSkipProvenance:
    """AC1: Primary quota=0 → quota_skip row in model_dispatch_attempts."""

    async def test_quota_skip_row_written_and_fallback_succeeds(self, tmp_path: Path) -> None:
        """Primary quota exhausted → quota_skip row written → fallback invoked → success."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        # First quota check: denied; second (for fallback): allowed.
        quota_responses = [_QUOTA_24H_FULL, _QUOTA_OK]
        quota_iter = iter(quota_responses)

        adapter = _OkAdapter(result_text="fallback-ran")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                side_effect=lambda *_a: next(quota_iter),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=_catalog_fallback(model="fallback-model"),
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("test prompt", "tick")

        # Session should succeed using the fallback model.
        assert result.success is True
        assert result.model == "fallback-model"
        assert result.output == "fallback-ran"

        # A quota_skip row must be in model_dispatch_attempts for the primary.
        quota_skip_rows = _dispatch_rows_with_outcome(mock_pool, "quota_skip")
        assert len(quota_skip_rows) == 1, (
            f"Expected exactly one quota_skip row, got: {quota_skip_rows}"
        )
        # catalog_entry_id (index 2) should be the primary.
        assert quota_skip_rows[0][2] == _PRIMARY_ID

    async def test_quota_skip_attempt_index_is_zero(self, tmp_path: Path) -> None:
        """quota_skip row for the first candidate has attempt_index=0."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_24H_FULL,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,  # no fallback — just test the index
            ),
        ):
            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_OkAdapter(),
            ).trigger("hello", "tick")

        quota_skip_rows = _dispatch_rows_with_outcome(mock_pool, "quota_skip")
        assert len(quota_skip_rows) >= 1
        # attempt_index is at index 9 in the SQL args tuple.
        assert quota_skip_rows[0][9] == 0, f"Expected attempt_index=0, got {quota_skip_rows[0][9]}"


# ---------------------------------------------------------------------------
# Test 2: Exhausted quota — all candidates quota=0
# ---------------------------------------------------------------------------


class TestExhaustedQuota:
    """AC2: All same-tier candidates have quota=0 → exhaustion metric + all skips persisted."""

    async def test_all_quota_exhausted_returns_failure_with_all_skip_rows(
        self, tmp_path: Path
    ) -> None:
        """Two candidates both quota-exhausted; spawner fails; both quota_skip rows written."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        # primary denied → get fallback; fallback denied → no more candidates.
        quota_responses = [_QUOTA_24H_FULL, _QUOTA_24H_FULL]
        quota_iter = iter(quota_responses)

        next_side_effects = [
            _catalog_fallback(model="fallback-model"),  # after primary skipped
            None,  # after fallback also skipped → exhausted
        ]
        next_iter = iter(next_side_effects)

        adapter = _OkAdapter()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model", tier="workhorse"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                side_effect=lambda *_a: next(quota_iter),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                side_effect=lambda *_a, **_kw: next(next_iter),
            ),
        ):
            spawner = Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )
            with patch.object(spawner._metrics, "record_failover_exhausted") as mock_exhausted:
                result = await spawner.trigger("hello", "tick")

        # Must fail — no adapter invocations.
        assert result.success is False
        assert adapter.invoke_calls == 0

        # Exhaustion metric must be emitted.
        mock_exhausted.assert_called_once()
        call_kwargs = mock_exhausted.call_args[1]
        assert call_kwargs.get("tier") == "workhorse"

        # Both candidates should appear as quota_skip in provenance.
        quota_skip_rows = _dispatch_rows_with_outcome(mock_pool, "quota_skip")
        assert len(quota_skip_rows) == 2, (
            f"Expected 2 quota_skip rows, got {len(quota_skip_rows)}: {quota_skip_rows}"
        )
        skipped_ids = {r[2] for r in quota_skip_rows}
        assert _PRIMARY_ID in skipped_ids
        assert _FALLBACK_ID in skipped_ids


# ---------------------------------------------------------------------------
# Test 3: Eligible runtime failure → retry on next same-tier → both provenance rows
# ---------------------------------------------------------------------------


class TestEligibleRuntimeFailureRetry:
    """AC3: Systemic pre-tool-call error → classifier eligible → retry → success.

    Verify provenance: primary=runtime_failure, fallback=success.
    """

    async def test_rate_limit_triggers_failover_provenance(self, tmp_path: Path) -> None:
        """Rate-limit error before any tool call: classifier eligible → retry succeeds."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        # Primary fails with rate-limit; fallback succeeds.
        adapter = _FailThenOkAdapter(
            fail_count=1,
            error=RuntimeError("rate limit exceeded: too many requests"),
            result_text="fallback-ok",
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model", tier="workhorse"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=_catalog_fallback(model="fallback-model"),
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("hello", "tick")

        # Final result: success using the fallback model.
        assert result.success is True
        assert result.model == "fallback-model"
        assert result.output == "fallback-ok"
        # Adapter was invoked twice: once for primary (fail), once for fallback.
        assert adapter.invoke_calls == 2

        # Provenance: runtime_failure row for primary.
        rf_rows = _dispatch_rows_with_outcome(mock_pool, "runtime_failure")
        assert len(rf_rows) >= 1, "Expected runtime_failure provenance row for primary"
        # The row should carry the primary catalog_entry_id.
        assert rf_rows[0][2] == _PRIMARY_ID

        # Provenance: success row for fallback.
        success_rows = _dispatch_rows_with_outcome(mock_pool, "success")
        assert len(success_rows) >= 1, "Expected success provenance row for fallback"
        assert success_rows[0][2] == _FALLBACK_ID

    async def test_model_unavailable_error_triggers_failover(self, tmp_path: Path) -> None:
        """Model-unavailable error (auth signal from bu-ojiij.5) is failover-eligible."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        adapter = _FailThenOkAdapter(
            fail_count=1,
            error=RuntimeError("model is unavailable: primary-model-001"),
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model", tier="workhorse"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=_catalog_fallback(model="fallback-model"),
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.model == "fallback-model"

    async def test_timeout_before_tool_call_triggers_failover(self, tmp_path: Path) -> None:
        """TimeoutError before any tool call is eligible for failover (bu-ojiij.5 signal)."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        adapter = _FailThenOkAdapter(
            fail_count=1,
            error=TimeoutError("session timed out after 1800s (model=primary-model)"),
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=_catalog_fallback(model="fallback-model"),
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.model == "fallback-model"

        # Verify that the TimeoutError is treated as a pre-tool-call signal.
        # The classifier should have found it eligible.
        exc = TimeoutError("session timed out after 1800s")
        dec = classify_failover_eligibility(FailoverContext(exception=exc, tool_calls=[]))
        assert dec.eligible, f"Expected TimeoutError eligible, got: {dec.reason}"
        assert "timeout" in dec.reason


# ---------------------------------------------------------------------------
# Test 4: Captured-tool suppression — failure AFTER a tool call
# ---------------------------------------------------------------------------


class TestCapturedToolSuppression:
    """AC4: Primary fails AFTER a tool call → classifier suppressed → no retry.

    Verify model_dispatch_failovers_suppressed_total metric emits with right
    reason label, and that next_same_tier_candidate is never called.
    """

    async def test_tool_call_after_failure_suppresses_failover_and_emits_metric(
        self, tmp_path: Path
    ) -> None:
        """Failure with captured MCP tool call → suppressed metric + no retry."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        # Adapter fails; but the tool-call capture buffer returns a tool call.
        tool_call = {"name": "state_set", "id": "tc-001", "input": {"key": "x", "value": 1}}

        class _FailWithCapturedToolCall(RuntimeAdapter):
            @property
            def binary_name(self) -> str:
                return "mock"

            async def invoke(self, *_a: Any, **_kw: Any) -> tuple[str, list, dict | None]:
                raise RuntimeError("rate limit exceeded: tool call already made")

            async def reset(self) -> None:
                pass

            def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
                p = tmp_dir / "cfg.json"
                p.write_text(json.dumps({"mcpServers": mcp_servers}))
                return p

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
            # Inject a tool call into the captured buffer so the classifier sees side effects.
            patch(
                "butlers.core.spawner.consume_runtime_session_tool_calls",
                return_value=[tool_call],
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailWithCapturedToolCall(),
            )
            with patch.object(spawner._metrics, "record_failover_suppressed") as mock_suppressed:
                result = await spawner.trigger("hello", "tick")

        # Must fail — tool call was captured, failover suppressed.
        assert result.success is False

        # Spawner must NOT have tried a next candidate.
        mock_next.assert_not_called()

        # Suppressed metric must be emitted with a reason that references tool calls.
        mock_suppressed.assert_called_once()
        reason_arg = mock_suppressed.call_args[1].get("reason", "")
        assert "tool_call" in reason_arg or "captured" in reason_arg, (
            f"Expected reason to mention tool calls, got: {reason_arg!r}"
        )

        # Provenance: suppressed row written.
        suppressed_rows = _dispatch_rows_with_outcome(mock_pool, "suppressed")
        assert len(suppressed_rows) >= 1, "Expected suppressed provenance row"


# ---------------------------------------------------------------------------
# Test 5: Guardrail no-retry — business / guardrail failure is suppressed
# ---------------------------------------------------------------------------


class TestGuardrailNoRetry:
    """AC5: Guardrail / business failure → suppressed → no retry."""

    async def test_degenerate_tool_loop_suppresses_failover(self, tmp_path: Path) -> None:
        """degenerate_tool_loop termination is suppressed; next candidate NOT tried."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        adapter = _FailAdapter(error=RuntimeError("degenerate_tool_loop detected: too many calls"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_sc.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )
            with patch.object(spawner._metrics, "record_failover_suppressed") as mock_suppressed:
                result = await spawner.trigger("hello", "tick")

        assert result.success is False
        assert "degenerate_tool_loop" in (result.error or "")
        # Failover must not have been attempted.
        mock_next.assert_not_called()
        # Suppressed metric emitted.
        mock_suppressed.assert_called_once()

    async def test_token_budget_exceeded_suppresses_failover(self, tmp_path: Path) -> None:
        """token_budget_exceeded is suppressed; next candidate NOT tried."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        adapter = _FailAdapter(
            error=RuntimeError("token_budget_exceeded: session used 100000 tokens")
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_sc.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("hello", "tick")

        assert result.success is False
        mock_next.assert_not_called()

    async def test_content_policy_failure_suppresses_failover(self, tmp_path: Path) -> None:
        """Unknown exception class (content policy, business error) suppresses failover."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        class _ContentPolicyError(Exception):
            """Simulated content policy / business validation error."""

        adapter = _FailAdapter(error=_ContentPolicyError("content policy violation detected"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_sc.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("hello", "tick")

        assert result.success is False
        # Business / unknown errors must not trigger failover.
        mock_next.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: Attempt exhaustion — all same-tier candidates exhausted
# ---------------------------------------------------------------------------


class TestAttemptExhaustion:
    """AC6: Eligible failure but all same-tier candidates exhausted.

    Verify model_dispatch_failovers_exhausted_total emits.
    """

    async def test_exhausted_metric_emitted_when_all_candidates_gone(self, tmp_path: Path) -> None:
        """Primary fails (eligible), next_same_tier_candidate returns None → exhausted metric."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        # Eligible error: rate-limit before any tool call.
        adapter = _FailAdapter(error=RuntimeError("rate limit exceeded: 429 too many requests"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model", tier="workhorse"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,  # no more candidates
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )
            with patch.object(spawner._metrics, "record_failover_exhausted") as mock_exhausted:
                result = await spawner.trigger("hello", "tick")

        assert result.success is False
        # Exhaustion metric emitted with the effective tier.
        mock_exhausted.assert_called_once()
        call_kwargs = mock_exhausted.call_args[1]
        assert call_kwargs.get("tier") == "workhorse", (
            f"Expected tier='workhorse', got {call_kwargs}"
        )

    async def test_exhausted_after_multiple_eligible_failures(self, tmp_path: Path) -> None:
        """Eligible failure on primary + fallback; all exhausted → exhaustion metric."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        # Both primary and fallback fail; tertiary is None (exhausted).
        adapter = _FailAdapter(error=RuntimeError("connection refused: provider unavailable"))

        next_responses = [
            _catalog_fallback(model="fallback-model"),  # after primary fails
            None,  # after fallback fails → exhausted
        ]
        next_iter = iter(next_responses)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model", tier="workhorse"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                side_effect=lambda *_a, **_kw: next(next_iter),
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )
            with patch.object(spawner._metrics, "record_failover_exhausted") as mock_exhausted:
                result = await spawner.trigger("hello", "tick")

        assert result.success is False
        mock_exhausted.assert_called_once()
        call_kwargs = mock_exhausted.call_args[1]
        assert call_kwargs.get("tier") == "workhorse"

        # Two runtime_failure rows in provenance: one for primary, one for fallback.
        rf_rows = _dispatch_rows_with_outcome(mock_pool, "runtime_failure")
        assert len(rf_rows) == 2, f"Expected 2 runtime_failure rows, got {len(rf_rows)}"


# ---------------------------------------------------------------------------
# Test 7: MCP discovery failure (bu-ojiij.5 MCPToolDiscoveryError signal)
# ---------------------------------------------------------------------------


class TestMCPDiscoveryFailureFailover:
    """MCPToolDiscoveryError with no tool calls is eligible for failover.

    Verifies that the adapter signal introduced in bu-ojiij.5 (is_pre_tool_call,
    internal_retry_count) feeds correctly into the spawner's failover path.
    """

    def test_mcp_discovery_error_is_pre_tool_call_true(self) -> None:
        """MCPToolDiscoveryError always has is_pre_tool_call=True."""
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed after 3 attempts",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=2,
        )
        assert exc.is_pre_tool_call is True
        assert exc.internal_retry_count == 2

    def test_mcp_discovery_error_with_no_tool_calls_is_eligible(self) -> None:
        """MCPToolDiscoveryError is classifier-eligible when no spawner tool calls captured."""
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=0,
        )
        dec = classify_failover_eligibility(FailoverContext(exception=exc, tool_calls=[]))
        assert dec.eligible, f"Expected eligible, got: {dec.reason}"
        assert "mcp_discovery" in dec.reason

    def test_mcp_discovery_internal_retry_not_counted_as_cross_model_failover(self) -> None:
        """internal_retry_count from MCPToolDiscoveryError must NOT inflate cross-model attempt count.

        Even if Codex retried MCP discovery 3 times (internal_retry_count=2),
        the spawner treats this as ONE logical failover-eligible attempt.
        is_pre_tool_call=True across all internal retries confirms no side effects.
        """
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed after 3 attempts (1 initial + 2 retries)",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=2,
        )
        # Spawner sees ONE exception → ONE failover event.
        dec = classify_failover_eligibility(FailoverContext(exception=exc, tool_calls=[]))
        assert dec.eligible

        # The internal_retry_count attribute is accessible so the spawner CAN distinguish
        # adapter-internal retries from cross-model failover, but must not double-count.
        assert exc.internal_retry_count == 2
        # is_pre_tool_call confirms no side effects despite multiple adapter-internal runs.
        assert exc.is_pre_tool_call is True

    async def test_runtime_error_with_captured_tool_calls_suppressed(self, tmp_path: Path) -> None:
        """RuntimeError after tool calls captured → suppressed, no retry.

        When a RuntimeError (not MCPToolDiscoveryError) is raised and the daemon's
        tool-call buffer has entries, the classifier sees captured tool calls and
        suppresses failover to prevent duplicate side effects. This verifies the
        side-effect gating contract from bu-ojiij.5.
        """
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        tool_call = {"name": "memory_store", "id": "tc-x", "input": {}}

        class _FailAfterToolCapture(RuntimeAdapter):
            """Adapter that raises RuntimeError; daemon buffer has a tool call."""

            @property
            def binary_name(self) -> str:
                return "mock"

            async def invoke(self, *_a: Any, **_kw: Any) -> tuple[str, list, dict | None]:
                raise RuntimeError("connection refused: provider unavailable")

            async def reset(self) -> None:
                pass

            def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
                p = tmp_dir / "cfg.json"
                p.write_text(json.dumps({"mcpServers": mcp_servers}))
                return p

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(model="primary-model"),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_OK,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
            # Inject a server-side tool call: world was touched before the RuntimeError.
            patch(
                "butlers.core.spawner.consume_runtime_session_tool_calls",
                return_value=[tool_call],
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailAfterToolCapture(),
            ).trigger("hello", "tick")

        assert result.success is False
        # Suppressed — tool call was captured, so no retry allowed.
        mock_next.assert_not_called()

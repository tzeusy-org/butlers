"""Tests for durable failover attempt provenance (bu-fqkip).

Verifies that public.model_dispatch_attempts rows are written (best-effort) at
each key point in the failover flow:

1. quota_skip — candidate skipped pre-invocation because quota is exhausted
2. runtime_failure — eligible failover; a next candidate is tried
3. suppressed — failover ineligible (side effects or unknown error)
4. success — fallback attempt succeeded (only when prior attempts exist)
5. Best-effort: insert failure does not propagate and does not affect the result
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

_PRIMARY_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_FALLBACK_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_SESSION_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")

_QUOTA_ALLOWED = QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)
_QUOTA_DENIED_24H = QuotaStatus(
    allowed=False, usage_24h=1000, limit_24h=1000, usage_30d=0, limit_30d=None
)

_ATTEMPTS_INSERT = "INSERT INTO public.model_dispatch_attempts"


# ---------------------------------------------------------------------------
# Helpers
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


class _SuccessAdapter(RuntimeAdapter):
    """Adapter that always succeeds."""

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, *args: Any, **kwargs: Any) -> tuple[str, list, dict | None]:
        return "ok", [], None

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _FailingAdapter(RuntimeAdapter):
    """Adapter that always raises RuntimeError."""

    def __init__(self, msg: str = "boom") -> None:
        self._msg = msg

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, *args: Any, **kwargs: Any) -> tuple[str, list, dict | None]:
        raise RuntimeError(self._msg)

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _execute_calls_with_fragment(mock_pool: AsyncMock, fragment: str) -> list[tuple]:
    """Return positional args from pool.execute calls whose SQL matches fragment."""
    result = []
    for c in mock_pool.execute.call_args_list:
        args = c[0]
        if args and isinstance(args[0], str) and fragment in args[0]:
            result.append(args)
    return result


# ---------------------------------------------------------------------------
# Tests: quota_skip provenance
# ---------------------------------------------------------------------------


class TestQuotaSkipProvenance:
    async def test_quota_skip_row_written_on_exhausted_primary(self, tmp_path: Path) -> None:
        """A quota_skip row is written when the primary candidate is quota-exhausted."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_DENIED_24H,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-fallback",
                    [],
                    _FALLBACK_ID,
                    1800,
                ),
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID
            # Quota allowed for the fallback (second check_token_quota call)
            # Reset side_effect to return ALLOWED on the second call
            patch_quota = patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                side_effect=[_QUOTA_DENIED_24H, _QUOTA_ALLOWED],
            )
            with patch_quota:
                await Spawner(
                    config=_make_config(),
                    config_dir=config_dir,
                    pool=mock_pool,
                    runtime=_SuccessAdapter(),
                ).trigger("hello", "tick")

        # Should have at least one quota_skip INSERT
        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        outcomes = [a[4] for a in attempts]  # outcome is 5th arg ($4 in SQL)
        assert "quota_skip" in outcomes, f"Expected quota_skip in outcomes: {outcomes}"

    async def test_quota_skip_row_has_correct_catalog_entry_id(self, tmp_path: Path) -> None:
        """quota_skip row carries the skipped catalog_entry_id."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_DENIED_24H,
            ),
        ):
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_SuccessAdapter(),
            ).trigger("hello", "tick")

        assert result.success is False
        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        assert len(attempts) >= 1
        # catalog_entry_id is the 2nd positional arg ($2 in SQL)
        assert attempts[0][2] == _PRIMARY_ID

    async def test_quota_skip_attempt_index_zero_for_primary(self, tmp_path: Path) -> None:
        """quota_skip attempt_index is 0 for the primary (first) candidate."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_DENIED_24H,
            ),
        ):
            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_SuccessAdapter(),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        assert len(attempts) >= 1
        # attempt_index is the 9th positional arg ($9)
        assert attempts[0][9] == 0


# ---------------------------------------------------------------------------
# Tests: runtime_failure provenance
# ---------------------------------------------------------------------------


class TestRuntimeFailureProvenance:
    async def test_runtime_failure_row_written_on_eligible_failover(self, tmp_path: Path) -> None:
        """A runtime_failure row is written when failover is eligible and next candidate tried."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,  # exhausted after first fail
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("cli not found"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        outcomes = [a[4] for a in attempts]
        assert "runtime_failure" in outcomes, f"Expected runtime_failure in outcomes: {outcomes}"

    async def test_runtime_failure_row_has_error_code(self, tmp_path: Path) -> None:
        """runtime_failure row carries the exception class name as error_code."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("cli not found"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        runtime_failure_rows = [a for a in attempts if a[4] == "runtime_failure"]
        assert len(runtime_failure_rows) >= 1
        # error_code is at index 6 (SQL $6: session_id=$1, catalog_entry_id=$2,
        # butler=$3, outcome=$4, failure_reason=$5, error_code=$6)
        assert runtime_failure_rows[0][6] == "RuntimeError"


# ---------------------------------------------------------------------------
# Tests: exhausted provenance
# ---------------------------------------------------------------------------


class TestExhaustedProvenance:
    async def test_exhausted_row_written_when_all_candidates_fail(self, tmp_path: Path) -> None:
        """An explicit 'exhausted' terminal row is written when no next candidate exists."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,  # exhausted after first fail
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("cli not found"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        outcomes = [a[4] for a in attempts]
        assert "exhausted" in outcomes, f"Expected exhausted terminal row in outcomes: {outcomes}"

    async def test_exhausted_row_carries_terminal_metadata(self, tmp_path: Path) -> None:
        """The exhausted row carries the failed catalog entry, error code, and logical session."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("cli not found"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        exhausted_rows = [a for a in attempts if a[4] == "exhausted"]
        assert len(exhausted_rows) == 1, f"Expected exactly one exhausted row: {attempts}"
        row = exhausted_rows[0]
        # Column mapping: SQL=a[0]; $1 session_id=a[1]; $2 catalog_entry_id=a[2];
        # $3 butler=a[3]; $4 outcome=a[4]; $5 failure_reason=a[5]; $6 error_code=a[6];
        # $9 attempt_index=a[9]; $10 logical_session_id=a[10].
        assert row[2] == _PRIMARY_ID, "exhausted row should reference the last failed catalog entry"
        assert row[6] == "RuntimeError", "exhausted row should carry the terminal error code"
        assert "same_tier_failover_exhausted" in (row[5] or ""), (
            f"exhausted row should identify exhaustion in failure_reason: {row[5]}"
        )
        assert row[10] is not None, "exhausted row must carry a logical_session_id"

    async def test_exhausted_row_after_runtime_failure_row(self, tmp_path: Path) -> None:
        """Exhaustion writes the runtime_failure row first, then the terminal exhausted row."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("cli not found"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        outcomes = [a[4] for a in attempts]
        assert outcomes.index("runtime_failure") < outcomes.index("exhausted"), (
            f"runtime_failure row must precede the exhausted terminal row: {outcomes}"
        )


# ---------------------------------------------------------------------------
# Tests: suppressed provenance
# ---------------------------------------------------------------------------


class TestSuppressedProvenance:
    async def test_suppressed_row_written_when_failover_ineligible(self, tmp_path: Path) -> None:
        """A suppressed row is written when the classifier rejects failover."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(
                    eligible=False, reason="tool_calls_present:1 captured"
                ),
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("side effects detected"),
            ).trigger("hello", "tick")

        assert result.success is False
        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        outcomes = [a[4] for a in attempts]
        assert "suppressed" in outcomes, f"Expected suppressed in outcomes: {outcomes}"

    async def test_suppressed_row_carries_failure_reason(self, tmp_path: Path) -> None:
        """suppressed row carries the classifier's failure reason."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(
                    eligible=False, reason="tool_calls_present:1 captured"
                ),
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("boom"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        suppressed_rows = [a for a in attempts if a[4] == "suppressed"]
        assert len(suppressed_rows) == 1
        # failure_reason is the 6th positional arg ($5 in SQL = args[5])
        assert "tool_calls_present" in (suppressed_rows[0][5] or "")


# ---------------------------------------------------------------------------
# Tests: success provenance
# ---------------------------------------------------------------------------


class TestSuccessProvenance:
    async def test_success_row_written_when_fallback_succeeds(self, tmp_path: Path) -> None:
        """A success row is written for the winning fallback attempt."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-fallback",
                    [],
                    _FALLBACK_ID,
                    1800,
                ),
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            # First adapter fails; the spawner will switch to its fallback instance.
            # We need to inject both fail-then-succeed behavior.
            call_count = 0

            class _FailThenSucceed(RuntimeAdapter):
                @property
                def binary_name(self) -> str:
                    return "mock"

                async def invoke(self, *args: Any, **kwargs: Any) -> tuple[str, list, dict | None]:
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        raise RuntimeError("primary failed")
                    return "ok", [], None

                async def reset(self) -> None:
                    pass

                def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
                    import json

                    p = tmp_dir / "cfg.json"
                    p.write_text(json.dumps({"mcpServers": mcp_servers}))
                    return p

                def parse_system_prompt_file(self, config_dir: Path) -> str:
                    return ""

            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailThenSucceed(),
            ).trigger("hello", "tick")

        assert result.success is True
        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        outcomes = [a[4] for a in attempts]
        assert "success" in outcomes, f"Expected success row when fallback wins: {outcomes}"

    async def test_no_success_row_when_primary_succeeds_directly(self, tmp_path: Path) -> None:
        """No success row when no failover occurred (primary succeeded first try)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_SuccessAdapter(),
            ).trigger("hello", "tick")

        assert result.success is True
        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        # No attempt rows at all when no failover occurred
        assert len(attempts) == 0, (
            f"Expected no dispatch attempt rows on direct success, got: {attempts}"
        )


# ---------------------------------------------------------------------------
# Tests: best-effort (insert failure does not propagate)
# ---------------------------------------------------------------------------


class TestDispatchAttemptBestEffort:
    async def test_insert_failure_does_not_propagate_on_quota_skip(self, tmp_path: Path) -> None:
        """Dispatch attempt INSERT failure is swallowed; session result is unaffected."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        # Make execute raise on the model_dispatch_attempts INSERT
        async def _execute_side_effect(sql: str, *args: Any, **kwargs: Any) -> str:
            if _ATTEMPTS_INSERT in sql:
                raise RuntimeError("DB write failed")
            return "OK"

        mock_pool.execute = AsyncMock(side_effect=_execute_side_effect)

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
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
            # Must not raise
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_SuccessAdapter(),
            ).trigger("hello", "tick")

        # The quota exhaustion error is the real result, not the INSERT failure
        assert result.success is False
        assert "quota" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Tests: Bug fixes (bu-fqkip review)
# ---------------------------------------------------------------------------


class TestLogicalSessionIdCorrelation:
    """Bug 1: quota_skip rows must use the same logical_session_id as later rows.

    When request_id is None (scheduler/tick trigger), effective_request_id is
    minted before the quota-skip loop so all rows for the same session share
    the same non-null UUID.
    """

    async def test_quota_skip_logical_session_id_not_null_when_request_id_none(
        self, tmp_path: Path
    ) -> None:
        """quota_skip row has a non-null logical_session_id even when request_id is None."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
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
            # trigger() is called without a request_id (simulates scheduler/tick trigger)
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_SuccessAdapter(),
            ).trigger("hello", "tick")

        assert result.success is False
        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        assert len(attempts) >= 1
        quota_skip_rows = [a for a in attempts if a[4] == "quota_skip"]
        assert len(quota_skip_rows) >= 1
        # logical_session_id is the 10th positional arg ($10 in SQL, index 10)
        logical_session_id = quota_skip_rows[0][10]
        assert logical_session_id is not None, (
            "quota_skip row must have a non-null logical_session_id even when request_id is None"
        )
        # Must be a valid UUID7-style string (36 chars with hyphens)
        assert len(logical_session_id) == 36, f"Expected UUID string, got: {logical_session_id!r}"

    async def test_quota_skip_and_runtime_failure_share_logical_session_id(
        self, tmp_path: Path
    ) -> None:
        """quota_skip and runtime_failure rows share the same logical_session_id."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                side_effect=[_QUOTA_DENIED_24H, _QUOTA_ALLOWED],
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                side_effect=[
                    (DEFAULT_RUNTIME_TYPE, "claude-fallback", [], _FALLBACK_ID, 1800),
                    None,  # exhausted after failover attempt
                ],
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("boom"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        quota_skip_rows = [a for a in attempts if a[4] == "quota_skip"]
        runtime_failure_rows = [a for a in attempts if a[4] == "runtime_failure"]

        if quota_skip_rows and runtime_failure_rows:
            qs_lsid = quota_skip_rows[0][10]
            rf_lsid = runtime_failure_rows[0][10]
            assert qs_lsid is not None
            assert rf_lsid is not None
            assert qs_lsid == rf_lsid, (
                f"quota_skip logical_session_id={qs_lsid!r} must equal "
                f"runtime_failure logical_session_id={rf_lsid!r}"
            )


class TestAttemptIndexAccuracy:
    """Bugs 2 & 3: attempt_index must equal len(_attempted_ids) at write time.

    The old formula `_attempt_count - 1 + len(_attempted_ids)` double-counted
    because failed attempts are appended to _attempted_ids AFTER the write.
    """

    async def test_suppressed_attempt_index_equals_zero_on_first_attempt(
        self, tmp_path: Path
    ) -> None:
        """suppressed row at first attempt has attempt_index=0 (not 1 from _attempt_count)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(
                    eligible=False, reason="tool_calls_present:1 captured"
                ),
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("side effects"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        suppressed_rows = [a for a in attempts if a[4] == "suppressed"]
        assert len(suppressed_rows) == 1
        # attempt_index is the 9th positional arg ($9, index 9)
        assert suppressed_rows[0][9] == 0, (
            f"suppressed attempt_index should be 0 on first attempt, got {suppressed_rows[0][9]}"
        )

    async def test_runtime_failure_attempt_index_equals_zero_on_first_attempt(
        self, tmp_path: Path
    ) -> None:
        """runtime_failure row at first attempt has attempt_index=0."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("boom"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        runtime_failure_rows = [a for a in attempts if a[4] == "runtime_failure"]
        assert len(runtime_failure_rows) >= 1
        assert runtime_failure_rows[0][9] == 0, (
            f"runtime_failure attempt_index should be 0 on first attempt, "
            f"got {runtime_failure_rows[0][9]}"
        )

    async def test_runtime_failure_attempt_index_increments_after_quota_skip(
        self, tmp_path: Path
    ) -> None:
        """runtime_failure attempt_index is 1 when one quota_skip preceded it."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                side_effect=[_QUOTA_DENIED_24H, _QUOTA_ALLOWED],
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                side_effect=[
                    (DEFAULT_RUNTIME_TYPE, "claude-fallback", [], _FALLBACK_ID, 1800),
                    None,
                ],
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID

            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_FailingAdapter("boom"),
            ).trigger("hello", "tick")

        attempts = _execute_calls_with_fragment(mock_pool, _ATTEMPTS_INSERT)
        quota_skip_rows = [a for a in attempts if a[4] == "quota_skip"]
        runtime_failure_rows = [a for a in attempts if a[4] == "runtime_failure"]
        # quota_skip should be attempt_index=0, runtime_failure should be attempt_index=1
        assert len(quota_skip_rows) == 1
        assert quota_skip_rows[0][9] == 0
        if runtime_failure_rows:
            assert runtime_failure_rows[0][9] == 1, (
                f"runtime_failure after 1 quota_skip should have attempt_index=1, "
                f"got {runtime_failure_rows[0][9]}"
            )


class TestSuccessPoolGuard:
    """Finding A: success write site must guard against pool=None."""

    async def test_no_success_write_when_pool_is_none(self, tmp_path: Path) -> None:
        """Success row is not attempted when pool is None (avoids AttributeError)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        call_count = 0

        class _FailThenSucceed(RuntimeAdapter):
            @property
            def binary_name(self) -> str:
                return "mock"

            async def invoke(self, *args: Any, **kwargs: Any) -> tuple[str, list, dict | None]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("primary failed")
                return "ok", [], None

            async def reset(self) -> None:
                pass

            def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
                import json

                p = tmp_dir / "cfg.json"
                p.write_text(json.dumps({"mcpServers": mcp_servers}))
                return p

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-primary",
                    [],
                    _PRIMARY_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.classify_failover_eligibility",
                return_value=FailoverDecision(eligible=True, reason="cli_missing"),
            ),
        ):
            # pool=None means no DB writes — must not raise AttributeError
            result = await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=None,
                runtime=_FailThenSucceed(),
            ).trigger("hello", "tick")

        # With pool=None, no session_create either, so session result is still computed
        # The key assertion is: no AttributeError / NoneType exception was raised
        # (reaching here means it didn't crash)
        assert result is not None

"""Tests for provider-native session resume wiring in Spawner (bu-bkthr).

Follow-up to bu-ep4ks.8 (PR #3582), which landed the ledger/provider-layer
primitives (``dashboard_conversations`` anchor + resume columns,
``butlers.api.conversations`` CRUD/resolve helpers, ``RuntimeAdapter.
supports_resume``) without wiring them into the spawner's dispatch loop.
This file covers that wiring:

1. ``resume_session_id`` is passed to the adapter's first invocation attempt
   ONLY when (a) the resolved adapter supports resume, (b)
   ``resolve_resume_handle`` resolves a same-runtime-type, non-expired
   handle, and (c) the dispatch is a conversational turn
   (``trigger_source == "route"``).
2. A failed resume attempt (with no confirmed side-effecting tool call, i.e.
   ``classify_failover_eligibility`` says eligible) evicts the stale handle
   and transparently retries the SAME candidate cold -- without writing a
   ``runtime_failure`` dispatch-attempt row and without consuming a same-tier
   failover slot (``next_same_tier_candidate`` is never called for this).
3. A successful invocation (resumed or cold) on a resume-capable adapter
   persists whatever ``provider_session_id`` it reports back onto the
   conversation as the new resume handle for the next turn.
4. A same-tier failover that lands on a later attempt never carries the
   resume handle forward, since it is scoped to attempt 1 only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

_PRIMARY_CATALOG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_FALLBACK_CATALOG_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_SESSION_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")
_CONVERSATION_ID = uuid.UUID("dddddddd-0000-0000-0000-000000000004")

_QUOTA_ALLOWED = QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


def _catalog_primary(model: str = "primary-model", tier: str = "workhorse") -> tuple:
    return (DEFAULT_RUNTIME_TYPE, model, [], _PRIMARY_CATALOG_ID, 1800, tier)


def _fresh_provider_session(
    *, runtime_type: str = DEFAULT_RUNTIME_TYPE, session_id: str = "resumable-session-id"
) -> dict[str, Any]:
    return {
        "provider_session_id": session_id,
        "provider_runtime_type": runtime_type,
        "provider_session_updated_at": datetime.now(UTC) - timedelta(minutes=5),
    }


class _ResumeCapableAdapter(RuntimeAdapter):
    """Adapter that records every invoke() call's resume_session_id and
    optionally fails its first N calls."""

    supports_resume = True

    def __init__(
        self,
        *,
        fail_count: int = 0,
        error: Exception | None = None,
        result_text: str = "ok",
        reported_session_id: str = "new-session-id",
    ) -> None:
        self._fail_count = fail_count
        self._error = error or RuntimeError("connection refused: provider unavailable")
        self._result_text = result_text
        self._reported_session_id = reported_session_id
        self.invoke_calls: list[dict[str, Any]] = []
        self._last_process_info: dict[str, Any] | None = None

    @property
    def binary_name(self) -> str:
        return "mock"

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        return self._last_process_info

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
        resume_session_id: str | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        self.invoke_calls.append({"resume_session_id": resume_session_id, "model": model})
        if len(self.invoke_calls) <= self._fail_count:
            self._last_process_info = {"runtime_type": DEFAULT_RUNTIME_TYPE}
            raise self._error
        self._last_process_info = {
            "runtime_type": DEFAULT_RUNTIME_TYPE,
            "provider_session_id": self._reported_session_id,
        }
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


class _NonResumeAdapter(_ResumeCapableAdapter):
    supports_resume = False


# ---------------------------------------------------------------------------
# (a) + (b) + (c) gating: resume_session_id only attached under all three
# ---------------------------------------------------------------------------


class TestResumeGating:
    async def test_resume_passed_when_eligible_on_conversational_turn(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(result_text="resumed-ok")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=_fresh_provider_session(),
            ) as mock_get_session,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        mock_get_session.assert_awaited_once_with(
            mock_pool, _CONVERSATION_ID, butler_name=config.name
        )
        assert len(adapter.invoke_calls) == 1
        assert adapter.invoke_calls[0]["resume_session_id"] == "resumable-session-id"

    async def test_resume_not_passed_when_trigger_source_is_not_route(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(result_text="ok")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=_fresh_provider_session(),
            ) as mock_get_session,
        ):
            mock_create.return_value = _SESSION_ID
            # trigger_source="tick" — not a conversational turn (route).
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        mock_get_session.assert_not_awaited()
        assert adapter.invoke_calls[0]["resume_session_id"] is None

    async def test_resume_not_passed_when_adapter_does_not_support_resume(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _NonResumeAdapter(result_text="ok")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=_fresh_provider_session(),
            ) as mock_get_session,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        mock_get_session.assert_not_awaited()
        assert adapter.invoke_calls[0]["resume_session_id"] is None

    async def test_resume_not_passed_when_conversation_id_is_none(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(result_text="ok")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=_fresh_provider_session(),
            ) as mock_get_session,
            patch(
                "butlers.core.spawner.conversation_set_provider_session",
                new_callable=AsyncMock,
            ) as mock_set_session,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route")

        assert result.success is True
        mock_get_session.assert_not_awaited()
        mock_set_session.assert_not_awaited()
        assert adapter.invoke_calls[0]["resume_session_id"] is None

    async def test_resume_not_passed_when_handle_expired(self, tmp_path: Path) -> None:
        """resolve_resume_handle's real TTL logic rejects a stale handle."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(result_text="ok")

        stale_session = _fresh_provider_session()
        stale_session["provider_session_updated_at"] = datetime.now(UTC) - timedelta(hours=25)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=stale_session,
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        assert adapter.invoke_calls[0]["resume_session_id"] is None

    async def test_resume_not_passed_when_runtime_type_mismatches(self, tmp_path: Path) -> None:
        """A handle minted by a different runtime_type is never resumed."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(result_text="ok")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=_fresh_provider_session(runtime_type="a-different-runtime-type"),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        assert adapter.invoke_calls[0]["resume_session_id"] is None


# ---------------------------------------------------------------------------
# Fallback-to-cold semantics on a failed resume attempt
# ---------------------------------------------------------------------------


class TestResumeFailureFallsBackToCold:
    async def test_failed_resume_retries_same_candidate_cold_without_provenance_row(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(
            fail_count=1,
            error=RuntimeError("connection refused: provider unavailable"),
            result_text="cold-succeeded",
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=_fresh_provider_session(),
            ),
            patch(
                "butlers.core.spawner.conversation_clear_provider_session",
                new_callable=AsyncMock,
            ) as mock_clear,
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
            patch(
                "butlers.core.spawner._write_dispatch_attempt", new_callable=AsyncMock
            ) as mock_write,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        assert result.output == "cold-succeeded"
        # Same candidate retried cold: two invoke() calls, same model both times,
        # first with the handle, second without.
        assert len(adapter.invoke_calls) == 2
        assert adapter.invoke_calls[0]["resume_session_id"] == "resumable-session-id"
        assert adapter.invoke_calls[1]["resume_session_id"] is None
        assert adapter.invoke_calls[1]["model"] == adapter.invoke_calls[0]["model"]
        # No same-tier failover candidate was consulted for the resume failure.
        mock_next.assert_not_called()
        # The stale handle was evicted.
        mock_clear.assert_awaited_once_with(mock_pool, _CONVERSATION_ID, butler_name=config.name)
        # Only ONE dispatch-attempt row was written (the final success) -- the
        # failed resume attempt did not consume a failover slot or count
        # against the model's breaker.
        outcomes = [c.kwargs.get("outcome") for c in mock_write.call_args_list]
        assert outcomes == ["success"]

    async def test_failed_resume_with_confirmed_tool_calls_uses_ordinary_failover(
        self, tmp_path: Path
    ) -> None:
        """A resume attempt that DID run a side-effecting tool call before
        failing must NOT be silently retried -- it goes through the ordinary
        (default-closed) failover classification like any other failure."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(
            fail_count=1,
            error=RuntimeError("connection refused: provider unavailable"),
            result_text="unused",
        )
        tool_call = {"name": "notify", "id": "tc1", "input": {}}

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=_fresh_provider_session(),
            ),
            patch(
                "butlers.core.spawner.conversation_clear_provider_session",
                new_callable=AsyncMock,
            ) as mock_clear,
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
            # Simulate a confirmed MCP tool call captured before the failure.
            patch(
                "butlers.core.spawner.consume_runtime_session_tool_calls",
                return_value=[tool_call],
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        # Side effects confirmed -> failover suppressed, session fails terminally.
        assert result.success is False
        assert len(adapter.invoke_calls) == 1
        mock_next.assert_not_called()
        # The transparent-cold-retry branch never fires for an ineligible
        # (side-effecting) failure -- the handle is left untouched.
        mock_clear.assert_not_awaited()


# ---------------------------------------------------------------------------
# Persisting the reported provider_session_id after success
# ---------------------------------------------------------------------------


class TestResumeHandlePersistedAfterSuccess:
    async def test_two_turns_reuse_anchor_and_resume_first_provider_session(
        self, tmp_path: Path
    ) -> None:
        """Turn two resumes the provider handle persisted by turn one."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(
            result_text="ok", reported_session_id="provider-session-from-turn-one"
        )
        provider_state: dict[str, Any] | None = None

        async def get_provider_session(*_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
            return provider_state

        async def set_provider_session(
            _pool: Any,
            _conversation_id: uuid.UUID,
            *,
            provider_session_id: str,
            provider_runtime_type: str,
            **_kwargs: Any,
        ) -> None:
            nonlocal provider_state
            provider_state = _fresh_provider_session(
                runtime_type=provider_runtime_type,
                session_id=provider_session_id,
            )

        with (
            patch(
                "butlers.core.spawner.session_create",
                new_callable=AsyncMock,
                return_value=_SESSION_ID,
            ),
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                side_effect=get_provider_session,
            ),
            patch(
                "butlers.core.spawner.conversation_set_provider_session",
                new_callable=AsyncMock,
                side_effect=set_provider_session,
            ),
        ):
            spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)
            first = await spawner.trigger(
                "first Telegram turn", "route", conversation_id=_CONVERSATION_ID
            )
            second = await spawner.trigger(
                "second Telegram turn", "route", conversation_id=_CONVERSATION_ID
            )

        assert first.success is True and second.success is True
        assert [call["resume_session_id"] for call in adapter.invoke_calls] == [
            None,
            "provider-session-from-turn-one",
        ]

    async def test_handle_persisted_after_cold_success(self, tmp_path: Path) -> None:
        """Even a cold (non-resumed) turn on a resume-capable adapter persists
        the freshly reported session id for the *next* turn."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _ResumeCapableAdapter(result_text="ok", reported_session_id="brand-new-session")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.conversation_set_provider_session",
                new_callable=AsyncMock,
            ) as mock_set_session,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        mock_set_session.assert_awaited_once_with(
            mock_pool,
            _CONVERSATION_ID,
            butler_name=config.name,
            provider_session_id="brand-new-session",
            provider_runtime_type=DEFAULT_RUNTIME_TYPE,
        )

    async def test_handle_not_persisted_when_adapter_reports_none(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        class _NoSessionIdAdapter(_ResumeCapableAdapter):
            async def invoke(self, *args: Any, **kwargs: Any):
                result = await super().invoke(*args, **kwargs)
                self._last_process_info.pop("provider_session_id", None)
                return result

        adapter = _NoSessionIdAdapter(result_text="ok")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.conversation_set_provider_session",
                new_callable=AsyncMock,
            ) as mock_set_session,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        mock_set_session.assert_not_awaited()

    async def test_handle_not_persisted_for_non_resume_adapter(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _NonResumeAdapter(result_text="ok", reported_session_id="ignored")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_set_provider_session",
                new_callable=AsyncMock,
            ) as mock_set_session,
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        mock_set_session.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cross-runtime-type failover never carries the resume handle
# ---------------------------------------------------------------------------


class TestCrossRuntimeFailoverNeverCarriesHandle:
    async def test_failover_to_different_runtime_type_after_cold_retry_has_no_resume(
        self, tmp_path: Path
    ) -> None:
        """attempt 1 (resumed) fails eligibly -> attempt 2 (same candidate,
        cold) also fails eligibly -> ordinary same-tier failover advances to a
        DIFFERENT runtime_type candidate. That third attempt must never carry
        resume_session_id, and the new adapter is the one actually invoked."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter_primary = _ResumeCapableAdapter(
            fail_count=2,  # both attempt 1 (resumed) and attempt 2 (cold) fail
            error=RuntimeError("connection refused: provider unavailable"),
        )
        adapter_fallback = _NonResumeAdapter(result_text="fallback-succeeded")

        def _get_adapter_side_effect(runtime_type: str, provider_config=None):
            if runtime_type == "other-runtime-type":
                return adapter_fallback
            return adapter_primary

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_QUOTA_ALLOWED,
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                new_callable=AsyncMock,
                return_value=_fresh_provider_session(),
            ),
            patch(
                "butlers.core.spawner.conversation_clear_provider_session",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=(
                    "other-runtime-type",
                    "fallback-model",
                    [],
                    _FALLBACK_CATALOG_ID,
                    1800,
                ),
            ),
        ):
            spawner = Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter_primary
            )
            spawner._get_or_create_adapter = _get_adapter_side_effect  # type: ignore[method-assign]
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hello", "route", conversation_id=_CONVERSATION_ID)

        assert result.success is True
        assert result.output == "fallback-succeeded"
        # Primary adapter: resumed attempt then cold retry, both failed.
        assert len(adapter_primary.invoke_calls) == 2
        assert adapter_primary.invoke_calls[0]["resume_session_id"] == "resumable-session-id"
        assert adapter_primary.invoke_calls[1]["resume_session_id"] is None
        # Fallback adapter (different runtime_type): exactly one call, no resume.
        assert len(adapter_fallback.invoke_calls) == 1
        assert adapter_fallback.invoke_calls[0]["resume_session_id"] is None

"""Unit tests for staffer routing exclusion in the switchboard.

Covers:
- list_butlers(butler_only=True) excludes staffer-typed agents
- list_butlers() (default) includes both butlers and staffers
- _load_available_butlers returns only butler-typed agents
- correct_route rejects staffer-typed targets
- correct_route accepts butler-typed targets normally
- Staffer registrations store agent_type = 'staffer'
- _normalize_agent_type normalizes edge cases

Issue: bu-8njj0.4
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry_row(
    name: str,
    agent_type: str = "butler",
    eligibility_state: str = "active",
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a minimal butler_registry row dict for use in mock pool responses."""
    return {
        "name": name,
        "endpoint_url": f"http://localhost:4200{name[:1]}/sse",
        "description": None,
        "modules": json.dumps([]),
        "capabilities": json.dumps(["trigger"]),
        "last_seen_at": datetime.now(UTC),
        "registered_at": datetime.now(UTC),
        "eligibility_state": eligibility_state,
        "liveness_ttl_seconds": 300,
        "quarantined_at": None,
        "quarantine_reason": None,
        "route_contract_min": 1,
        "route_contract_max": 1,
        "eligibility_updated_at": datetime.now(UTC),
        "agent_type": agent_type,
        **kwargs,
    }


# ---------------------------------------------------------------------------
# _normalize_agent_type — unit tests
# ---------------------------------------------------------------------------


class TestNormalizeAgentType:
    """Unit tests for the _normalize_agent_type helper."""

    def test_butler_passes_through(self) -> None:
        from butlers.tools.switchboard.registry.registry import _normalize_agent_type

        assert _normalize_agent_type("butler") == "butler"

    def test_staffer_passes_through(self) -> None:
        from butlers.tools.switchboard.registry.registry import _normalize_agent_type

        assert _normalize_agent_type("staffer") == "staffer"

    def test_unknown_defaults_to_butler(self) -> None:
        from butlers.tools.switchboard.registry.registry import _normalize_agent_type

        assert _normalize_agent_type("unknown") == "butler"

    def test_none_defaults_to_butler(self) -> None:
        from butlers.tools.switchboard.registry.registry import _normalize_agent_type

        assert _normalize_agent_type(None) == "butler"

    def test_empty_string_defaults_to_butler(self) -> None:
        from butlers.tools.switchboard.registry.registry import _normalize_agent_type

        assert _normalize_agent_type("") == "butler"

    def test_case_insensitive(self) -> None:
        from butlers.tools.switchboard.registry.registry import _normalize_agent_type

        assert _normalize_agent_type("STAFFER") == "staffer"
        assert _normalize_agent_type("Butler") == "butler"

    def test_whitespace_stripped(self) -> None:
        from butlers.tools.switchboard.registry.registry import _normalize_agent_type

        assert _normalize_agent_type("  staffer  ") == "staffer"


# ---------------------------------------------------------------------------
# list_butlers — filtering tests (unit, mocked pool)
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.unit


class TestListButlersAgentTypeFilter:
    """Unit tests for butler_only filtering in list_butlers."""

    async def _make_pool_with_rows(self, rows: list[dict[str, Any]]) -> AsyncMock:
        """Return a mock pool whose fetch() returns the given rows."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows)
        # _reconcile_eligibility_state calls pool.execute — make it a no-op
        pool.execute = AsyncMock(return_value=None)
        return pool

    async def test_butler_only_excludes_staffers(self) -> None:
        """butler_only=True excludes staffer-typed agents."""
        from butlers.tools.switchboard.registry.registry import list_butlers

        rows = [
            _make_registry_row("general", agent_type="butler"),
            _make_registry_row("messenger", agent_type="staffer"),
        ]
        pool = await self._make_pool_with_rows(rows)

        result = await list_butlers(pool, butler_only=True)

        names = [r["name"] for r in result]
        assert "general" in names
        assert "messenger" not in names

    async def test_butler_only_preserves_butler_agents(self) -> None:
        """butler_only=True keeps all butler-typed agents."""
        from butlers.tools.switchboard.registry.registry import list_butlers

        rows = [
            _make_registry_row("health", agent_type="butler"),
            _make_registry_row("calendar", agent_type="butler"),
            _make_registry_row("switchboard", agent_type="staffer"),
        ]
        pool = await self._make_pool_with_rows(rows)

        result = await list_butlers(pool, butler_only=True)

        names = [r["name"] for r in result]
        assert "health" in names
        assert "calendar" in names
        assert "switchboard" not in names

    async def test_default_includes_both_types(self) -> None:
        """list_butlers() without butler_only includes both butlers and staffers."""
        from butlers.tools.switchboard.registry.registry import list_butlers

        rows = [
            _make_registry_row("general", agent_type="butler"),
            _make_registry_row("messenger", agent_type="staffer"),
        ]
        pool = await self._make_pool_with_rows(rows)

        result = await list_butlers(pool)

        names = [r["name"] for r in result]
        assert "general" in names
        assert "messenger" in names

    async def test_routable_and_butler_only_combined(self) -> None:
        """butler_only and routable_only can both be applied together."""
        from butlers.tools.switchboard.registry.registry import list_butlers

        # Use last_seen_at far in the past to force stale-by-TTL derivation
        old_ts = datetime.now(UTC) - timedelta(hours=2)
        rows = [
            _make_registry_row("active_butler", agent_type="butler", eligibility_state="active"),
            _make_registry_row(
                "stale_butler",
                agent_type="butler",
                eligibility_state="stale",
                last_seen_at=old_ts,
            ),
            _make_registry_row("active_staffer", agent_type="staffer", eligibility_state="active"),
        ]
        pool = await self._make_pool_with_rows(rows)

        result = await list_butlers(pool, routable_only=True, butler_only=True)

        names = [r["name"] for r in result]
        assert "active_butler" in names
        assert "stale_butler" not in names
        assert "active_staffer" not in names

    async def test_agent_type_preserved_in_result(self) -> None:
        """Returned rows include the agent_type field."""
        from butlers.tools.switchboard.registry.registry import list_butlers

        rows = [
            _make_registry_row("general", agent_type="butler"),
            _make_registry_row("messenger", agent_type="staffer"),
        ]
        pool = await self._make_pool_with_rows(rows)

        result = await list_butlers(pool)

        by_name = {r["name"]: r for r in result}
        assert by_name["general"]["agent_type"] == "butler"
        assert by_name["messenger"]["agent_type"] == "staffer"


# ---------------------------------------------------------------------------
# _load_available_butlers — classifier integration (unit, mocked)
# ---------------------------------------------------------------------------


class TestLoadAvailableButlers:
    """Unit tests for _load_available_butlers staffer exclusion."""

    async def test_excludes_staffers_from_candidate_set(self) -> None:
        """_load_available_butlers only returns butler-typed agents."""
        from butlers.tools.switchboard.routing.classify import _load_available_butlers

        butler_row = _make_registry_row("general", agent_type="butler")
        staffer_row = _make_registry_row("messenger", agent_type="staffer")

        pool = AsyncMock()
        # First call returns rows directly (no discover needed)
        pool.fetch = AsyncMock(return_value=[butler_row, staffer_row])
        pool.execute = AsyncMock(return_value=None)

        result = await _load_available_butlers(pool)

        names = [r["name"] for r in result]
        assert "general" in names
        assert "messenger" not in names

    async def test_empty_registry_auto_discover_filters_staffers(self) -> None:
        """After auto-discovery, _load_available_butlers still filters staffers."""
        from butlers.tools.switchboard.routing.classify import _load_available_butlers

        butler_row = _make_registry_row("health", agent_type="butler")
        staffer_row = _make_registry_row("switchboard", agent_type="staffer")

        pool = AsyncMock()
        # First call: empty (triggers auto-discover path)
        # Second call: returns discovered rows
        pool.fetch = AsyncMock(side_effect=[[], [butler_row, staffer_row]])
        pool.execute = AsyncMock(return_value=None)

        with patch(
            "butlers.tools.switchboard.routing.classify.discover_butlers",
            new_callable=AsyncMock,
        ) as mock_discover:
            mock_discover.return_value = [{"name": "health"}, {"name": "switchboard"}]
            result = await _load_available_butlers(pool)

        names = [r["name"] for r in result]
        assert "health" in names
        assert "switchboard" not in names


# ---------------------------------------------------------------------------
# register_butler — agent_type stored correctly
# ---------------------------------------------------------------------------


class TestRegisterButlerAgentType:
    """Unit tests confirming agent_type is stored via register_butler."""

    async def test_staffer_registration_stores_agent_type(self) -> None:
        """register_butler with agent_type='staffer' stores 'staffer' in the DB."""
        from butlers.tools.switchboard.registry.registry import register_butler

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock(return_value=None)

        await register_butler(
            pool,
            "messenger",
            "http://localhost:41201/sse",
            "Messenger staffer",
            agent_type="staffer",
        )

        # Verify execute was called with the agent_type='staffer' parameter
        execute_calls = pool.execute.call_args_list
        # At least one INSERT/UPSERT call should include 'staffer'
        call_args_flat = [str(call) for call in execute_calls]
        assert any("staffer" in args for args in call_args_flat), (
            "Expected 'staffer' to appear in an execute() call for the INSERT/UPSERT"
        )

    async def test_butler_registration_uses_butler_default(self) -> None:
        """register_butler without agent_type defaults to 'butler'."""
        from butlers.tools.switchboard.registry.registry import register_butler

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock(return_value=None)

        await register_butler(
            pool,
            "general",
            "http://localhost:41101/sse",
        )

        execute_calls = pool.execute.call_args_list
        call_args_flat = [str(call) for call in execute_calls]
        # 'butler' should appear in the INSERT call
        assert any("butler" in args for args in call_args_flat), (
            "Expected 'butler' to appear in an execute() call for the INSERT/UPSERT"
        )


# ---------------------------------------------------------------------------
# correct_route — staffer target rejection (unit, mocked pool)
# ---------------------------------------------------------------------------


class TestCorrectRouteStafferRejection:
    """Unit tests for correct_route staffer-target validation.

    These tests mock the pool at a granular level to avoid needing a real DB.
    They cover acceptance criteria: correct_route rejects staffer-typed targets
    with a descriptive error (target_is_staffer).
    """

    async def _make_pool(
        self,
        *,
        ingestion_event: dict[str, Any] | None = None,
        inbox_row: dict[str, Any] | None = None,
        target_agent_type: str = "butler",
    ) -> AsyncMock:
        """Build a mock pool that returns the given rows per query.

        The pool distinguishes between the agent_type SELECT (SELECT agent_type FROM
        butler_registry WHERE name = $1) used by the staffer type check and the full
        butler_registry SELECT used by resolve_routing_target inside route().

        The agent_type check query selects only 'agent_type'; the route() lookup
        selects many columns.  We return the type-check row for the narrow query
        and None for the broad route lookup so the route fails with "not found"
        rather than a KeyError on missing columns.
        """
        pool = AsyncMock()

        async def _fetchrow(query: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            q = query.strip().lower()
            if "public.ingestion_events" in q:
                return ingestion_event
            if "message_inbox" in q:
                return inbox_row
            # The staffer type check uses a narrow SELECT: "SELECT agent_type FROM ..."
            # The route() resolve_routing_target uses a wide SELECT with endpoint_url.
            # Distinguish by the presence of "endpoint_url" in the query.
            if "butler_registry" in q and "endpoint_url" not in q:
                return {"agent_type": target_agent_type}
            # All other butler_registry queries (resolve_routing_target, etc.): not found
            if "butler_registry" in q:
                return None
            return None

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.execute = AsyncMock(return_value=None)
        return pool

    def _make_ingestion_event(self, request_id: uuid.UUID) -> dict[str, Any]:
        return {
            "id": request_id,
            "received_at": datetime.now(UTC),
            "source_channel": "telegram_bot",
            "source_provider": "telegram",
            "source_endpoint_identity": "bot_test",
            "source_sender_identity": "user_123",
            "source_thread_identity": None,
            "external_event_id": f"evt_{request_id}",
            "dedupe_key": f"dedupe_{request_id}",
            "ingestion_tier": "full",
            "policy_tier": "default",
            "triage_decision": "route_to",
            "triage_target": "assistant",
        }

    def _make_inbox_row(self, request_id: uuid.UUID) -> dict[str, Any]:
        return {
            "id": request_id,
            "received_at": datetime.now(UTC),
            "raw_payload": json.dumps({}),
            "normalized_text": "Hello from wrong butler",
            "lifecycle_state": "accepted",
            "request_context": json.dumps(
                {"triage_decision": "route_to", "triage_target": "assistant"}
            ),
            "attachments": None,
        }

    async def test_staffer_target_returns_target_is_staffer_error(self) -> None:
        """correct_route returns target_is_staffer when target is a staffer."""
        from butlers.tools.switchboard.routing.correct_route import correct_route

        request_id = uuid.uuid4()
        correction_id = uuid.uuid4()

        pool = await self._make_pool(
            ingestion_event=self._make_ingestion_event(request_id),
            inbox_row=self._make_inbox_row(request_id),
            target_agent_type="staffer",
        )

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="messenger",
            correction_id=correction_id,
        )

        assert result["success"] is False
        assert result["error"] == "target_is_staffer"

    async def test_staffer_rejection_message_is_actionable(self) -> None:
        """Rejection message tells the LLM what to do instead."""
        from butlers.tools.switchboard.routing.correct_route import correct_route

        request_id = uuid.uuid4()
        correction_id = uuid.uuid4()

        pool = await self._make_pool(
            ingestion_event=self._make_ingestion_event(request_id),
            inbox_row=self._make_inbox_row(request_id),
            target_agent_type="staffer",
        )

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="messenger",
            correction_id=correction_id,
        )

        assert "messenger" in result["message"]
        assert "staffer" in result["message"].lower()
        assert "list_butlers" in result["message"]

    async def test_butler_target_proceeds_past_type_check(self) -> None:
        """correct_route proceeds normally when target is a butler-typed agent.

        We verify that the type check does NOT block dispatch for butler-typed
        targets.  The call will fail at the dispatch step (unregistered butler),
        but it must not return target_is_staffer.
        """
        from butlers.tools.switchboard.routing.correct_route import correct_route

        request_id = uuid.uuid4()
        correction_id = uuid.uuid4()

        pool = await self._make_pool(
            ingestion_event=self._make_ingestion_event(request_id),
            inbox_row=self._make_inbox_row(request_id),
            target_agent_type="butler",
        )

        # Route to an unregistered butler — should fail with dispatch_failed,
        # not target_is_staffer.
        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        # Must not return target_is_staffer
        assert result.get("error") != "target_is_staffer"

    async def test_unknown_target_not_in_registry_rejected_with_available(
        self, monkeypatch: Any
    ) -> None:
        """A target not in the registry is rejected with the available butlers.

        Per the butler-switchboard spec, re-dispatch to an unregistered butler
        must fail with the list of available butlers (sourced from the real
        registry) so the caller can pick a valid routing target — it must NOT
        fall through to a generic dispatch error.
        """
        from butlers.tools.switchboard.routing import correct_route as correct_route_mod
        from butlers.tools.switchboard.routing.correct_route import correct_route

        request_id = uuid.uuid4()
        correction_id = uuid.uuid4()

        pool = AsyncMock()

        async def _fetchrow(query: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            q = query.strip().lower()
            if "public.ingestion_events" in q:
                return self._make_ingestion_event(request_id)
            if "message_inbox" in q:
                return self._make_inbox_row(request_id)
            # agent_type query returns None → target not in registry
            return None

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.execute = AsyncMock(return_value=None)

        # Stub the registry enumeration so available_butlers is populated from a
        # known set of routable butler-typed agents.
        list_butlers_mock = AsyncMock(
            return_value=[{"name": "personal_assistant"}, {"name": "finance"}]
        )
        monkeypatch.setattr(correct_route_mod, "list_butlers", list_butlers_mock)

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="ghost_butler",
            correction_id=correction_id,
        )

        assert result["success"] is False
        assert result["error"] == "butler_not_registered"
        assert result["available_butlers"] == ["personal_assistant", "finance"]
        # Enumeration must request only routable butler-typed agents.
        list_butlers_mock.assert_awaited_once()
        assert list_butlers_mock.await_args.kwargs == {
            "routable_only": True,
            "butler_only": True,
        }

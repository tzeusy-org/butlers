"""Contract test locking in the fleet-wide degraded-envelope convention.

CLAUDE.md "API Conventions -- Degraded-Mode Response Envelope" (bu-qvnce.1,
generalised by the bu-tpudw epic): every fan-out/aggregation endpoint across
the dashboard API must never let a source that raises or is unreachable
render as a truthful empty/zero/all-clear result. The concrete flag shape
varies by endpoint (a bespoke boolean field, a `meta.<flag>` entry on the
extensible `ApiMeta`/`PaginationMeta` bag, or a named list on the payload),
but the underlying promise is the same: a genuine source failure is always
named somewhere in the response.

This file is a REGISTRY, not a per-endpoint exhaustive suite. Each entry in
``REGISTRY`` pairs a short surface name with one ``run()`` coroutine that:

    1. wires the FastAPI app so exactly one underlying source raises while at
       least one sibling source (or the only source) answers,
    2. calls the real endpoint through the ASGI transport,
    3. asserts the documented flag is present/true/named -- exact-value
       assertions (not just "truthy"), so a regression that silently drops
       the flag or renames it fails loudly.

To cover a newly-flagged fan-out/aggregation endpoint, add ONE ``_case_*()``
factory below and append its call to ``REGISTRY`` -- do not hand-roll a
parallel ad hoc test elsewhere in this file.

Scope boundary: this file only pins the "a source raising gets flagged"
direction. The "a fully-healthy fan-out stays honestly flag-free" direction
is already covered exhaustively by each surface's own dedicated test module
under ``tests/api/`` (e.g. ``test_sessions_aggregate.py``,
``test_api_issues_degraded.py``) -- duplicating that direction here would
just be bulk, not additional protection, since those modules run in the same
CI gate. Where a builder is reused from those modules below, it is imported
directly (not re-implemented) so this file can never silently drift from the
real per-butler SQL dispatch those modules were written and verified against
(mocked-pool vs real-Postgres gap -- see AGENTS.md notes on Butlers mocked-pool
tests).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

import butlers.api.routers.settings_console as console_mod
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.pricing import ModelPricing, PricingConfig
from butlers.api.routers.memory import _get_db_manager as _memory_get_db
from butlers.api.routers.notifications import _get_db_manager as _notifications_get_db
from butlers.api.routers.search import _get_db_manager as _search_get_db
from butlers.api.routers.secrets_v2 import _get_db_manager as _secrets_get_db
from tests.api.test_api_approvals import (
    _app_with_one_healthy_one_raising_butler,
)
from tests.api.test_api_approvals import (
    _make_action as _make_approval_action,
)
from tests.api.test_api_issues_degraded import (
    _SOURCE_AUDIT_GROUPS,
)
from tests.api.test_api_issues_degraded import (
    _build_app as _make_issues_app,
)
from tests.api.test_butlers_board import (
    _build_app as _make_board_app,
)
from tests.api.test_butlers_board import (
    _FakeButlerPool as _BoardButlerPool,
)
from tests.api.test_butlers_board import (
    _FakeDb as _BoardFakeDb,
)
from tests.api.test_butlers_board import (
    _FakeSwitchboardPool as _BoardSwitchboardPool,
)
from tests.api.test_butlers_board import (
    _registry_row as _board_registry_row,
)
from tests.api.test_calendar_conflict_radar import _build_app as _make_conflicts_app
from tests.api.test_calendar_dedup_review import (
    _DUP_PARAMS,
)
from tests.api.test_calendar_dedup_review import (
    _build_app as _make_duplicates_app,
)
from tests.api.test_calendar_dedup_review import (
    _FakePool as _DedupFakePool,
)
from tests.api.test_calendar_workspace import (
    _audit_row,
    _workspace_event_row,
)
from tests.api.test_calendar_workspace import (
    _build_app as _make_calendar_workspace_app,
)
from tests.api.test_calendar_workspace import (
    _build_audit_app as _make_calendar_audit_app,
)
from tests.api.test_memory import (
    _EmptyMemoryPool,
    _EntityListPool,
    _EntityMemoryPool,
    _InspectPool,
    _make_entity_row,
    _MemoryDetailPool,
    _MemoryFanOutDB,
    _RaisingMemoryPool,
    _RaisingStatsPool,
    _StatsDB,
    _StatsPool,
)
from tests.api.test_notifications import (
    _make_unavailable_db as _make_notifications_unavailable_db,
)
from tests.api.test_session_detail_global import _make_app as _make_session_detail_app
from tests.api.test_sessions_aggregate import _make_app_with_aggregate
from tests.api.test_sessions_pagination import _make_app_with_sessions, _make_session_row
from tests.api.test_spend import (
    _flat_pricing,
    _make_tool_result,
    _mock_db,
)
from tests.api.test_spend import (
    _mock_mgr as _make_spend_mcp_mgr,
)
from tests.api.test_spend import (
    _wire as _wire_spend_app,
)
from tests.api.test_spend import _wire_db as _wire_spend_db

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class DegradedCase:
    """One registry entry: a surface name plus a self-contained assertion."""

    name: str
    run: Callable[[], Awaitable[None]]


async def _request(app, method: str, path: str, params: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, params=params)


# ---------------------------------------------------------------------------
# sessions (bu-tpudw.2 / .3 lineage)
# ---------------------------------------------------------------------------


def _case_sessions_list() -> DegradedCase:
    async def _run() -> None:
        app = _make_app_with_sessions([_make_session_row()], degraded=["finance"])
        resp = await _request(app, "GET", "/api/sessions")
        assert resp.status_code == 200
        assert resp.json()["meta"]["sources_degraded"] == ["finance"]

    return DegradedCase("sessions_list", _run)


def _case_sessions_aggregate() -> DegradedCase:
    async def _run() -> None:
        app = _make_app_with_aggregate(
            {"health": {"total": 5, "success_count": 5, "failed_count": 0}},
            degraded=["finance"],
        )
        resp = await _request(app, "GET", "/api/sessions/aggregate")
        assert resp.status_code == 200
        body = resp.json()
        # The undercounted zero must not read as "no sessions failed".
        assert body["data"]["failed_count"] == 0
        assert body["meta"]["sources_degraded"] == ["finance"]

    return DegradedCase("sessions_aggregate", _run)


def _case_sessions_detail() -> DegradedCase:
    async def _run() -> None:
        app = _make_session_detail_app(owning_butler="general", row=None, degraded=["general"])
        resp = await _request(app, "GET", f"/api/sessions/{uuid4()}")
        # A miss while a pool was unreachable is a 503, never a false 404.
        assert resp.status_code == 503

    return DegradedCase("sessions_detail", _run)


# ---------------------------------------------------------------------------
# search (bu-tpudw.4)
# ---------------------------------------------------------------------------


def _case_search() -> DegradedCase:
    async def _run() -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["relationship", "finance"]
        mock_db.pool = MagicMock(return_value=pool)
        # sessions fan-out: finance fails; state fan-out: clean.
        mock_db.fan_out_with_status = AsyncMock(
            side_effect=[({"relationship": [], "finance": []}, ["finance"]), ({}, [])]
        )
        app = create_app()
        app.dependency_overrides[_search_get_db] = lambda: mock_db

        resp = await _request(app, "GET", "/api/search", {"q": "anything"})
        assert resp.status_code == 200
        assert resp.json()["meta"]["sources_degraded"] == ["finance"]

    return DegradedCase("search", _run)


# ---------------------------------------------------------------------------
# issues (bu-tpudw.3)
# ---------------------------------------------------------------------------


def _case_issues() -> DegradedCase:
    async def _run() -> None:
        async def fetch(sql: str, *_args: object) -> list[object]:
            if "grouped_errors" in sql:
                raise ConnectionError("connection reset by peer")
            return []

        app, _pool = _make_issues_app(fetch)
        resp = await _request(app, "GET", "/api/issues")
        assert resp.status_code == 200
        assert resp.json()["meta"]["sources_degraded"] == [_SOURCE_AUDIT_GROUPS]

    return DegradedCase("issues", _run)


# ---------------------------------------------------------------------------
# calendar workspace (bu-yjfk2)
# ---------------------------------------------------------------------------

_WORKSPACE_PARAMS = {
    "view": "user",
    "start": "2026-02-22T00:00:00Z",
    "end": "2026-02-23T00:00:00Z",
}


def _case_calendar_entries() -> DegradedCase:
    async def _run() -> None:
        row = _workspace_event_row(
            lane="user",
            source_key="provider:google:primary",
            source_kind="provider_event",
            butler_name=None,
            calendar_id="primary",
            metadata={"source_type": "provider_event"},
        )
        app, _db, _mgr = _make_calendar_workspace_app(
            create_app(),
            workspace_rows={"general": [row]},
            calendar_butlers=["general", "relationship"],
            workspace_failed=["relationship"],
        )
        resp = await _request(app, "GET", "/api/calendar/workspace", _WORKSPACE_PARAMS)
        assert resp.status_code == 200
        body = resp.json()["data"]
        # The responding schema's entry still renders...
        assert len(body["entries"]) == 1
        # ...but the grid is honestly flagged incomplete.
        assert body["entries_source_available"] is False

    return DegradedCase("calendar_workspace_entries", _run)


def _case_calendar_conflicts() -> DegradedCase:
    async def _run() -> None:
        app, _db = _make_conflicts_app(
            create_app(),
            workspace_rows={"general": []},
            workspace_failed=["relationship"],
        )
        resp = await _request(
            app,
            "GET",
            "/api/calendar/workspace/conflicts",
            {"start": "2026-07-01T00:00:00Z", "end": "2026-07-02T00:00:00Z"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["issues"] == []
        assert data["issues_available"] is False

    return DegradedCase("calendar_workspace_conflicts", _run)


def _case_calendar_audit() -> DegradedCase:
    async def _run() -> None:
        rows = {
            "general": [_audit_row(action_type="workspace_user_create", action_status="applied")]
        }
        app, _db = _make_calendar_audit_app(
            create_app(),
            audit_rows=rows,
            calendar_butlers=["general", "relationship"],
            audit_failed=["relationship"],
        )
        resp = await _request(app, "GET", "/api/calendar/workspace/audit")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["entries"]) == 1
        assert data["sources_available"] is False

    return DegradedCase("calendar_workspace_audit", _run)


def _case_calendar_duplicates() -> DegradedCase:
    async def _run() -> None:
        with patch(
            "butlers.api.routers.calendar_workspace._fetch_flattened_workspace_rows",
            AsyncMock(side_effect=RuntimeError("pool down")),
        ):
            app, _db = _make_duplicates_app(create_app(), workspace_rows={}, pool=_DedupFakePool())
            resp = await _request(app, "GET", "/api/calendar/workspace/duplicates", _DUP_PARAMS)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["clusters"] == []
        assert data["available"] is False

    return DegradedCase("calendar_workspace_duplicates", _run)


# ---------------------------------------------------------------------------
# butlers board (bu-86c4c.17 / bu-qvnce.1)
# ---------------------------------------------------------------------------


def _case_board() -> DegradedCase:
    async def _run() -> None:
        configs = [
            ButlerConnectionInfo(name="finance", port=41105),
            ButlerConnectionInfo(name="general", port=41101),
        ]
        db = _BoardFakeDb(
            switchboard=_BoardSwitchboardPool(
                rows=[_board_registry_row("finance"), _board_registry_row("general")]
            ),
            butlers={
                "finance": _BoardButlerPool(hourly_counts=[2] * 24),
                "general": _BoardButlerPool(hourly_query_fails=True),
            },
        )
        app = _make_board_app(configs, db)
        resp = await _request(app, "GET", "/api/butlers/board")
        assert resp.status_code == 200
        payload = resp.json()["data"]
        rows_by_name = {r["name"]: r for r in payload["rows"]}
        assert rows_by_name["general"]["stripe_source_error"] is True
        assert payload["aggregates"]["sessions_source_error"] is True
        assert payload["aggregates"]["sources_partially_degraded"] is True

    return DegradedCase("butlers_board", _run)


# ---------------------------------------------------------------------------
# notifications (bu-qvnce.1)
# ---------------------------------------------------------------------------


def _case_notifications_list() -> DegradedCase:
    async def _run() -> None:
        app = create_app()
        app.dependency_overrides[_notifications_get_db] = _make_notifications_unavailable_db
        resp = await _request(app, "GET", "/api/notifications")
        assert resp.status_code == 200
        assert resp.json()["source_available"] is False

    return DegradedCase("notifications_list", _run)


def _case_notifications_stats() -> DegradedCase:
    async def _run() -> None:
        app = create_app()
        app.dependency_overrides[_notifications_get_db] = _make_notifications_unavailable_db
        resp = await _request(app, "GET", "/api/notifications/stats")
        assert resp.status_code == 200
        assert resp.json()["data"]["source_available"] is False

    return DegradedCase("notifications_stats", _run)


# ---------------------------------------------------------------------------
# memory (bu-qvnce.1)
# ---------------------------------------------------------------------------


def _case_memory_stats() -> DegradedCase:
    async def _run() -> None:
        db = _StatsDB(
            {
                "atlas": _StatsPool(counts={"consolidation_status = 'dead_letter'": 3}),
                "finance": _RaisingStatsPool(RuntimeError("connection reset by peer")),
            }
        )
        app = create_app()
        app.dependency_overrides[_memory_get_db] = lambda: db
        resp = await _request(app, "GET", "/api/memory/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["dead_letter_episodes"] == 3
        assert body["meta"]["pools_failed"] == ["finance"]

    return DegradedCase("memory_stats", _run)


def _case_memory_retention_stats() -> DegradedCase:
    async def _run() -> None:
        db = _StatsDB(
            {
                "atlas": _StatsPool(retention={"expired": 0, "eligible": 1}),
                "finance": _StatsPool(retention_exc=RuntimeError("connection reset by peer")),
            }
        )
        app = create_app()
        app.dependency_overrides[_memory_get_db] = lambda: db
        resp = await _request(app, "GET", "/api/memory/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["retention_pools_failed"] == ["finance"]
        assert body["meta"]["retention_status"] == "unknown"
        assert body["data"]["expired_retained_episodes"] is None

    return DegradedCase("memory_retention_stats", _run)


def _case_memory_list(endpoint: str, name: str) -> DegradedCase:
    async def _run() -> None:
        db = _MemoryFanOutDB(
            {
                "atlas": _EmptyMemoryPool(),
                "finance": _RaisingMemoryPool(RuntimeError("connection reset by peer")),
            }
        )
        app = create_app()
        app.dependency_overrides[_memory_get_db] = lambda: db
        resp = await _request(app, "GET", endpoint)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["pools_failed"] == ["finance"]

    return DegradedCase(name, _run)


def _case_memory_entities() -> DegradedCase:
    async def _run() -> None:
        db = _MemoryFanOutDB(
            {
                "atlas": _EntityListPool(uuid4()),
                "finance": _RaisingMemoryPool(RuntimeError("connection reset by peer")),
            }
        )
        app = create_app()
        app.dependency_overrides[_memory_get_db] = lambda: db
        resp = await _request(app, "GET", "/api/memory/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["fact_count"] == 0
        assert body["meta"]["pools_failed"] == ["finance"]

    return DegradedCase("memory_entities", _run)


def _case_memory_detail() -> DegradedCase:
    async def _run() -> None:
        db = _MemoryFanOutDB(
            {
                "atlas": _MemoryDetailPool(),
                "finance": _MemoryDetailPool(error=RuntimeError("connection reset by peer")),
            }
        )
        app = create_app()
        app.dependency_overrides[_memory_get_db] = lambda: db
        resp = await _request(app, "GET", f"/api/memory/episodes/{uuid4()}")
        assert resp.status_code == 503
        assert "finance" in resp.json()["detail"]

    return DegradedCase("memory_detail", _run)


def _case_memory_entity_detail() -> DegradedCase:
    async def _run() -> None:
        entity_id = uuid4()
        db = _MemoryFanOutDB(
            {
                "atlas": _EntityMemoryPool(entity=_make_entity_row(entity_id)),
                "finance": _EntityMemoryPool(fact_error=RuntimeError("connection reset by peer")),
            }
        )
        app = create_app()
        app.dependency_overrides[_memory_get_db] = lambda: db
        resp = await _request(app, "GET", f"/api/memory/entities/{entity_id}")
        assert resp.status_code == 200
        assert resp.json()["meta"]["pools_failed"] == ["finance"]

    return DegradedCase("memory_entity_detail", _run)


def _case_memory_inspect() -> DegradedCase:
    async def _run() -> None:
        db = _MemoryFanOutDB(
            {
                "atlas": _InspectPool(),
                "finance": _RaisingMemoryPool(RuntimeError("connection reset by peer")),
            }
        )
        app = create_app()
        app.dependency_overrides[_memory_get_db] = lambda: db
        resp = await _request(app, "GET", "/api/memory/inspect")
        assert resp.status_code == 200
        assert resp.json()["meta"]["pools_failed"] == ["finance"]

    return DegradedCase("memory_inspect", _run)


def _case_memory_reembed_pending() -> DegradedCase:
    async def _run() -> None:
        from butlers.modules.memory import reembedding as reembedding

        healthy_pool = object()
        failed_pool = object()
        db = _MemoryFanOutDB({"general": healthy_pool, "health": failed_pool})
        app = create_app()
        app.dependency_overrides[_memory_get_db] = lambda: db

        async def _count_pending(pool: object, *_args: object, **_kwargs: object) -> dict[str, int]:
            if pool is failed_pool:
                raise RuntimeError("connection reset by peer")
            return {"episodes": 0, "facts": 0, "rules": 0}

        with patch.object(reembedding, "count_pending", _count_pending):
            resp = await _request(app, "GET", "/api/memory/reembed/pending")

        assert resp.status_code == 200
        assert resp.json()["meta"]["pools_failed"] == ["health"]

    return DegradedCase("memory_reembed_pending", _run)


# ---------------------------------------------------------------------------
# approvals (bu-qvnce.1)
# ---------------------------------------------------------------------------


def _case_approvals_list() -> DegradedCase:
    async def _run() -> None:
        row = _make_approval_action(tool_name="notify")
        app = _app_with_one_healthy_one_raising_butler(create_app(), healthy_rows=[row])
        resp = await _request(app, "GET", "/api/approvals")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["meta"]["sources_degraded"] == ["home"]

    return DegradedCase("approvals_list", _run)


# ---------------------------------------------------------------------------
# secrets breaks-catalogue (bu-r724f / bu-qvnce.1)
# ---------------------------------------------------------------------------


def _case_secrets_catalogue() -> DegradedCase:
    async def _run() -> None:
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = []
        mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no pool"))
        app = create_app()
        app.dependency_overrides[_secrets_get_db] = lambda: mock_db

        resp = await _request(app, "GET", "/api/secrets/breaks-catalogue", {"provider": "google"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["catalogue_available"] is False

    return DegradedCase("secrets_breaks_catalogue", _run)


# ---------------------------------------------------------------------------
# spend (bu-qvnce.1)
# ---------------------------------------------------------------------------


def _case_spend_summary() -> DegradedCase:
    async def _run() -> None:
        app = create_app()
        configs = [
            ButlerConnectionInfo(name="sw", port=41100),
            ButlerConnectionInfo(name="broken", port=41101),
        ]
        sw_data = {
            "total_sessions": 2,
            "total_input_tokens": 1000,
            "total_output_tokens": 500,
            "by_model": {"claude-sonnet-4-20250514": {"input_tokens": 1000, "output_tokens": 500}},
        }
        mgr = _make_spend_mcp_mgr(
            {"sw": _make_tool_result(sw_data), "broken": ButlerUnreachableError("broken")}
        )
        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("ledger unavailable"))
        _wire_spend_db(
            _wire_spend_app(app, mgr, configs, _flat_pricing()),
            _mock_db({"switchboard": pool}),
        )

        resp = await _request(app, "GET", "/api/spend")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_cost_usd"] == 0.0
        assert data["total_sessions"] == 0
        assert data["total_input_tokens"] == 0
        assert data["total_output_tokens"] == 0
        assert data["by_butler"] == {}
        assert data["by_model"] == {}
        assert data["source_error"] is True
        # Ledger failures describe one failed source, not an invented list of
        # per-butler MCP failures. The compatibility field stays empty.
        assert data["unavailable_butlers"] == []
        mgr.get_client.assert_not_called()

    return DegradedCase("spend_summary", _run)


# ---------------------------------------------------------------------------
# settings console (bu-qvnce.1) -- HeaderCounts fields turn null, not 0
# ---------------------------------------------------------------------------

_CONSOLE_PRICING = PricingConfig(models={"claude-sonnet-4-6": ModelPricing(0.000003, 0.000015)})
_CONSOLE_BUTLERS = [ButlerConnectionInfo(name="general", port=41100)]


def _case_settings_console() -> DegradedCase:
    async def _run() -> None:
        console_mod._cache_ts = 0.0
        console_mod._cache_payload = None
        try:
            app = create_app(api_key="")
            mock_mgr = MagicMock(spec=MCPClientManager)
            mock_mgr.get_client = AsyncMock(side_effect=Exception("unreachable in tests"))
            app.dependency_overrides[get_butler_configs] = lambda: _CONSOLE_BUTLERS
            app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
            app.dependency_overrides[get_pricing] = lambda: _CONSOLE_PRICING
            app.dependency_overrides[console_mod._get_db_manager] = lambda: None

            spend_err_item = console_mod.AttentionItem(
                tone="amber",
                kind="subsystem_error",
                text="Could not fetch spend data -- totals may be unavailable.",
                action_route="/settings/spend",
            )

            with (
                patch.object(
                    console_mod, "_count_active_butlers", new=AsyncMock(return_value=(3, None))
                ),
                patch.object(
                    console_mod,
                    "_get_spend_mtd",
                    new=AsyncMock(return_value=(0.0, None, spend_err_item)),
                ),
                patch.object(
                    console_mod, "_count_open_approvals", new=AsyncMock(return_value=(2, None))
                ),
                patch.object(
                    console_mod, "_count_models", new=AsyncMock(return_value=(1, 1, None))
                ),
                patch.object(console_mod, "_check_cli_auth", new=AsyncMock(return_value=[])),
                patch.object(console_mod, "_check_model_errors", new=AsyncMock(return_value=[])),
                patch.object(console_mod, "_check_failed_webhooks", new=AsyncMock(return_value=[])),
            ):
                resp = await _request(app, "GET", "/api/settings/console")

            assert resp.status_code == 200
            counts = resp.json()["data"]["header_counts"]
            # The failed subsystem's field is None, not a confident 0 -- a
            # header-only consumer cannot otherwise tell "genuinely zero" from
            # "unknown" without cross-referencing the attention list.
            assert counts["spend_mtd_usd"] is None
            # Healthy sibling subsystems keep their real values.
            assert counts["active_butlers"] == 3
            assert counts["open_approvals"] == 2
        finally:
            console_mod._cache_ts = 0.0
            console_mod._cache_payload = None

    return DegradedCase("settings_console_header_counts", _run)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: list[DegradedCase] = [
    _case_sessions_list(),
    _case_sessions_aggregate(),
    _case_sessions_detail(),
    _case_search(),
    _case_issues(),
    _case_calendar_entries(),
    _case_calendar_conflicts(),
    _case_calendar_audit(),
    _case_calendar_duplicates(),
    _case_board(),
    _case_notifications_list(),
    _case_notifications_stats(),
    _case_memory_stats(),
    _case_memory_retention_stats(),
    _case_memory_list("/api/memory/episodes", "memory_episodes"),
    _case_memory_list("/api/memory/facts", "memory_facts"),
    _case_memory_list("/api/memory/rules", "memory_rules"),
    _case_memory_list("/api/memory/activity", "memory_activity"),
    _case_memory_entities(),
    _case_memory_detail(),
    _case_memory_entity_detail(),
    _case_memory_inspect(),
    _case_memory_reembed_pending(),
    _case_approvals_list(),
    _case_secrets_catalogue(),
    _case_spend_summary(),
    _case_settings_console(),
]


def test_registry_names_are_unique() -> None:
    names = [case.name for case in REGISTRY]
    assert len(names) == len(set(names)), f"duplicate case names: {names}"


def test_registry_is_non_empty() -> None:
    assert REGISTRY


@pytest.mark.parametrize("case", REGISTRY, ids=[c.name for c in REGISTRY])
async def test_source_failure_surfaces_degraded_flag(case: DegradedCase) -> None:
    """A genuine source failure on this surface is named, never zero-filled."""
    await case.run()

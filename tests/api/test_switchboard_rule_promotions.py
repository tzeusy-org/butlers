"""Unit tests for the rule-promotion approvals REST surface (bu-o62bc, bead 4).

Mocked-pool coverage of the four switchboard endpoints:
- GET  /rule-promotion-suggestions  (pending cards + auto-applied info; degraded flag)
- POST /rule-promotion-suggestions/{id}/confirm   (mint on owner confirm)
- POST /rule-promotion-suggestions/{id}/dismiss
- POST /rule-promotion-suggestions/{id}/rule-enabled  (reversible disable/enable)

The mint/auto-apply DB transaction logic itself is covered against real Postgres
in tests/integration/test_switchboard_rule_promotion_apply.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests.api.test_switchboard import _app_with_mock, _make_row

pytestmark = pytest.mark.unit

_TS = datetime(2026, 7, 5, 0, 0, 0, tzinfo=UTC)

_APPLY_MOD = "butlers.tools.switchboard.routing.rule_promotion_apply"


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_list_returns_pending_and_auto_applied(app):
    pending = [
        _make_row(
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "sender_key": "alerts@acme.com",
                "source_channel": "gmail",
                "proposed_rule_type": "sender_address",
                "proposed_condition": {"address": "alerts@acme.com"},
                "proposed_action": "route_to:finance",
                "evidence_count": 4,
                "is_clearly_automated": False,
                "first_evidence_at": _TS,
                "last_evidence_at": _TS,
                "created_at": _TS,
            }
        )
    ]
    auto_applied = [
        _make_row(
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "sender_key": "noreply@acme.com",
                "source_channel": "gmail",
                "proposed_action": "metadata_only",
                "evidence_count": 6,
                "created_rule_id": "33333333-3333-3333-3333-333333333333",
                "decided_at": _TS,
                "decided_by": "auto:promotion",
                "rule_enabled": True,
            }
        )
    ]
    _app, mock_pool = _app_with_mock(app)
    mock_pool.fetch = AsyncMock(side_effect=[pending, auto_applied])

    async with _client(app) as client:
        resp = await client.get("/api/switchboard/rule-promotion-suggestions")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["pending"]) == 1
    assert data["pending"][0]["proposed_action"] == "route_to:finance"
    assert len(data["auto_applied"]) == 1
    assert data["auto_applied"][0]["proposed_action"] == "metadata_only"
    assert data["auto_applied"][0]["rule_enabled"] is True
    # No degraded flag on the happy path.
    assert not resp.json()["meta"].get("sources_degraded")


async def test_list_flags_degraded_when_pending_query_fails(app):
    """A genuine query failure must never render as a fabricated 'nothing pending'."""
    _app, mock_pool = _app_with_mock(app)
    mock_pool.fetch = AsyncMock(side_effect=[RuntimeError("boom"), []])

    async with _client(app) as client:
        resp = await client.get("/api/switchboard/rule-promotion-suggestions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["pending"] == []
    assert "rule_promotion_pending" in body["meta"]["sources_degraded"]


async def test_confirm_mints_rule(app):
    minted = _make_row(
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "scope": "global",
            "rule_type": "sender_address",
            "condition": {"address": "alerts@acme.com"},
            "action": "route_to:finance",
            "priority": 10,
            "enabled": True,
            "name": "Promoted",
            "description": "",
            "created_by": "promotion",
            "created_at": "2026-07-06T00:00:00+00:00",
            "updated_at": "2026-07-06T00:00:00+00:00",
            "deleted_at": None,
        }
    )
    _app_with_mock(app)
    with patch(f"{_APPLY_MOD}.apply_suggestion", new=AsyncMock(return_value=minted)) as apply_mock:
        async with _client(app) as client:
            resp = await client.post(
                "/api/switchboard/rule-promotion-suggestions/"
                "11111111-1111-1111-1111-111111111111/confirm"
            )
    assert resp.status_code == 200
    assert resp.json()["data"]["created_by"] == "promotion"
    # decided_by is the owner on an explicit confirm.
    assert apply_mock.await_args.kwargs["decided_by"] == "owner"


async def test_confirm_conflict_maps_status(app):
    from butlers.tools.switchboard.routing.rule_promotion_apply import SuggestionNotApplicable

    _app_with_mock(app)
    with patch(
        f"{_APPLY_MOD}.apply_suggestion",
        new=AsyncMock(side_effect=SuggestionNotApplicable("already decided", status_code=409)),
    ):
        async with _client(app) as client:
            resp = await client.post(
                "/api/switchboard/rule-promotion-suggestions/"
                "11111111-1111-1111-1111-111111111111/confirm"
            )
    assert resp.status_code == 409


async def test_confirm_invalid_id_is_422(app):
    _app_with_mock(app)
    async with _client(app) as client:
        resp = await client.post("/api/switchboard/rule-promotion-suggestions/not-a-uuid/confirm")
    assert resp.status_code == 422


async def test_dismiss_pending_suggestion(app):
    _app, mock_pool = _app_with_mock(app, fetchrow_result=_make_row({"status": "pending_review"}))
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")
    async with _client(app) as client:
        resp = await client.post(
            "/api/switchboard/rule-promotion-suggestions/"
            "11111111-1111-1111-1111-111111111111/dismiss",
            json={"reason": "not useful", "cooldown_days": 14},
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "dismissed"
    mock_pool.execute.assert_awaited()


async def test_dismiss_missing_is_404(app):
    _app_with_mock(app, fetchrow_result=None)
    async with _client(app) as client:
        resp = await client.post(
            "/api/switchboard/rule-promotion-suggestions/"
            "11111111-1111-1111-1111-111111111111/dismiss",
            json={},
        )
    assert resp.status_code == 404


async def test_dismiss_already_decided_is_409(app):
    _app_with_mock(app, fetchrow_result=_make_row({"status": "confirmed"}))
    async with _client(app) as client:
        resp = await client.post(
            "/api/switchboard/rule-promotion-suggestions/"
            "11111111-1111-1111-1111-111111111111/dismiss",
            json={},
        )
    assert resp.status_code == 409


async def test_dismiss_does_not_overwrite_a_concurrent_supersede(app):
    """The owner dismissal must lose cleanly if the trigger decides first."""
    _app, mock_pool = _app_with_mock(app, fetchrow_result=_make_row({"status": "pending_review"}))
    mock_pool.execute = AsyncMock(return_value="UPDATE 0")

    async with _client(app) as client:
        resp = await client.post(
            "/api/switchboard/rule-promotion-suggestions/"
            "11111111-1111-1111-1111-111111111111/dismiss",
            json={},
        )

    assert resp.status_code == 409
    update_sql = mock_pool.execute.await_args.args[0]
    assert "AND status = 'pending_review'" in update_sql


async def test_rule_enabled_toggles_minted_rule(app):
    _app, mock_pool = _app_with_mock(app)
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            _make_row({"created_rule_id": "33333333-3333-3333-3333-333333333333"}),
            _make_row({"id": "33333333-3333-3333-3333-333333333333"}),
        ]
    )
    async with _client(app) as client:
        resp = await client.post(
            "/api/switchboard/rule-promotion-suggestions/"
            "22222222-2222-2222-2222-222222222222/rule-enabled",
            json={"enabled": False},
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["enabled"] is False


async def test_rule_enabled_no_minted_rule_is_409(app):
    _app_with_mock(app, fetchrow_result=_make_row({"created_rule_id": None}))
    async with _client(app) as client:
        resp = await client.post(
            "/api/switchboard/rule-promotion-suggestions/"
            "22222222-2222-2222-2222-222222222222/rule-enabled",
            json={"enabled": True},
        )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /rule-promotion-stats  (bead 6, bu-hb61f)
# ---------------------------------------------------------------------------


def _stats_fetch_router(*, counts=None, ids=None, counts_exc=None, ids_exc=None):
    """Route the stats endpoint's two ``pool.fetch`` calls by SQL.

    Block 1 fetches from ``rule_promotion_suggestions`` (the status counts);
    block 3 fetches promoted-rule ids from ``ingestion_rules``. Routing by SQL
    keeps the mock robust to call order.
    """

    async def _fetch(sql, *args):
        if "FROM switchboard.rule_promotion_suggestions" in sql:
            if counts_exc is not None:
                raise counts_exc
            return counts or []
        if "FROM switchboard.ingestion_rules" in sql:
            if ids_exc is not None:
                raise ids_exc
            return ids or []
        raise AssertionError(f"unexpected fetch SQL: {sql}")

    return _fetch


async def test_stats_happy_path(app):
    """Aggregate metrics are computed from the sub-queries."""
    counts = [
        _make_row({"suggestion_kind": "promotion", "status": "pending_review", "n": 3}),
        _make_row({"suggestion_kind": "promotion", "status": "confirmed", "n": 7}),
        _make_row({"suggestion_kind": "promotion", "status": "dismissed", "n": 2}),
        _make_row({"suggestion_kind": "promotion", "status": "superseded", "n": 4}),
        _make_row({"suggestion_kind": "demotion", "status": "pending_review", "n": 1}),
    ]
    ids = [_make_row({"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})]
    verdict = _make_row({"matches": 128, "spot_checks": 40})
    _app, mock_pool = _app_with_mock(app)
    mock_pool.fetch = AsyncMock(side_effect=_stats_fetch_router(counts=counts, ids=ids))
    mock_pool.fetchval = AsyncMock(return_value=5)
    mock_pool.fetchrow = AsyncMock(return_value=verdict)

    async with _client(app) as client:
        resp = await client.get("/api/switchboard/rule-promotion-stats")

    assert resp.status_code == 200
    body = resp.json()
    d = body["data"]
    assert d["suggestions_pending"] == 3
    assert d["suggestions_confirmed"] == 7
    assert d["suggestions_dismissed"] == 2
    assert d["suggestions_superseded"] == 4
    assert d["demotion_pending"] == 1
    assert d["promoted_rules_active"] == 5
    assert d["promoted_rule_matches"] == 128
    # Honest estimate: one promoted-rule match == one avoided LLM session.
    assert d["llm_sessions_avoided_estimate"] == 128
    assert d["promoted_rule_spot_checks"] == 40
    assert not body["meta"].get("sources_degraded")


async def test_stats_no_promoted_rules_skips_verdict_query(app):
    """With no promoted rules, verdict metrics are 0 and not degraded."""
    counts = [_make_row({"suggestion_kind": "promotion", "status": "pending_review", "n": 1})]
    _app, mock_pool = _app_with_mock(app)
    mock_pool.fetch = AsyncMock(side_effect=_stats_fetch_router(counts=counts, ids=[]))
    mock_pool.fetchval = AsyncMock(return_value=0)
    # fetchrow must never be called when there are no promoted rule ids.
    mock_pool.fetchrow = AsyncMock(side_effect=AssertionError("verdict query should be skipped"))

    async with _client(app) as client:
        resp = await client.get("/api/switchboard/rule-promotion-stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["promoted_rule_matches"] == 0
    assert body["data"]["llm_sessions_avoided_estimate"] == 0
    assert not body["meta"].get("sources_degraded")


async def test_stats_flags_degraded_verdict_block(app):
    """A failed verdict-log scan must flag its source, never read as zero savings."""
    counts = [_make_row({"suggestion_kind": "promotion", "status": "confirmed", "n": 4})]
    ids = [_make_row({"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})]
    _app, mock_pool = _app_with_mock(app)
    mock_pool.fetch = AsyncMock(side_effect=_stats_fetch_router(counts=counts, ids=ids))
    mock_pool.fetchval = AsyncMock(return_value=2)
    mock_pool.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))

    async with _client(app) as client:
        resp = await client.get("/api/switchboard/rule-promotion-stats")

    assert resp.status_code == 200
    body = resp.json()
    # The other blocks still computed.
    assert body["data"]["suggestions_confirmed"] == 4
    assert body["data"]["promoted_rules_active"] == 2
    # The verdict block is flagged, and its fields are the un-fabricated 0.
    assert "verdict_metrics" in body["meta"]["sources_degraded"]
    assert body["data"]["promoted_rule_matches"] == 0
    assert body["data"]["llm_sessions_avoided_estimate"] == 0


async def test_stats_flags_degraded_suggestion_and_rules_blocks(app):
    """Each block degrades independently."""
    ids = [_make_row({"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})]
    _app, mock_pool = _app_with_mock(app)
    # Block 1 (suggestion counts) fails; block 3's id fetch succeeds.
    mock_pool.fetch = AsyncMock(
        side_effect=_stats_fetch_router(counts_exc=RuntimeError("boom"), ids=ids)
    )
    mock_pool.fetchval = AsyncMock(side_effect=RuntimeError("boom"))
    mock_pool.fetchrow = AsyncMock(return_value=_make_row({"matches": 9, "spot_checks": 0}))

    async with _client(app) as client:
        resp = await client.get("/api/switchboard/rule-promotion-stats")

    assert resp.status_code == 200
    body = resp.json()
    degraded = body["meta"]["sources_degraded"]
    assert "suggestion_counts" in degraded
    assert "promoted_rules" in degraded
    assert "verdict_metrics" not in degraded
    assert body["data"]["promoted_rule_matches"] == 9

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

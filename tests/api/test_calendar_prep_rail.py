"""Tests for the calendar meeting-prep rail read endpoint.

``GET /api/calendar/workspace/prep/{event_id}`` projects precomputed prep
contributions from the cached cross-schema view ``calendar.v_prep_contributions``
(migration ``core_142``). These tests pin the doctrine the bead is about:

- populated read returns attendees + relationship notes + last-met from the
  contribution-sourced cached data;
- honest structured empty-state when no prep contribution exists (the expected
  state for most events today);
- fail-open (never HTTP 500) when the view is missing/unreadable;
- the no-direct-cross-schema-read and no-LLM guarantees (the read touches ONLY
  the prep view and never opens an MCP/LLM session at request time);
- cross-butler merge by attendee (relationship context + a future email-owning
  butler's message context) and the butler-mismatch guardrail.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.routers.calendar_workspace import _get_db_manager

pytestmark = pytest.mark.unit


def _prep_row(
    *,
    butler: str,
    event_id: str,
    attendees: list[dict] | None = None,
    envelope_butler: str | None = "__match__",
    event_title: str = "Lunch with the team",
) -> dict:
    """Build a ``calendar.v_prep_contributions`` row (column + JSONB value).

    ``butler`` is the view's hardcoded source column; ``envelope_butler`` is the
    ``value->>'butler'`` field — ``"__match__"`` (default) makes it equal the
    column (the valid case), and an explicit value exercises the mismatch guard.
    """
    payload_attendees = attendees or []
    payload_butler = butler if envelope_butler == "__match__" else envelope_butler
    return {
        "butler": butler,
        "key": f"calendar/prep/{event_id}",
        "value": {
            "butler": payload_butler,
            "event_id": event_id,
            "event_title": event_title,
            "event_starts_at": "2026-02-22T12:00:00+00:00",
            "has_context": len(payload_attendees) > 0,
            "attendees": payload_attendees,
        },
    }


def _attendee(
    *,
    entity_id: str,
    name: str,
    dunbar_tier: int | None = None,
    notes: list[dict] | None = None,
    last_met: str | None = None,
    last_met_event: str | None = None,
    message_context: list[dict] | None = None,
) -> dict:
    return {
        "entity_id": entity_id,
        "name": name,
        "dunbar_tier": dunbar_tier,
        "notes": notes or [],
        "last_met": last_met,
        "last_met_event": last_met_event,
        "message_context": message_context or [],
    }


def _build_prep_app(
    app,
    *,
    prep_rows: dict[str, list[dict]] | None = None,
    prep_raise: bool = False,
    calendar_butlers: list[str] | None = None,
) -> tuple:
    """Wire the shared app with a fan_out mock that serves only the prep view."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=calendar_butlers)
    seen_queries: list[str] = []

    async def _fan_out(query: str, args=(), butler_names=None):
        seen_queries.append(query)
        if "FROM calendar.v_prep_contributions" in query:
            if prep_raise:
                raise RuntimeError('relation "calendar.v_prep_contributions" does not exist')
            rows_to_scan = prep_rows or {}
            if butler_names is not None:
                rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}
            return rows_to_scan
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)
    mock_db.seen_queries = seen_queries  # type: ignore[attr-defined]

    mock_mgr = AsyncMock(spec=MCPClientManager)

    async def _get_client(name: str):  # pragma: no cover - must never be reached
        raise AssertionError("prep rail must not open an MCP/LLM session")

    mock_mgr.get_client = AsyncMock(side_effect=_get_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db, mock_mgr


async def _get_prep(app, event_id: str):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/calendar/workspace/prep/{event_id}")


# ---------------------------------------------------------------------------
# Populated read + the no-direct-read / no-LLM guarantees
# ---------------------------------------------------------------------------


async def test_prep_rail_returns_precomputed_context(app):
    """Precomputed prep contributions yield attendees + notes + last-met, no LLM."""
    event_id = str(uuid4())
    e1, e2 = str(uuid4()), str(uuid4())
    row = _prep_row(
        butler="relationship",
        event_id=event_id,
        attendees=[
            _attendee(
                entity_id=e1,
                name="Alice Tan",
                dunbar_tier=5,
                notes=[{"kind": "contact_note", "text": "Allergic to shellfish"}],
                last_met="2026-01-10T12:00:00+00:00",
                last_met_event="Quarterly sync",
            ),
            _attendee(entity_id=e2, name="Bob Lee"),
        ],
    )
    app, mock_db, mock_mgr = _build_prep_app(
        app, prep_rows={"relationship": [row]}, calendar_butlers=["relationship"]
    )

    resp = await _get_prep(app, event_id)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["event_id"] == event_id
    assert body["has_prep_context"] is True
    assert body["source_butlers"] == ["relationship"]
    # Attendees are ordered by name; notes + last-met carried through.
    names = [a["name"] for a in body["attendees"]]
    assert names == ["Alice Tan", "Bob Lee"]
    alice = body["attendees"][0]
    assert alice["dunbar_tier"] == 5
    assert alice["notes"] == [{"kind": "contact_note", "text": "Allergic to shellfish"}]
    assert alice["last_met"] == "2026-01-10T12:00:00+00:00"
    assert alice["last_met_event"] == "Quarterly sync"

    # NO LLM/MCP session at request time.
    mock_mgr.get_client.assert_not_called()
    # NO direct cross-schema read: every query touched ONLY the prep view, never
    # relationship.* / health.* tables directly.
    assert mock_db.seen_queries, "expected at least one read"
    for q in mock_db.seen_queries:
        assert "calendar.v_prep_contributions" in q
        assert "relationship." not in q
        assert "health." not in q


# ---------------------------------------------------------------------------
# Honest empty-state + fail-open
# ---------------------------------------------------------------------------


async def test_prep_rail_honest_empty_state_when_no_contribution(app):
    """No prep contribution for the event → structured empty payload, not 500."""
    event_id = str(uuid4())
    app, _, mock_mgr = _build_prep_app(app, prep_rows={}, calendar_butlers=["relationship"])

    resp = await _get_prep(app, event_id)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["event_id"] == event_id
    assert body["has_prep_context"] is False
    assert body["attendees"] == []
    assert body["source_butlers"] == []
    mock_mgr.get_client.assert_not_called()


async def test_prep_rail_fail_open_on_missing_view(app):
    """A missing/unreadable view degrades to the empty-state, never HTTP 500."""
    event_id = str(uuid4())
    app, _, _ = _build_prep_app(app, prep_raise=True, calendar_butlers=["relationship"])

    resp = await _get_prep(app, event_id)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["has_prep_context"] is False
    assert body["attendees"] == []


async def test_prep_rail_contribution_with_no_attendees_is_real_context(app):
    """An envelope that resolved zero attendees still counts as 'a job ran'."""
    event_id = str(uuid4())
    row = _prep_row(butler="relationship", event_id=event_id, attendees=[])
    app, _, _ = _build_prep_app(
        app, prep_rows={"relationship": [row]}, calendar_butlers=["relationship"]
    )

    resp = await _get_prep(app, event_id)

    body = resp.json()["data"]
    # Honest empty-state distinguishes "contribution exists, empty" from "no job".
    assert body["has_prep_context"] is True
    assert body["attendees"] == []
    assert body["source_butlers"] == ["relationship"]


# ---------------------------------------------------------------------------
# Cross-butler merge + guardrail
# ---------------------------------------------------------------------------


async def test_prep_rail_merges_message_context_across_butlers(app):
    """A future email-owning butler's message context merges into the attendee."""
    event_id = str(uuid4())
    eid = str(uuid4())
    rel = _prep_row(
        butler="relationship",
        event_id=event_id,
        attendees=[
            _attendee(
                entity_id=eid,
                name="Alice Tan",
                notes=[{"kind": "contact_note", "text": "Prefers morning calls"}],
            )
        ],
    )
    msg = _prep_row(
        butler="messenger",
        event_id=event_id,
        attendees=[
            _attendee(
                entity_id=eid,
                name="Alice Tan",
                message_context=[{"subject": "Re: agenda", "snippet": "see you then"}],
            )
        ],
    )
    # The cross-schema UNION view returns every butler's rows through one reader.
    app, _, _ = _build_prep_app(
        app,
        prep_rows={"relationship": [rel, msg]},
        calendar_butlers=["relationship"],
    )

    resp = await _get_prep(app, event_id)

    body = resp.json()["data"]
    assert sorted(body["source_butlers"]) == ["messenger", "relationship"]
    # Single merged attendee carrying BOTH the relationship note and the message.
    assert len(body["attendees"]) == 1
    alice = body["attendees"][0]
    assert alice["notes"] == [{"kind": "contact_note", "text": "Prefers morning calls"}]
    assert alice["message_context"] == [{"subject": "Re: agenda", "snippet": "see you then"}]


async def test_prep_rail_skips_butler_mismatch(app):
    """An envelope whose payload butler != the view's source column is skipped."""
    event_id = str(uuid4())
    row = _prep_row(
        butler="relationship",
        event_id=event_id,
        attendees=[_attendee(entity_id=str(uuid4()), name="Mallory")],
        envelope_butler="health",  # disagrees with the hardcoded column
    )
    app, _, _ = _build_prep_app(
        app, prep_rows={"relationship": [row]}, calendar_butlers=["relationship"]
    )

    resp = await _get_prep(app, event_id)

    body = resp.json()["data"]
    assert body["has_prep_context"] is False
    assert body["attendees"] == []
    assert body["source_butlers"] == []


async def test_prep_rail_skips_missing_payload_butler(app):
    """An envelope with a missing/null payload ``butler`` is malformed → skipped."""
    event_id = str(uuid4())
    row = _prep_row(
        butler="relationship",
        event_id=event_id,
        attendees=[_attendee(entity_id=str(uuid4()), name="Mallory")],
        envelope_butler=None,  # malformed: payload omits the butler literal
    )
    app, _, _ = _build_prep_app(
        app, prep_rows={"relationship": [row]}, calendar_butlers=["relationship"]
    )

    resp = await _get_prep(app, event_id)

    body = resp.json()["data"]
    assert body["has_prep_context"] is False
    assert body["attendees"] == []
    assert body["source_butlers"] == []

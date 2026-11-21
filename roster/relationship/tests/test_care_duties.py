"""Care duties (bu-s11n0s.13 S1): validation in the tool module and the dashboard HTTP contract.

The SQL is exercised on real PostgreSQL in ``test_care_duties_db.py``; these tests pin what the
owner can get wrong and what the API must refuse: shape, rule validity, which starts a rule
produces, entity existence, the owner gate, and that a duty never reports coverage.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db, get_mcp_manager
from butlers.roster_imports import roster_module
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

care = roster_module("relationship", "tools/care_duties")

pytestmark = pytest.mark.unit

_SUBJECT = uuid.uuid4()
_CARER = uuid.uuid4()
_DUTY = uuid.uuid4()
_ANCHOR = datetime(2026, 11, 24, 15, 15, tzinfo=UTC)  # a Tuesday
_BASE = "/api/relationship/care"


def _duty_row(**over):
    row = {
        "id": _DUTY,
        "subject_entity_id": _SUBJECT,
        "title": "Mia pickup",
        "kind": "calendar_anchored",
        "rrule": "FREQ=WEEKLY;BYDAY=TU",
        "interval_days": None,
        "anchor_at": _ANCHOR,
        "window_minutes": 30,
        "lead_time_hours": 24,
        "default_carrier_entity_id": None,
        "retired_at": None,
        "created_at": _ANCHOR,
        "updated_at": _ANCHOR,
    }
    row.update(over)
    return row


def _pool(*, entity_exists: bool = True, duty: dict | None = None) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
    pool.fetchrow = AsyncMock(return_value=duty)
    pool.fetch = AsyncMock(return_value=[])
    return pool


class TestCreateDuty:
    async def _create(self, pool, **over):
        args = {
            "subject_entity_id": _SUBJECT,
            "title": "Mia pickup",
            "kind": "calendar_anchored",
            "rrule": "FREQ=WEEKLY;BYDAY=TU",
            "interval_days": None,
            "anchor_at": _ANCHOR,
        }
        args.update(over)
        return await care.create_duty(pool, **args)

    async def test_declares_a_calendar_duty(self):
        pool = _pool(duty=_duty_row())
        row = await self._create(pool)
        assert row["title"] == "Mia pickup"
        pool.fetchrow.assert_awaited_once()

    @pytest.mark.parametrize(
        "over",
        [
            {"rrule": None},
            {"interval_days": 7},
            {"kind": "completion_anchored", "rrule": "FREQ=DAILY", "interval_days": 3},
            {"kind": "completion_anchored", "rrule": None, "interval_days": None},
            {"kind": "weekly"},
        ],
    )
    async def test_mixed_or_missing_shape_is_refused(self, over):
        pool = _pool(duty=_duty_row())
        with pytest.raises(care.CareDutyError) as exc:
            await self._create(pool, **over)
        assert exc.value.code == "invalid_shape"
        pool.fetchrow.assert_not_awaited()

    async def test_invalid_rrule_is_refused(self):
        with pytest.raises(care.CareDutyError) as exc:
            await self._create(_pool(), rrule="FREQ=SOMETIMES")
        assert exc.value.code == "invalid_rrule"

    async def test_blank_title_is_refused(self):
        with pytest.raises(care.CareDutyError) as exc:
            await self._create(_pool(), title="   ")
        assert exc.value.code == "invalid_title"

    async def test_unknown_subject_is_not_found_and_nothing_is_written(self):
        pool = _pool(entity_exists=False)
        with pytest.raises(care.CareDutyError) as exc:
            await self._create(pool)
        assert exc.value.code == "not_found"
        pool.fetchrow.assert_not_awaited()


class TestAssignOccurrence:
    async def test_only_a_start_the_rule_produces_is_accepted(self):
        pool = _pool(duty=_duty_row())
        with pytest.raises(care.CareDutyError) as exc:
            await care.assign_occurrence(
                pool, _DUTY, datetime(2026, 11, 25, 15, 15, tzinfo=UTC), carrier_entity_id=_CARER
            )
        assert exc.value.code == "not_an_occurrence"

    async def test_a_produced_start_is_upserted(self):
        start = datetime(2026, 12, 1, 15, 15, tzinfo=UTC)
        pool = _pool()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _duty_row(),
                {
                    "id": uuid.uuid4(),
                    "duty_id": _DUTY,
                    "occurrence_start": start,
                    "carrier_entity_id": _CARER,
                    "state": "confirmed",
                    "reason": None,
                    "updated_at": start,
                },
            ]
        )
        row = await care.assign_occurrence(pool, _DUTY, start, carrier_entity_id=_CARER)
        assert row["state"] == "confirmed"
        sql = pool.fetchrow.await_args_list[1].args[0]
        assert "ON CONFLICT (duty_id, occurrence_start)" in sql

    async def test_a_retired_duty_takes_no_assignment(self):
        pool = _pool(duty=_duty_row(retired_at=_ANCHOR))
        with pytest.raises(care.CareDutyError) as exc:
            await care.assign_occurrence(pool, _DUTY, _ANCHOR, carrier_entity_id=_CARER)
        assert exc.value.code == "retired"

    async def test_completion_duty_refuses_a_start_before_its_anchor(self):
        duty = _duty_row(kind="completion_anchored", rrule=None, interval_days=30)
        pool = _pool(duty=duty)
        with pytest.raises(care.CareDutyError) as exc:
            await care.assign_occurrence(
                pool, _DUTY, datetime(2026, 1, 1, tzinfo=UTC), carrier_entity_id=_CARER
            )
        assert exc.value.code == "not_an_occurrence"

    async def test_unknown_carer_is_not_found(self):
        pool = _pool(entity_exists=False, duty=_duty_row())
        with pytest.raises(care.CareDutyError) as exc:
            await care.assign_occurrence(pool, _DUTY, _ANCHOR, carrier_entity_id=_CARER)
        assert exc.value.code == "not_found"


class TestNoOutboundSurface:
    def test_the_module_imports_nothing_that_can_contact_anyone(self):
        """Consent line: only stdlib and the rule parser, so no notify, MCP or HTTP client."""
        tree = ast.parse(Path(str(care.__file__)).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert imported <= {"__future__", "datetime", "typing", "uuid", "dateutil.rrule"}


def _make_app(*, caller_is_owner: bool = True):
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)
    pool.fetchrow = AsyncMock(
        return_value={"id": _SUBJECT, "roles": ["owner"] if caller_is_owner else []}
    )
    pool.fetch = AsyncMock(return_value=[])
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app: FastAPI = create_app(api_key="")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    return app, pool


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestApi:
    async def test_non_owner_is_refused_everywhere(self):
        app, _ = _make_app(caller_is_owner=False)
        async with _client(app) as client:
            for response in (
                await client.get(f"{_BASE}/duties"),
                await client.get(f"{_BASE}/occurrences"),
                await client.post(f"{_BASE}/duties/{_DUTY}/retire"),
                await client.put(f"{_BASE}/duties/{_DUTY}/carrier", json={}),
            ):
                assert response.status_code == 403, response.text
                assert response.json()["code"] == "owner_required"

    async def test_create_returns_201(self, monkeypatch):
        monkeypatch.setattr(care, "create_duty", AsyncMock(return_value=_duty_row()))
        app, _ = _make_app()
        async with _client(app) as client:
            response = await client.post(
                f"{_BASE}/duties",
                json={
                    "subject_entity_id": str(_SUBJECT),
                    "title": "Mia pickup",
                    "kind": "calendar_anchored",
                    "rrule": "FREQ=WEEKLY;BYDAY=TU",
                    "anchor_at": _ANCHOR.isoformat(),
                },
            )
        assert response.status_code == 201, response.text
        assert response.json()["title"] == "Mia pickup"

    async def test_tool_errors_map_to_422_and_404(self, monkeypatch):
        app, _ = _make_app()
        body = {
            "subject_entity_id": str(_SUBJECT),
            "title": "x",
            "kind": "calendar_anchored",
            "rrule": "FREQ=WEEKLY",
            "anchor_at": _ANCHOR.isoformat(),
        }
        async with _client(app) as client:
            monkeypatch.setattr(
                care,
                "create_duty",
                AsyncMock(side_effect=care.CareDutyError("invalid_rrule", "bad")),
            )
            bad = await client.post(f"{_BASE}/duties", json=body)
            monkeypatch.setattr(
                care,
                "create_duty",
                AsyncMock(side_effect=care.CareDutyError("not_found", "Subject entity not found")),
            )
            missing = await client.post(f"{_BASE}/duties", json=body)
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_rrule"
        assert missing.status_code == 404

    async def test_listing_reports_only_recorded_state(self, monkeypatch):
        recorded = {
            "id": uuid.uuid4(),
            "duty_id": _DUTY,
            "occurrence_start": _ANCHOR,
            "carrier_entity_id": _CARER,
            "state": "assumed",
            "reason": None,
            "title": "Mia pickup",
            "subject_entity_id": _SUBJECT,
        }
        monkeypatch.setattr(care, "list_occurrences", AsyncMock(return_value=[recorded]))
        app, _ = _make_app()
        async with _client(app) as client:
            response = await client.get(f"{_BASE}/occurrences")
        assert response.status_code == 200
        assert [o["state"] for o in response.json()["occurrences"]] == ["assumed"]

    async def test_inverted_window_is_refused(self):
        app, _ = _make_app()
        async with _client(app) as client:
            response = await client.get(
                f"{_BASE}/occurrences",
                params={"start": "2026-12-02T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
            )
        assert response.status_code == 422

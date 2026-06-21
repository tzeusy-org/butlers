"""Unit tests for the health butler endpoints that read SPO facts (bu-7oyhi.1).

Verifies that the medications / medication-doses / conditions / symptoms /
research endpoints read from the ``facts`` table (scope = ``health``,
validity = ``active``) by predicate — the same surface written by the
corresponding MCP tools (``medication_add``, ``medication_log_dose``,
``condition_add``, ``symptom_log``, ``research_save``).

Predicate mapping:
- medications        -> predicate = 'medication'  (metadata: name/dosage/...)
- medication doses   -> predicate = 'took_dose'   (metadata: medication_id/skipped)
- conditions         -> predicate = 'condition'   (metadata: name/status/...)
- symptoms           -> predicate = 'symptom'     (content = name, metadata.severity)
- research           -> predicate = 'research'    (content = body, metadata.title/tags)

Each suite includes a regression guard asserting no query touches the legacy
orphaned relational table (``medications``, ``medication_doses``, ``conditions``,
``symptoms``, ``research``).
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)

# Trigger router discovery, then grab the dependency fn from the registered module.
_APP_SEED = create_app(api_key="")
_health_get_db_manager = sys.modules["health_api_router"]._get_db_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass mimicking asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


def _make_app(*, fetch_rows=None, fetchval_result=0):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchval = AsyncMock(return_value=fetchval_result)

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    return app, pool


def _all_sql(pool) -> list[str]:
    sql: list[str] = []
    for call in pool.fetchval.call_args_list:
        if call[0]:
            sql.append(call[0][0])
    for call in pool.fetch.call_args_list:
        if call[0]:
            sql.append(call[0][0])
    return sql


async def _get(app, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# Populated-row serialization (one mapping guard per read entity).
#
# The empty-row GET tests above only exercise the predicate/WHERE shape; they
# never feed a non-empty fact row through the row->model serializer.  These
# guards pin the field mapping (e.g. dose taken_at = row created_at, symptom
# occurred_at = valid_at, medication active default) which would otherwise be
# tested by ZERO tests.
# ---------------------------------------------------------------------------


def _med_fact_row(*, name="Vitamin D", active=True) -> _Row:
    return _row(
        {
            "id": uuid.uuid4(),
            "content": f"{name} 1000IU daily",
            "created_at": _NOW,
            "metadata": {
                "name": name,
                "dosage": "1000IU",
                "frequency": "daily",
                "schedule": ["08:00"],
                "active": active,
                "notes": "with breakfast",
            },
        }
    )


def _dose_fact_row(*, medication_id: str, skipped=False) -> _Row:
    return _row(
        {
            "id": uuid.uuid4(),
            "valid_at": _NOW,
            "created_at": _NOW,
            "metadata": {
                "medication_id": medication_id,
                "skipped": skipped,
                "notes": "took it",
            },
        }
    )


def _condition_fact_row(*, name="Hypertension", status="managed") -> _Row:
    return _row(
        {
            "id": uuid.uuid4(),
            "content": f"{name}: {status}",
            "created_at": _NOW,
            "metadata": {
                "name": name,
                "status": status,
                "diagnosed_at": "2024-01-01T00:00:00+00:00",
                "notes": "monitor BP",
            },
        }
    )


def _symptom_fact_row(*, name="Headache", severity=5, condition_id=None) -> _Row:
    meta: dict[str, Any] = {"severity": severity, "notes": "dull ache"}
    if condition_id is not None:
        meta["condition_id"] = condition_id
    return _row(
        {
            "id": uuid.uuid4(),
            "content": name,
            "valid_at": _NOW,
            "created_at": _NOW,
            "metadata": meta,
        }
    )


def _research_fact_row(*, title="Magnesium and sleep", tags=None) -> _Row:
    return _row(
        {
            "id": uuid.uuid4(),
            "content": "Studies suggest magnesium improves sleep latency.",
            "created_at": _NOW,
            "metadata": {
                "title": title,
                "tags": tags if tags is not None else ["sleep", "supplements"],
                "source_url": "https://example.com/study",
                "condition_id": None,
            },
        }
    )


async def test_medications_returns_fact_based_entry():
    row = _med_fact_row(name="Vitamin D")
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    resp = await _get(app, "/api/health/medications")
    assert resp.status_code == 200
    m = resp.json()["data"][0]
    assert m["id"] == str(row["id"])
    assert m["name"] == "Vitamin D"
    assert m["dosage"] == "1000IU"
    assert m["frequency"] == "daily"
    assert m["schedule"] == ["08:00"]
    assert m["active"] is True
    assert m["notes"] == "with breakfast"


async def test_doses_returns_fact_based_entry():
    med_id = str(uuid.uuid4())
    row = _dose_fact_row(medication_id=med_id, skipped=False)
    app, _ = _make_app(fetch_rows=[row])
    resp = await _get(app, f"/api/health/medications/{med_id}/doses")
    assert resp.status_code == 200
    d = resp.json()[0]
    assert d["id"] == str(row["id"])
    assert d["medication_id"] == med_id
    assert d["skipped"] is False
    assert d["notes"] == "took it"
    # taken_at maps off the fact's created_at, not a relational column.
    assert d["taken_at"] == _NOW.isoformat()


async def test_conditions_returns_fact_based_entry():
    row = _condition_fact_row(name="Hypertension", status="managed")
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    resp = await _get(app, "/api/health/conditions")
    assert resp.status_code == 200
    c = resp.json()["data"][0]
    assert c["id"] == str(row["id"])
    assert c["name"] == "Hypertension"
    assert c["status"] == "managed"
    assert c["diagnosed_at"] == "2024-01-01T00:00:00+00:00"
    assert c["notes"] == "monitor BP"


async def test_symptoms_returns_fact_based_entry():
    cond_id = str(uuid.uuid4())
    row = _symptom_fact_row(name="Headache", severity=7, condition_id=cond_id)
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    resp = await _get(app, "/api/health/symptoms")
    assert resp.status_code == 200
    s = resp.json()["data"][0]
    assert s["id"] == str(row["id"])
    assert s["name"] == "Headache"
    assert s["severity"] == 7
    assert s["condition_id"] == cond_id
    assert s["notes"] == "dull ache"
    # occurred_at maps off the fact's valid_at.
    assert s["occurred_at"] == _NOW.isoformat()


async def test_research_returns_fact_based_entry():
    row = _research_fact_row(title="Magnesium and sleep")
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    resp = await _get(app, "/api/health/research")
    assert resp.status_code == 200
    r = resp.json()["data"][0]
    assert r["id"] == str(row["id"])
    assert r["title"] == "Magnesium and sleep"
    assert r["content"].startswith("Studies suggest")
    assert r["tags"] == ["sleep", "supplements"]
    assert r["source_url"] == "https://example.com/study"
    assert r["condition_id"] is None


# ---------------------------------------------------------------------------
# GET /medications
# ---------------------------------------------------------------------------


async def test_medications_empty():
    app, _ = _make_app(fetch_rows=[], fetchval_result=0)
    resp = await _get(app, "/api/health/medications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


async def test_medications_predicate_query():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/medications")
    sql = _all_sql(pool)
    assert any("FROM facts" in s and "predicate = 'medication'" in s for s in sql)


async def test_medications_active_filter():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/medications?active=true")
    # The active filter must hit metadata->>'active', not a relational `active` column.
    assert any("metadata->>'active'" in s for s in _all_sql(pool))
    # And the bound arg should be the active boolean.
    assert any(True in call[0][1:] for call in pool.fetchval.call_args_list if len(call[0]) > 1)


async def test_medications_no_orphan_table():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/medications")
    for s in _all_sql(pool):
        assert "FROM medications" not in s, f"must not touch orphaned table:\n{s}"


# ---------------------------------------------------------------------------
# GET /medications/{id}/doses
# ---------------------------------------------------------------------------


async def test_doses_predicate_and_med_filter():
    med_id = str(uuid.uuid4())
    app, pool = _make_app(fetch_rows=[])
    await _get(app, f"/api/health/medications/{med_id}/doses")
    sql = _all_sql(pool)
    assert any("predicate = 'took_dose'" in s for s in sql)
    assert any("metadata->>'medication_id'" in s for s in sql)
    # the medication_id is bound as the first positional arg
    assert any(med_id in call[0][1:] for call in pool.fetch.call_args_list if len(call[0]) > 1)


async def test_doses_no_orphan_table():
    med_id = str(uuid.uuid4())
    app, pool = _make_app(fetch_rows=[])
    await _get(app, f"/api/health/medications/{med_id}/doses")
    for s in _all_sql(pool):
        assert "FROM medication_doses" not in s, f"must not touch orphaned table:\n{s}"


# ---------------------------------------------------------------------------
# POST / PUT / DELETE /medications — direct dashboard CRUD (bu-aisjm)
#
# These mutations delegate to the Health butler's own fact-store tools
# (medication_add / medication_update / medication_delete) so dashboard writes
# and butler writes share a single predicate ('medication') and code path.
# We patch the tool functions to assert the endpoints wire to them correctly
# (status codes, request mapping, error translation) without a live DB.
# ---------------------------------------------------------------------------

_HEALTH_TOOLS = "butlers.tools.health"


async def test_create_medication_delegates_to_medication_add():
    app, _ = _make_app()
    new_id = uuid.uuid4()
    fake_add = AsyncMock(
        return_value={
            "id": new_id,
            "name": "Vitamin D",
            "dosage": "1000IU",
            "frequency": "daily",
            "schedule": ["08:00"],
            "active": True,
            "notes": "with breakfast",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.medication_add", fake_add):
        resp = await _request(
            app,
            "POST",
            "/api/health/medications",
            json={
                "name": "Vitamin D",
                "dosage": "1000IU",
                "frequency": "daily",
                "schedule": ["08:00"],
                "notes": "with breakfast",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(new_id)
    assert body["name"] == "Vitamin D"
    assert body["active"] is True
    # The endpoint forwarded the validated request fields to the butler tool.
    fake_add.assert_awaited_once()
    assert fake_add.await_args.kwargs["name"] == "Vitamin D"


async def test_create_medication_validates_required_fields():
    app, _ = _make_app()
    # Missing required `dosage` / `frequency` — pydantic rejects before any tool call.
    resp = await _request(app, "POST", "/api/health/medications", json={"name": "Vitamin D"})
    assert resp.status_code == 422


async def test_update_medication_delegates_to_medication_update():
    app, _ = _make_app()
    med_id = uuid.uuid4()
    fake_update = AsyncMock(
        return_value={
            "id": med_id,
            "name": "Vitamin D",
            "dosage": "2000IU",
            "frequency": "daily",
            "schedule": [],
            "active": True,
            "notes": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.medication_update", fake_update):
        resp = await _request(
            app, "PUT", f"/api/health/medications/{med_id}", json={"dosage": "2000IU"}
        )
    assert resp.status_code == 200
    assert resp.json()["dosage"] == "2000IU"
    fake_update.assert_awaited_once()
    # Only the supplied field is forwarded (exclude_none).
    assert fake_update.await_args.kwargs == {"dosage": "2000IU"}


async def test_update_medication_empty_body_is_422():
    app, _ = _make_app()
    med_id = uuid.uuid4()
    with patch(f"{_HEALTH_TOOLS}.medication_update", AsyncMock()) as fake_update:
        resp = await _request(app, "PUT", f"/api/health/medications/{med_id}", json={})
    assert resp.status_code == 422
    fake_update.assert_not_awaited()


async def test_update_medication_missing_is_404():
    app, _ = _make_app()
    med_id = uuid.uuid4()
    fake_update = AsyncMock(side_effect=ValueError(f"Medication {med_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.medication_update", fake_update):
        resp = await _request(
            app, "PUT", f"/api/health/medications/{med_id}", json={"dosage": "5mg"}
        )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


async def test_delete_medication_delegates_to_medication_delete():
    app, _ = _make_app()
    med_id = uuid.uuid4()
    fake_delete = AsyncMock(return_value=True)
    with patch(f"{_HEALTH_TOOLS}.medication_delete", fake_delete):
        resp = await _request(app, "DELETE", f"/api/health/medications/{med_id}")
    assert resp.status_code == 204
    fake_delete.assert_awaited_once()
    assert str(med_id) in fake_delete.await_args.args


# ---------------------------------------------------------------------------
# GET /conditions
# ---------------------------------------------------------------------------


async def test_conditions_predicate_query():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/conditions")
    sql = _all_sql(pool)
    assert any("FROM facts" in s and "predicate = 'condition'" in s for s in sql)


async def test_conditions_no_orphan_table():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/conditions")
    for s in _all_sql(pool):
        assert "FROM conditions" not in s, f"must not touch orphaned table:\n{s}"


# ---------------------------------------------------------------------------
# POST/PUT/DELETE /conditions — direct dashboard CRUD (mirrors medications)
# ---------------------------------------------------------------------------


async def test_create_condition_delegates_to_condition_add():
    app, _ = _make_app()
    new_id = uuid.uuid4()
    fake_add = AsyncMock(
        return_value={
            "id": new_id,
            "name": "Hypertension",
            "status": "managed",
            "diagnosed_at": "2024-01-01T00:00:00+00:00",
            "notes": "monitor BP",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.condition_add", fake_add):
        resp = await _request(
            app,
            "POST",
            "/api/health/conditions",
            json={
                "name": "Hypertension",
                "status": "managed",
                "diagnosed_at": "2024-01-01T00:00:00+00:00",
                "notes": "monitor BP",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(new_id)
    assert body["name"] == "Hypertension"
    assert body["status"] == "managed"
    # The endpoint forwarded the validated request fields to the butler tool,
    # coercing the ISO date string to a datetime.
    fake_add.assert_awaited_once()
    kwargs = fake_add.await_args.kwargs
    assert kwargs["name"] == "Hypertension"
    assert kwargs["diagnosed_at"] == datetime(2024, 1, 1, tzinfo=UTC)


async def test_create_condition_defaults_status_active():
    app, _ = _make_app()
    new_id = uuid.uuid4()
    fake_add = AsyncMock(
        return_value={
            "id": new_id,
            "name": "Asthma",
            "status": "active",
            "diagnosed_at": None,
            "notes": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.condition_add", fake_add):
        resp = await _request(app, "POST", "/api/health/conditions", json={"name": "Asthma"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "active"
    assert fake_add.await_args.kwargs["status"] == "active"


async def test_create_condition_rejects_invalid_status():
    app, _ = _make_app()
    resp = await _request(
        app,
        "POST",
        "/api/health/conditions",
        json={"name": "Asthma", "status": "chronic"},
    )
    assert resp.status_code == 422


async def test_update_condition_delegates_to_condition_update():
    app, _ = _make_app()
    cond_id = uuid.uuid4()
    fake_update = AsyncMock(
        return_value={
            "id": cond_id,
            "name": "Hypertension",
            "status": "resolved",
            "diagnosed_at": None,
            "notes": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.condition_update", fake_update):
        resp = await _request(
            app, "PUT", f"/api/health/conditions/{cond_id}", json={"status": "resolved"}
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    fake_update.assert_awaited_once()
    # Only the supplied field is forwarded (exclude_none).
    assert fake_update.await_args.kwargs == {"status": "resolved"}


async def test_update_condition_empty_body_is_422():
    app, _ = _make_app()
    cond_id = uuid.uuid4()
    with patch(f"{_HEALTH_TOOLS}.condition_update", AsyncMock()) as fake_update:
        resp = await _request(app, "PUT", f"/api/health/conditions/{cond_id}", json={})
    assert resp.status_code == 422
    fake_update.assert_not_awaited()


async def test_update_condition_invalid_status_is_422():
    app, _ = _make_app()
    cond_id = uuid.uuid4()
    with patch(f"{_HEALTH_TOOLS}.condition_update", AsyncMock()) as fake_update:
        resp = await _request(
            app, "PUT", f"/api/health/conditions/{cond_id}", json={"status": "chronic"}
        )
    assert resp.status_code == 422
    fake_update.assert_not_awaited()


async def test_update_condition_missing_is_404():
    app, _ = _make_app()
    cond_id = uuid.uuid4()
    fake_update = AsyncMock(side_effect=ValueError(f"Condition {cond_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.condition_update", fake_update):
        resp = await _request(
            app, "PUT", f"/api/health/conditions/{cond_id}", json={"status": "resolved"}
        )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


async def test_delete_condition_delegates_to_condition_delete():
    app, _ = _make_app()
    cond_id = uuid.uuid4()
    fake_delete = AsyncMock(return_value=True)
    with patch(f"{_HEALTH_TOOLS}.condition_delete", fake_delete):
        resp = await _request(app, "DELETE", f"/api/health/conditions/{cond_id}")
    assert resp.status_code == 204
    fake_delete.assert_awaited_once()
    assert str(cond_id) in fake_delete.await_args.args


# ---------------------------------------------------------------------------
# GET /symptoms
# ---------------------------------------------------------------------------


async def test_symptoms_predicate_query():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/symptoms")
    sql = _all_sql(pool)
    assert any("FROM facts" in s and "predicate = 'symptom'" in s for s in sql)


async def test_symptoms_name_filter_targets_content():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/symptoms?name=head")
    # name lives in `content`, not a relational `name` column.
    assert any("content ILIKE" in s for s in _all_sql(pool))


async def test_symptoms_no_orphan_table():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/symptoms")
    for s in _all_sql(pool):
        assert "FROM symptoms" not in s, f"must not touch orphaned table:\n{s}"


# ---------------------------------------------------------------------------
# POST / PUT / DELETE /symptoms — direct dashboard CRUD (bu-gk38e)
#
# Each mutation delegates to the Health butler's own fact-store tool
# (symptom_log / symptom_update / symptom_delete) so dashboard writes and
# butler writes share a single predicate ('symptom') and code path.  Symptoms
# are TEMPORAL facts — occurred_at -> valid_at, no supersession.
# ---------------------------------------------------------------------------


async def test_create_symptom_delegates_to_symptom_log():
    app, _ = _make_app()
    new_id = uuid.uuid4()
    fake_log = AsyncMock(
        return_value={
            "id": new_id,
            "name": "Headache",
            "severity": 7,
            "condition_id": None,
            "notes": "after screen time",
            "occurred_at": _NOW,
            "created_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.symptom_log", fake_log):
        resp = await _request(
            app,
            "POST",
            "/api/health/symptoms",
            json={
                "name": "Headache",
                "severity": 7,
                "occurred_at": "2024-01-01T00:00:00+00:00",
                "notes": "after screen time",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(new_id)
    assert body["name"] == "Headache"
    assert body["severity"] == 7
    fake_log.assert_awaited_once()
    kwargs = fake_log.await_args.kwargs
    assert kwargs["name"] == "Headache"
    # occurred_at -> valid_at: ISO string coerced to datetime.
    assert kwargs["occurred_at"] == datetime(2024, 1, 1, tzinfo=UTC)


async def test_create_symptom_rejects_blank_name():
    app, _ = _make_app()
    resp = await _request(app, "POST", "/api/health/symptoms", json={"name": "", "severity": 5})
    assert resp.status_code == 422


async def test_create_symptom_rejects_out_of_range_severity():
    app, _ = _make_app()
    resp = await _request(
        app, "POST", "/api/health/symptoms", json={"name": "Headache", "severity": 11}
    )
    assert resp.status_code == 422


async def test_create_symptom_missing_condition_is_404():
    app, _ = _make_app()
    cond_id = str(uuid.uuid4())
    fake_log = AsyncMock(side_effect=ValueError(f"Condition {cond_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.symptom_log", fake_log):
        resp = await _request(
            app,
            "POST",
            "/api/health/symptoms",
            json={"name": "Headache", "severity": 5, "condition_id": cond_id},
        )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


async def test_update_symptom_delegates_to_symptom_update():
    app, _ = _make_app()
    sym_id = uuid.uuid4()
    fake_update = AsyncMock(
        return_value={
            "id": sym_id,
            "name": "Headache",
            "severity": 9,
            "condition_id": None,
            "notes": None,
            "occurred_at": _NOW,
            "created_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.symptom_update", fake_update):
        resp = await _request(app, "PUT", f"/api/health/symptoms/{sym_id}", json={"severity": 9})
    assert resp.status_code == 200
    assert resp.json()["severity"] == 9
    fake_update.assert_awaited_once()
    # Only the supplied field is forwarded (exclude_none).
    assert fake_update.await_args.kwargs == {"severity": 9}


async def test_update_symptom_empty_body_is_422():
    app, _ = _make_app()
    sym_id = uuid.uuid4()
    with patch(f"{_HEALTH_TOOLS}.symptom_update", AsyncMock()) as fake_update:
        resp = await _request(app, "PUT", f"/api/health/symptoms/{sym_id}", json={})
    assert resp.status_code == 422
    fake_update.assert_not_awaited()


async def test_update_symptom_out_of_range_severity_is_422():
    app, _ = _make_app()
    sym_id = uuid.uuid4()
    with patch(f"{_HEALTH_TOOLS}.symptom_update", AsyncMock()) as fake_update:
        resp = await _request(app, "PUT", f"/api/health/symptoms/{sym_id}", json={"severity": 0})
    assert resp.status_code == 422
    fake_update.assert_not_awaited()


async def test_update_symptom_missing_is_404():
    app, _ = _make_app()
    sym_id = uuid.uuid4()
    fake_update = AsyncMock(side_effect=ValueError(f"Symptom {sym_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.symptom_update", fake_update):
        resp = await _request(app, "PUT", f"/api/health/symptoms/{sym_id}", json={"severity": 5})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


async def test_delete_symptom_delegates_to_symptom_delete():
    app, _ = _make_app()
    sym_id = uuid.uuid4()
    fake_delete = AsyncMock(return_value=True)
    with patch(f"{_HEALTH_TOOLS}.symptom_delete", fake_delete):
        resp = await _request(app, "DELETE", f"/api/health/symptoms/{sym_id}")
    assert resp.status_code == 204
    fake_delete.assert_awaited_once()
    assert str(sym_id) in fake_delete.await_args.args


# ---------------------------------------------------------------------------
# POST / PUT / DELETE /meals — direct dashboard CRUD (bu-5oeoq)
#
# Each mutation delegates to the Health butler's own fact-store tool
# (meal_log / meal_update / meal_delete) so dashboard writes and butler writes
# share the same predicate set ('meal_{type}') and code path.  Meals are
# TEMPORAL facts — eaten_at -> valid_at, no supersession; the update path edits
# the existing fact row in place.  The facts table has no updated_at column.
# ---------------------------------------------------------------------------


def _meal_tool_result(
    *,
    meal_id,
    type="lunch",
    description="Grilled chicken salad",
    estimated_calories=420,
    macros=None,
    notes=None,
) -> dict:
    """A meal_log/meal_update tool result dict (estimated_calories + macros)."""
    return {
        "id": meal_id,
        "type": type,
        "description": description,
        "estimated_calories": estimated_calories,
        "macros": macros or {"protein_g": 35, "carbs_g": 12, "fat_g": 18},
        "eaten_at": _NOW,
        "notes": notes,
        "created_at": _NOW,
    }


async def test_create_meal_delegates_to_meal_log():
    app, _ = _make_app()
    new_id = uuid.uuid4()
    fake_log = AsyncMock(return_value=_meal_tool_result(meal_id=new_id, notes="post-workout"))
    with patch(f"{_HEALTH_TOOLS}.meal_log", fake_log):
        resp = await _request(
            app,
            "POST",
            "/api/health/meals",
            json={
                "type": "lunch",
                "description": "Grilled chicken salad",
                "eaten_at": "2024-01-01T12:00:00+00:00",
                "nutrition": {"calories": 420, "protein_g": 35, "carbs_g": 12, "fat_g": 18},
                "notes": "post-workout",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(new_id)
    assert body["type"] == "lunch"
    assert body["description"] == "Grilled chicken salad"
    # Tool result's estimated_calories/macros reshape into the nutrition envelope.
    assert body["nutrition"]["calories"] == 420
    assert body["nutrition"]["protein_g"] == 35
    fake_log.assert_awaited_once()
    kwargs = fake_log.await_args.kwargs
    assert kwargs["eaten_at"] == datetime(2024, 1, 1, 12, tzinfo=UTC)
    assert kwargs["nutrition"] == {
        "calories": 420,
        "protein_g": 35,
        "carbs_g": 12,
        "fat_g": 18,
    }


async def test_create_meal_rejects_blank_description():
    app, _ = _make_app()
    resp = await _request(
        app,
        "POST",
        "/api/health/meals",
        json={"type": "lunch", "description": "", "eaten_at": "2024-01-01T12:00:00+00:00"},
    )
    assert resp.status_code == 422


async def test_create_meal_rejects_invalid_type():
    app, _ = _make_app()
    resp = await _request(
        app,
        "POST",
        "/api/health/meals",
        json={"type": "brunch", "description": "Eggs", "eaten_at": "2024-01-01T12:00:00+00:00"},
    )
    assert resp.status_code == 422


async def test_create_meal_requires_eaten_at():
    app, _ = _make_app()
    resp = await _request(
        app,
        "POST",
        "/api/health/meals",
        json={"type": "lunch", "description": "Salad"},
    )
    assert resp.status_code == 422


async def test_update_meal_delegates_to_meal_update():
    app, _ = _make_app()
    meal_id = uuid.uuid4()
    fake_update = AsyncMock(return_value=_meal_tool_result(meal_id=meal_id, type="dinner"))
    with patch(f"{_HEALTH_TOOLS}.meal_update", fake_update):
        resp = await _request(app, "PUT", f"/api/health/meals/{meal_id}", json={"type": "dinner"})
    assert resp.status_code == 200
    assert resp.json()["type"] == "dinner"
    fake_update.assert_awaited_once()
    # Only the supplied field is forwarded (exclude_none).
    assert fake_update.await_args.kwargs == {"type": "dinner"}


async def test_update_meal_invalid_type_is_422():
    app, _ = _make_app()
    meal_id = uuid.uuid4()
    with patch(f"{_HEALTH_TOOLS}.meal_update", AsyncMock()) as fake_update:
        resp = await _request(app, "PUT", f"/api/health/meals/{meal_id}", json={"type": "brunch"})
    assert resp.status_code == 422
    fake_update.assert_not_awaited()


async def test_update_meal_missing_is_404():
    app, _ = _make_app()
    meal_id = uuid.uuid4()
    fake_update = AsyncMock(side_effect=ValueError(f"Meal {meal_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.meal_update", fake_update):
        resp = await _request(app, "PUT", f"/api/health/meals/{meal_id}", json={"description": "x"})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


async def test_delete_meal_delegates_to_meal_delete():
    app, _ = _make_app()
    meal_id = uuid.uuid4()
    fake_delete = AsyncMock(return_value=True)
    with patch(f"{_HEALTH_TOOLS}.meal_delete", fake_delete):
        resp = await _request(app, "DELETE", f"/api/health/meals/{meal_id}")
    assert resp.status_code == 204
    fake_delete.assert_awaited_once()
    assert str(meal_id) in fake_delete.await_args.args


# ---------------------------------------------------------------------------
# GET /research
# ---------------------------------------------------------------------------


async def test_research_predicate_query():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/research")
    sql = _all_sql(pool)
    assert any("FROM facts" in s and "predicate = 'research'" in s for s in sql)


async def test_research_q_filter_targets_title_and_content():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/research?q=magnesium")
    sql = _all_sql(pool)
    assert any("metadata->>'title' ILIKE" in s and "content ILIKE" in s for s in sql)


async def test_research_tag_filter_targets_metadata_tags():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/research?tag=sleep")
    assert any("metadata->'tags'" in s for s in _all_sql(pool))


async def test_research_no_orphan_table():
    app, pool = _make_app(fetch_rows=[], fetchval_result=0)
    await _get(app, "/api/health/research")
    for s in _all_sql(pool):
        assert "FROM research" not in s, f"must not touch orphaned table:\n{s}"


# ---------------------------------------------------------------------------
# POST/PUT/DELETE /research — direct dashboard CRUD (property-fact, mirrors
# conditions: research_save/research_update key supersession on the
# ``research:{title}`` subject, NOT in-place like symptoms/meals)
# ---------------------------------------------------------------------------


async def test_create_research_delegates_to_research_save():
    app, _ = _make_app()
    new_id = uuid.uuid4()
    fake_save = AsyncMock(
        return_value={
            "id": new_id,
            "title": "Magnesium and sleep",
            "content": "Studies suggest magnesium improves sleep latency.",
            "tags": ["sleep", "supplements"],
            "source_url": "https://example.com/study",
            "condition_id": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.research_save", fake_save):
        resp = await _request(
            app,
            "POST",
            "/api/health/research",
            json={
                "title": "Magnesium and sleep",
                "content": "Studies suggest magnesium improves sleep latency.",
                "tags": ["sleep", "supplements"],
                "source_url": "https://example.com/study",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(new_id)
    assert body["title"] == "Magnesium and sleep"
    assert body["tags"] == ["sleep", "supplements"]
    assert body["source_url"] == "https://example.com/study"
    fake_save.assert_awaited_once()
    assert fake_save.await_args.kwargs["title"] == "Magnesium and sleep"


async def test_create_research_rejects_blank_title():
    app, _ = _make_app()
    resp = await _request(app, "POST", "/api/health/research", json={"title": "", "content": "x"})
    assert resp.status_code == 422


async def test_create_research_rejects_blank_content():
    app, _ = _make_app()
    resp = await _request(app, "POST", "/api/health/research", json={"title": "x", "content": ""})
    assert resp.status_code == 422


async def test_create_research_missing_condition_is_404():
    app, _ = _make_app()
    cond_id = str(uuid.uuid4())
    fake_save = AsyncMock(side_effect=ValueError(f"Condition {cond_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.research_save", fake_save):
        resp = await _request(
            app,
            "POST",
            "/api/health/research",
            json={"title": "T", "content": "C", "condition_id": cond_id},
        )
    assert resp.status_code == 404


async def test_update_research_delegates_to_research_update():
    app, _ = _make_app()
    res_id = uuid.uuid4()
    fake_update = AsyncMock(
        return_value={
            "id": res_id,
            "title": "Magnesium and sleep",
            "content": "Updated body.",
            "tags": ["sleep"],
            "source_url": None,
            "condition_id": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.research_update", fake_update):
        resp = await _request(
            app, "PUT", f"/api/health/research/{res_id}", json={"content": "Updated body."}
        )
    assert resp.status_code == 200
    assert resp.json()["content"] == "Updated body."
    fake_update.assert_awaited_once()
    assert fake_update.await_args.kwargs == {"content": "Updated body."}


async def test_update_research_missing_is_404():
    app, _ = _make_app()
    res_id = uuid.uuid4()
    fake_update = AsyncMock(side_effect=ValueError(f"Research {res_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.research_update", fake_update):
        resp = await _request(app, "PUT", f"/api/health/research/{res_id}", json={"title": "New"})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


async def test_delete_research_delegates_to_research_delete():
    app, _ = _make_app()
    res_id = uuid.uuid4()
    fake_delete = AsyncMock(return_value=True)
    with patch(f"{_HEALTH_TOOLS}.research_delete", fake_delete):
        resp = await _request(app, "DELETE", f"/api/health/research/{res_id}")
    assert resp.status_code == 204
    fake_delete.assert_awaited_once()
    assert str(res_id) in fake_delete.await_args.args


# ---------------------------------------------------------------------------
# POST / PUT / DELETE /measurements — direct dashboard CRUD (bu-mqhas)
#
# Each mutation delegates to the Health butler's own fact-store tool
# (measurement_log / measurement_update / measurement_delete) so dashboard
# writes and butler writes share the same predicate family
# ('measurement_{type}') and code path.  Measurements are TEMPORAL facts —
# measured_at -> valid_at, no supersession; the type is encoded in the predicate.
# ---------------------------------------------------------------------------


async def test_create_measurement_delegates_to_measurement_log():
    app, _ = _make_app()
    new_id = uuid.uuid4()
    fake_log = AsyncMock(
        return_value={
            "id": new_id,
            "type": "weight",
            "value": 70,
            "notes": "morning",
            "measured_at": _NOW,
            "created_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.measurement_log", fake_log):
        resp = await _request(
            app,
            "POST",
            "/api/health/measurements",
            json={
                "type": "weight",
                "value": {"value": 70},
                "measured_at": "2024-01-01T00:00:00+00:00",
                "notes": "morning",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(new_id)
    assert body["type"] == "weight"
    assert body["value"] == {"value": 70}
    fake_log.assert_awaited_once()
    kwargs = fake_log.await_args.kwargs
    assert kwargs["type"] == "weight"
    # Single-key {"value": x} payloads are unwrapped to a scalar for the tool.
    assert kwargs["value"] == 70
    assert kwargs["measured_at"] == datetime(2024, 1, 1, tzinfo=UTC)
    assert kwargs["notes"] == "morning"


async def test_create_measurement_preserves_compound_value():
    app, _ = _make_app()
    new_id = uuid.uuid4()
    bp = {"systolic": 120, "diastolic": 80}
    fake_log = AsyncMock(
        return_value={
            "id": new_id,
            "type": "blood_pressure",
            "value": bp,
            "notes": None,
            "measured_at": _NOW,
            "created_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.measurement_log", fake_log):
        resp = await _request(
            app,
            "POST",
            "/api/health/measurements",
            json={"type": "blood_pressure", "value": bp},
        )
    assert resp.status_code == 201
    assert resp.json()["value"] == bp
    # Compound dicts pass through to the tool untouched.
    assert fake_log.await_args.kwargs["value"] == bp


async def test_create_measurement_rejects_invalid_type():
    app, _ = _make_app()
    resp = await _request(
        app, "POST", "/api/health/measurements", json={"type": "cholesterol", "value": {"value": 1}}
    )
    assert resp.status_code == 422


async def test_create_measurement_invalid_type_from_tool_is_404():
    app, _ = _make_app()
    fake_log = AsyncMock(side_effect=ValueError("Unrecognized measurement type: 'weight'"))
    with patch(f"{_HEALTH_TOOLS}.measurement_log", fake_log):
        resp = await _request(
            app, "POST", "/api/health/measurements", json={"type": "weight", "value": {"value": 1}}
        )
    assert resp.status_code == 404


async def test_update_measurement_delegates_to_measurement_update():
    app, _ = _make_app()
    meas_id = uuid.uuid4()
    fake_update = AsyncMock(
        return_value={
            "id": meas_id,
            "type": "weight",
            "value": 72,
            "notes": None,
            "measured_at": _NOW,
            "created_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.measurement_update", fake_update):
        resp = await _request(
            app, "PUT", f"/api/health/measurements/{meas_id}", json={"value": {"value": 72}}
        )
    assert resp.status_code == 200
    assert resp.json()["value"] == {"value": 72}
    fake_update.assert_awaited_once()
    # Only the supplied field is forwarded (exclude_none), unwrapped to scalar.
    assert fake_update.await_args.kwargs == {"value": 72}


async def test_update_measurement_rewrites_type():
    app, _ = _make_app()
    meas_id = uuid.uuid4()
    fake_update = AsyncMock(
        return_value={
            "id": meas_id,
            "type": "blood_sugar",
            "value": 95,
            "notes": None,
            "measured_at": _NOW,
            "created_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.measurement_update", fake_update):
        resp = await _request(
            app,
            "PUT",
            f"/api/health/measurements/{meas_id}",
            json={"type": "blood_sugar", "value": {"value": 95}},
        )
    assert resp.status_code == 200
    assert resp.json()["type"] == "blood_sugar"
    assert fake_update.await_args.kwargs == {"type": "blood_sugar", "value": 95}


async def test_update_measurement_invalid_type_is_422():
    app, _ = _make_app()
    meas_id = uuid.uuid4()
    with patch(f"{_HEALTH_TOOLS}.measurement_update", AsyncMock()) as fake_update:
        resp = await _request(
            app, "PUT", f"/api/health/measurements/{meas_id}", json={"type": "cholesterol"}
        )
    assert resp.status_code == 422
    fake_update.assert_not_awaited()


async def test_update_measurement_missing_is_404():
    app, _ = _make_app()
    meas_id = uuid.uuid4()
    fake_update = AsyncMock(side_effect=ValueError(f"Measurement {meas_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.measurement_update", fake_update):
        resp = await _request(
            app, "PUT", f"/api/health/measurements/{meas_id}", json={"value": {"value": 5}}
        )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


async def test_delete_measurement_delegates_to_measurement_delete():
    app, _ = _make_app()
    meas_id = uuid.uuid4()
    fake_delete = AsyncMock(return_value=True)
    with patch(f"{_HEALTH_TOOLS}.measurement_delete", fake_delete):
        resp = await _request(app, "DELETE", f"/api/health/measurements/{meas_id}")
    assert resp.status_code == 204
    fake_delete.assert_awaited_once()
    assert str(meas_id) in fake_delete.await_args.args

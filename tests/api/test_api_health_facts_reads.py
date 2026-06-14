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
# GET /medications
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


async def test_medications_empty():
    app, _ = _make_app(fetch_rows=[], fetchval_result=0)
    resp = await _get(app, "/api/health/medications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


async def test_medications_returns_fact_based_entry():
    row = _med_fact_row(name="Vitamin D")
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    resp = await _get(app, "/api/health/medications")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    m = data[0]
    assert m["id"] == str(row["id"])
    assert m["name"] == "Vitamin D"
    assert m["dosage"] == "1000IU"
    assert m["frequency"] == "daily"
    assert m["schedule"] == ["08:00"]
    assert m["active"] is True
    assert m["notes"] == "with breakfast"


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


async def test_doses_returns_fact_based_entry():
    med_id = str(uuid.uuid4())
    row = _dose_fact_row(medication_id=med_id, skipped=False)
    app, _ = _make_app(fetch_rows=[row])
    resp = await _get(app, f"/api/health/medications/{med_id}/doses")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    d = data[0]
    assert d["id"] == str(row["id"])
    assert d["medication_id"] == med_id
    assert d["skipped"] is False
    assert d["notes"] == "took it"
    assert d["taken_at"] == _NOW.isoformat()


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
    kwargs = fake_add.await_args.kwargs
    assert kwargs["name"] == "Vitamin D"
    assert kwargs["dosage"] == "1000IU"
    assert kwargs["frequency"] == "daily"
    assert kwargs["schedule"] == ["08:00"]
    assert kwargs["notes"] == "with breakfast"


async def test_create_medication_validates_required_fields():
    app, _ = _make_app()
    # Missing required `dosage` / `frequency` — pydantic rejects before any tool call.
    resp = await _request(app, "POST", "/api/health/medications", json={"name": "Vitamin D"})
    assert resp.status_code == 422


async def test_create_medication_rejects_blank_name():
    app, _ = _make_app()
    resp = await _request(
        app,
        "POST",
        "/api/health/medications",
        json={"name": "", "dosage": "1000IU", "frequency": "daily"},
    )
    assert resp.status_code == 422


async def test_created_medication_is_read_back_by_get():
    """A dashboard-created medication is read back by the existing GET (same fact path)."""
    app, _ = _make_app()
    new_id = uuid.uuid4()
    fake_add = AsyncMock(
        return_value={
            "id": new_id,
            "name": "Magnesium",
            "dosage": "200mg",
            "frequency": "nightly",
            "schedule": [],
            "active": True,
            "notes": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with patch(f"{_HEALTH_TOOLS}.medication_add", fake_add):
        create_resp = await _request(
            app,
            "POST",
            "/api/health/medications",
            json={"name": "Magnesium", "dosage": "200mg", "frequency": "nightly"},
        )
    assert create_resp.status_code == 201

    # Now simulate the GET surface returning the same fact (predicate 'medication').
    read_row = _row(
        {
            "id": new_id,
            "content": "Magnesium 200mg nightly",
            "created_at": _NOW,
            "metadata": {
                "name": "Magnesium",
                "dosage": "200mg",
                "frequency": "nightly",
                "schedule": [],
                "active": True,
            },
        }
    )
    app2, _ = _make_app(fetch_rows=[read_row], fetchval_result=1)
    get_resp = await _get(app2, "/api/health/medications")
    assert get_resp.status_code == 200
    data = get_resp.json()["data"]
    assert any(m["id"] == str(new_id) and m["name"] == "Magnesium" for m in data)


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


async def test_delete_medication_missing_is_404():
    app, _ = _make_app()
    med_id = uuid.uuid4()
    fake_delete = AsyncMock(side_effect=ValueError(f"Medication {med_id} not found"))
    with patch(f"{_HEALTH_TOOLS}.medication_delete", fake_delete):
        resp = await _request(app, "DELETE", f"/api/health/medications/{med_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /conditions
# ---------------------------------------------------------------------------


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


async def test_conditions_returns_fact_based_entry():
    row = _condition_fact_row(name="Hypertension", status="managed")
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    resp = await _get(app, "/api/health/conditions")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    c = data[0]
    assert c["id"] == str(row["id"])
    assert c["name"] == "Hypertension"
    assert c["status"] == "managed"
    assert c["diagnosed_at"] == "2024-01-01T00:00:00+00:00"
    assert c["notes"] == "monitor BP"


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
# GET /symptoms
# ---------------------------------------------------------------------------


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


async def test_symptoms_returns_fact_based_entry():
    cond_id = str(uuid.uuid4())
    row = _symptom_fact_row(name="Headache", severity=7, condition_id=cond_id)
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    resp = await _get(app, "/api/health/symptoms")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    s = data[0]
    assert s["id"] == str(row["id"])
    assert s["name"] == "Headache"
    assert s["severity"] == 7
    assert s["condition_id"] == cond_id
    assert s["notes"] == "dull ache"
    assert s["occurred_at"] == _NOW.isoformat()


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
# GET /research
# ---------------------------------------------------------------------------


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


async def test_research_returns_fact_based_entry():
    row = _research_fact_row(title="Magnesium and sleep")
    app, _ = _make_app(fetch_rows=[row], fetchval_result=1)
    resp = await _get(app, "/api/health/research")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    r = data[0]
    assert r["id"] == str(row["id"])
    assert r["title"] == "Magnesium and sleep"
    assert r["content"].startswith("Studies suggest")
    assert r["tags"] == ["sleep", "supplements"]
    assert r["source_url"] == "https://example.com/study"
    assert r["condition_id"] is None


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

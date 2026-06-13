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
from unittest.mock import AsyncMock, MagicMock

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

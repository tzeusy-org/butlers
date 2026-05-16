"""Tests for the public.audit_log primitive API.

Covers:
- Static-check: no destructive statements targeting audit_log exist in repo code.
- audit.append() helper: inserts row, increments Prometheus counter, returns id.
- GET /api/audit-log: returns PaginatedResponse[AuditLogEntry], filters, ts DESC.
- GET /api/audit-log/{id}: returns ApiResponse[AuditLogEntry] or 404.
- AuditLogEntry.from_record: IP coercion, None fields.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.models.audit import AuditLogEntry
from butlers.api.routers.audit import _get_db_manager, append, audit_log_appended_total

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Static-check: append-only invariant
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_DIRS = [
    _REPO_ROOT / "src",
    _REPO_ROOT / "tests",
    _REPO_ROOT / "roster",
]

# Pattern that would violate the append-only contract.
# Detects SQL DELETE targeting audit_log on non-comment lines.
# Requires the keyword to appear at the start of meaningful SQL (not inside
# a Python comment starting with '#').
_DELETE_PATTERN = re.compile(
    r"DELETE\s+FROM\s+(?:public\.)?audit_log\b",
    re.IGNORECASE,
)

# This file itself describes the pattern in comments — skip it.
_THIS_FILE = Path(__file__).resolve()


def _iter_python_files():
    for src_dir in _SOURCE_DIRS:
        if src_dir.exists():
            for path in src_dir.rglob("*.py"):
                if path.resolve() != _THIS_FILE:
                    yield path


def _is_comment_or_docstring_line(line: str) -> bool:
    """Heuristic: skip lines that are pure Python comments."""
    stripped = line.strip()
    return stripped.startswith("#")


def test_no_delete_from_audit_log_in_repo():
    """Fail CI if any .py file in src/, tests/, or roster/ contains a
    destructive SQL statement targeting audit_log.  The table is append-only
    by design (spec §2.4, core_092).
    """
    violations: list[str] = []
    for path in _iter_python_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _is_comment_or_docstring_line(line):
                continue
            if _DELETE_PATTERN.search(line):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, (
        "Found destructive statement(s) targeting public.audit_log — "
        "the table is append-only:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# AuditLogEntry.from_record
# ---------------------------------------------------------------------------


def _make_row(**kwargs) -> MagicMock:
    defaults = {
        "id": 1,
        "ts": datetime.now(tz=UTC),
        "actor": "owner",
        "action": "test_action",
        "target": None,
        "note": None,
        "ip": None,
        "request_id": None,
    }
    data = {**defaults, **kwargs}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def test_audit_log_entry_from_record_minimal():
    row = _make_row()
    entry = AuditLogEntry.from_record(row)
    assert entry.id == 1
    assert entry.actor == "owner"
    assert entry.action == "test_action"
    assert entry.target is None
    assert entry.ip is None
    assert entry.request_id is None


def test_audit_log_entry_from_record_with_ip_address_object():
    """ip can arrive as an ipaddress.IPv4Address from asyncpg."""
    row = _make_row(ip=ipaddress.IPv4Address("10.0.0.1"))
    entry = AuditLogEntry.from_record(row)
    assert entry.ip == "10.0.0.1"


def test_audit_log_entry_from_record_with_ip_string():
    row = _make_row(ip="192.168.0.1")
    entry = AuditLogEntry.from_record(row)
    assert entry.ip == "192.168.0.1"


def test_audit_log_entry_from_record_full():
    rid = uuid.uuid4()
    ts = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    row = _make_row(
        id=42,
        ts=ts,
        actor="butler:qa",
        action="setting_change",
        target="rule:7",
        note="Changed threshold",
        ip="10.10.10.10",
        request_id=rid,
    )
    entry = AuditLogEntry.from_record(row)
    assert entry.id == 42
    assert entry.ts == ts
    assert entry.actor == "butler:qa"
    assert entry.action == "setting_change"
    assert entry.target == "rule:7"
    assert entry.note == "Changed threshold"
    assert entry.ip == "10.10.10.10"
    assert entry.request_id == rid


# ---------------------------------------------------------------------------
# audit.append() helper
# ---------------------------------------------------------------------------


@pytest.fixture
def prometheus_clean(monkeypatch):
    """Reset the audit_log_appended_total counter between tests."""
    # Access the underlying _metrics dict to reset sample values; in unit tests
    # we just read the current count before and after to verify increment.
    yield


async def test_append_inserts_row_and_returns_id():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=7)

    row_id = await append(pool, "owner", "model_priority_change")
    assert row_id == 7

    pool.fetchval.assert_awaited_once()
    call_args = pool.fetchval.call_args[0]
    assert "INSERT INTO public.audit_log" in call_args[0]
    assert "RETURNING id" in call_args[0]
    assert call_args[1] == "owner"
    assert call_args[2] == "model_priority_change"


async def test_append_with_all_optional_fields():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=99)

    rid = uuid.uuid4()
    row_id = await append(
        pool,
        "owner",
        "rule_delete",
        target="rule:42",
        note="Deleted stale rule",
        ip="1.2.3.4",
        request_id=rid,
    )
    assert row_id == 99
    call_args = pool.fetchval.call_args[0]
    # target, note, ip, request_id passed as positional args
    assert call_args[3] == "rule:42"
    assert call_args[4] == "Deleted stale rule"
    assert call_args[5] == "1.2.3.4"
    assert call_args[6] == rid


async def test_append_increments_prometheus_counter():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)

    before = audit_log_appended_total.labels(action="test_counter_increment")._value.get()
    await append(pool, "owner", "test_counter_increment")
    after = audit_log_appended_total.labels(action="test_counter_increment")._value.get()
    assert after == before + 1


# ---------------------------------------------------------------------------
# GET /api/audit-log
# ---------------------------------------------------------------------------


def _make_audit_app(rows: list[dict], total: int | None = None) -> tuple:
    """Wire a FastAPI app with mocked DatabaseManager for audit log reads."""
    if total is None:
        total = len(rows)

    def _make_record(row: dict) -> MagicMock:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
        return m

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(return_value=[_make_record(r) for r in rows])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool, mock_db


def _sample_row(
    *,
    row_id: int = 1,
    actor: str = "owner",
    action: str = "setting_change",
    target: str | None = None,
    note: str | None = None,
    ip=None,
    request_id=None,
) -> dict:
    return {
        "id": row_id,
        "ts": datetime(2026, 5, 16, 10, 0, 0, tzinfo=UTC),
        "actor": actor,
        "action": action,
        "target": target,
        "note": note,
        "ip": ip,
        "request_id": request_id,
    }


def test_list_audit_log_returns_paginated_response():
    row = _sample_row()
    app, _, _ = _make_audit_app([row])
    client = TestClient(app)
    resp = client.get("/api/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert len(body["data"]) == 1
    assert body["data"][0]["actor"] == "owner"
    assert body["data"][0]["action"] == "setting_change"


def test_list_audit_log_default_limit():
    """Default limit is 100."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    # The LIMIT $N arg is appended last to the args list passed to fetch
    call_args = mock_pool.fetch.call_args[0]
    # Last positional arg to pool.fetch is the limit value
    assert call_args[-1] == 100


def test_list_audit_log_limit_clamped():
    """limit > 1000 is rejected with HTTP 422."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?limit=9999")
    assert resp.status_code == 422


def test_list_audit_log_filter_by_actor():
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?actor=butler%3Aqa")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "actor = " in sql


def test_list_audit_log_filter_by_action():
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?action=rule_delete")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "action = " in sql


def test_list_audit_log_filter_by_since():
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?since=2026-01-01T00:00:00")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "ts >= " in sql


def test_list_audit_log_order_ts_desc():
    """SQL contains ORDER BY ts DESC."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "ORDER BY ts DESC" in sql


# ---------------------------------------------------------------------------
# GET /api/audit-log/{id}
# ---------------------------------------------------------------------------


def _make_audit_app_single(row: dict | None) -> tuple:
    """Wire a FastAPI app with mocked DatabaseManager for single-row reads."""
    mock_pool = AsyncMock()

    def _make_record(r: dict) -> MagicMock:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: r[key])
        return m

    mock_pool.fetchrow = AsyncMock(return_value=_make_record(row) if row is not None else None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool, mock_db


def test_get_audit_log_entry_by_id_found():
    row = _sample_row(row_id=5, action="model_change")
    app, _, _ = _make_audit_app_single(row)
    client = TestClient(app)
    resp = client.get("/api/audit-log/5")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["data"]["id"] == 5
    assert body["data"]["action"] == "model_change"


def test_get_audit_log_entry_by_id_not_found():
    app, _, _ = _make_audit_app_single(None)
    client = TestClient(app)
    resp = client.get("/api/audit-log/9999")
    assert resp.status_code == 404

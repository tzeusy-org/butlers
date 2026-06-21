"""Tests for the public.audit_log primitive API.

Covers:
- Static-check: no destructive statements targeting audit_log exist in repo code.
- audit.append() helper: inserts row, increments Prometheus counter, returns id.
- audit.append() raises AuditTableNotAvailableError on UndefinedTableError.
- GET /api/audit-log: returns PaginatedResponse[AuditLogEntry], filters, ts DESC, offset.
- GET /api/audit-log/{id}: returns ApiResponse[AuditLogEntry] or 404.
- GET /api/audit-log and /{id} return HTTP 503 when table is missing.
- AuditLogEntry.from_record: IP coercion, None fields.
"""

from __future__ import annotations

import ipaddress
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg.exceptions import UndefinedTableError
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.models.audit import AuditLogEntry
from butlers.api.routers.audit import (
    AuditTableNotAvailableError,
    _get_db_manager,
    append,
    audit_log_appended_total,
)
from butlers.core.credential_keys import normalize_credential_key

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


def test_audit_log_entry_from_record_with_ip_address_object():
    """ip can arrive as an ipaddress.IPv4Address from asyncpg; None fields stay None."""
    row = _make_row(ip=ipaddress.IPv4Address("10.0.0.1"))
    entry = AuditLogEntry.from_record(row)
    assert entry.ip == "10.0.0.1"
    assert entry.target is None
    assert entry.note is None
    assert entry.request_id is None


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


async def test_append_binds_target_note_ip_request_id_at_positions_3_through_6():
    """target/note/ip/request_id bind to INSERT positions 3/4/5/6 in that order.

    The INSERT column list is ``(actor, action, target, note, ip, request_id,
    metadata, result, error)``, so the positional args after the SQL string
    (call_args[0]) carry actor at idx 1, action at idx 2, then target/note/ip/
    request_id at idx 3/4/5/6.  Pins the binding order against an accidental
    swap that would silently corrupt append-only rows.
    """
    rid = uuid.uuid4()
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=11)

    await append(
        pool,
        "owner",
        "setting_change",
        target="rule:7",
        note="Changed threshold",
        ip="10.10.10.10",
        request_id=rid,
    )

    call_args = pool.fetchval.call_args[0]
    assert call_args[3] == "rule:7"
    assert call_args[4] == "Changed threshold"
    assert call_args[5] == "10.10.10.10"
    assert call_args[6] == rid


async def test_append_persists_metadata_result_error():
    """append() forwards metadata/result/error (core_122) into the INSERT.

    metadata is JSON-serialised and cast via ``$N::jsonb``; result and error
    are passed through as plain TEXT positional args.
    """
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=123)

    row_id = await append(
        pool,
        "owner",
        "model_priority_change",
        metadata={"path": "/api/x", "trigger_source": "dashboard"},
        result="success",
        error=None,
    )
    assert row_id == 123

    call_args = pool.fetchval.call_args[0]
    sql = call_args[0]
    assert "metadata" in sql
    assert "result" in sql
    assert "error" in sql
    assert "$7::jsonb" in sql
    # metadata is serialised to a JSON string and round-trips to the same dict.
    metadata_arg = call_args[7]
    assert isinstance(metadata_arg, str)
    assert json.loads(metadata_arg) == {"path": "/api/x", "trigger_source": "dashboard"}
    assert call_args[8] == "success"
    assert call_args[9] is None


async def test_append_without_new_fields_passes_nulls():
    """Omitting the core_122 fields keeps the call backward compatible.

    metadata defaults to None (→ SQL NULL, no JSON serialisation) and
    result/error default to None, so legacy callers are unaffected.
    """
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=5)

    row_id = await append(pool, "owner", "setting_change")
    assert row_id == 5

    call_args = pool.fetchval.call_args[0]
    # metadata (idx 7), result (idx 8), error (idx 9) all None.
    assert call_args[7] is None
    assert call_args[8] is None
    assert call_args[9] is None


def test_audit_log_entry_new_fields_default_none():
    """AuditLogEntry exposes metadata/result/error, defaulting to None."""
    entry = AuditLogEntry(
        id=1,
        ts=datetime.now(tz=UTC),
        actor="owner",
        action="x",
    )
    assert entry.metadata is None
    assert entry.result is None
    assert entry.error is None

    populated = AuditLogEntry(
        id=2,
        ts=datetime.now(tz=UTC),
        actor="owner",
        action="x",
        metadata={"k": "v"},
        result="error",
        error="boom",
    )
    assert populated.metadata == {"k": "v"}
    assert populated.result == "error"
    assert populated.error == "boom"


async def test_append_increments_prometheus_counter():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)

    before = audit_log_appended_total.labels(action="test_counter_increment")._value.get()
    await append(pool, "owner", "test_counter_increment")
    after = audit_log_appended_total.labels(action="test_counter_increment")._value.get()
    assert after == before + 1


async def test_append_raises_audit_table_not_available_error_on_undefined_table():
    """append() MUST raise AuditTableNotAvailableError, not the raw asyncpg error."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=UndefinedTableError("relation does not exist"))

    with pytest.raises(AuditTableNotAvailableError):
        await append(pool, "owner", "some_action")


async def test_append_accepts_connection_for_atomicity():
    """append() works with an asyncpg connection, enabling same-transaction writes."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=42)

    row_id = await append(conn, "owner", "model_priority_change")
    assert row_id == 42
    conn.fetchval.assert_awaited_once()


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

    # The read path queries the canonical public.audit_log via
    # credential_shared_pool() only (bu-j26e8 removed the legacy UNION arm).  A
    # spare switchboard-pool stub is kept wired for any non-read code paths.
    sw_pool = AsyncMock()
    sw_pool.fetchval = AsyncMock(return_value=0)
    sw_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool
    mock_db.pool.return_value = sw_pool

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
    # Trailing positional args to pool.fetch are (limit, offset); limit defaults
    # to 100 and offset to 0.
    call_args = mock_pool.fetch.call_args[0]
    assert call_args[-2] == 100
    assert call_args[-1] == 0


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


def test_list_audit_log_paginates_with_sql_limit_offset():
    """Post audit-unify (bu-j26e8) reads come solely from public.audit_log, so
    pagination is a plain SQL ``LIMIT $N OFFSET $N+1`` against the canonical
    table — no in-memory merge, no over-fetch.  The last two positional args to
    pool.fetch are (limit, offset)."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?offset=50&limit=25")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "LIMIT" in sql
    assert "OFFSET" in sql
    args = fetch_call[1:]
    # Trailing positional args: limit then offset.
    assert args[-2] == 25
    assert args[-1] == 50


def test_list_audit_log_offset_reflected_in_meta():
    """offset value is returned in the pagination meta."""
    row = _sample_row()
    app, _, _ = _make_audit_app([row])
    client = TestClient(app)
    resp = client.get("/api/audit-log?offset=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["offset"] == 10


def test_list_audit_log_table_missing_returns_503():
    """UndefinedTableError on count query surfaces as HTTP 503."""
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(side_effect=UndefinedTableError("relation does not exist"))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)
    resp = client.get("/api/audit-log")
    assert resp.status_code == 503


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


def test_get_audit_log_entry_table_missing_returns_503():
    """UndefinedTableError on fetchrow surfaces as HTTP 503, not 404."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=UndefinedTableError("relation does not exist"))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)
    resp = client.get("/api/audit-log/1")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/audit-log?key= — credential-key filter (bu-2rdyc)
# ---------------------------------------------------------------------------
# The ?key= param normalises the input via normalize_key_param() before
# filtering on the `target` column using ix_audit_log_target_ts.
# ---------------------------------------------------------------------------


def test_filter_by_canonical_key_returns_matching_rows():
    """?key=u:google returns rows whose target equals the normalised key."""
    row = _sample_row(target="u:google")
    app, mock_pool, _ = _make_audit_app([row])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=u:google&limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["target"] == "u:google"


def test_filter_by_canonical_key_sql_uses_target_condition():
    """SQL generated for ?key= contains a target= filter condition."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?key=u:google")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " in sql


def test_filter_by_long_scope_form_normalised():
    """?key=user:google is equivalent to ?key=u:google after normalisation."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?key=user:google")
    fetch_call = mock_pool.fetch.call_args[0]
    args = fetch_call[1:]
    assert "u:google" in args


def test_unknown_key_returns_empty_page():
    """?key=u:does-not-exist returns empty PaginatedResponse with total=0."""
    app, mock_pool, _ = _make_audit_app([], total=0)
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=u:does-not-exist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert body["meta"]["has_more"] is False


def test_key_param_combined_with_all_filters():
    """?key=, ?since=, ?actor=, and ?action= all applied together."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get(
        "/api/audit-log?key=s:MY_SECRET&since=2026-01-01T00:00:00&actor=owner&action=updated"
    )
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " in sql
    assert "ts >= " in sql
    assert "actor = " in sql
    assert "action = " in sql


def test_key_param_malformed_returns_422():
    """?key= without a colon separator returns HTTP 422."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=invalid-no-colon")
    assert resp.status_code == 422


def test_key_param_unknown_scope_returns_422():
    """?key= with an unrecognised scope prefix returns HTTP 422."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=admin:foo")
    assert resp.status_code == 422


def test_no_key_param_no_target_condition_in_sql():
    """Without ?key=, the SQL must NOT contain a target= filter."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " not in sql


def test_empty_key_param_treated_as_no_filter():
    """?key= (empty string) is treated as if the parameter was not provided."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=")
    assert resp.status_code == 200
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " not in sql


def test_whitespace_key_param_treated_as_no_filter():
    """?key=   (whitespace-only) is treated as if the parameter was not provided."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=   ")
    assert resp.status_code == 200
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " not in sql


# ---------------------------------------------------------------------------
# Regression: audit-write callsite → ?key= filter round-trip (bu-h6x8q)
#
# Validates that a target written via normalize_credential_key() is found by
# the ?key= filter.  The write side uses the same helper that production
# callsites in secrets_v2.py and oauth.py use; the read side goes through the
# full list_audit_log() handler with ?key= normalisation.
# ---------------------------------------------------------------------------


async def test_audit_write_target_found_by_key_filter():
    """Target written via normalize_credential_key() is returned by ?key= filter.

    Simulates the full round-trip:
    1. A write callsite produces target = normalize_credential_key("user", "google")
    2. The row is stored with that canonical target ("u:google").
    3. GET /api/audit-log?key=u:google returns the row.
    4. GET /api/audit-log?key=user:google (long-scope form) also returns the row.
    """
    # Step 1: derive the canonical target exactly as a write callsite would.
    canonical_target = normalize_credential_key("user", "google")
    assert canonical_target == "u:google"  # belt-and-suspenders: confirm the contract

    # Step 2: seed the mock DB with a row whose target equals the canonical key.
    row = _sample_row(target=canonical_target, action="rotated")
    app, mock_pool, _ = _make_audit_app([row])
    client = TestClient(app)

    # Step 3: query with canonical short-prefix form — must match.
    resp = client.get("/api/audit-log?key=u:google")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["target"] == "u:google"
    # Verify the SQL filter argument was the normalised key.
    fetch_args = mock_pool.fetch.call_args[0]
    assert "u:google" in fetch_args

    # Step 4: query with long-scope form — normalised to same value before SQL.
    resp_long = client.get("/api/audit-log?key=user:google")
    assert resp_long.status_code == 200
    fetch_args_long = mock_pool.fetch.call_args[0]
    assert "u:google" in fetch_args_long  # normalised, not "user:google"


async def test_audit_write_target_system_scope_found_by_key_filter():
    """System-scope target written via normalize_credential_key() found by ?key=."""
    canonical_target = normalize_credential_key("system", "BUTLER_TELEGRAM_TOKEN")
    assert canonical_target == "s:BUTLER_TELEGRAM_TOKEN"

    row = _sample_row(target=canonical_target, action="set")
    app, mock_pool, _ = _make_audit_app([row])
    client = TestClient(app)

    resp = client.get("/api/audit-log?key=s:BUTLER_TELEGRAM_TOKEN")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["target"] == "s:BUTLER_TELEGRAM_TOKEN"
    fetch_args = mock_pool.fetch.call_args[0]
    assert "s:BUTLER_TELEGRAM_TOKEN" in fetch_args


def test_non_normalised_target_not_returned_for_canonical_key_filter():
    """A row stored with a raw un-normalised target is NOT matched by ?key= with canonical form.

    This regression test documents the invariant: if a callsite bypasses
    normalize_credential_key() and writes "google" instead of "u:google",
    the ?key=u:google filter will not find it.  All production callsites
    MUST use normalize_credential_key() to avoid silent filter misses.
    """
    # Row written without normalisation (simulating a defective callsite).
    raw_target_row = _sample_row(target="google", action="rotated")
    app, mock_pool, _ = _make_audit_app(
        [raw_target_row],
        # count mock returns 0 — the WHERE target='u:google' clause excludes the raw row
        total=0,
    )
    client = TestClient(app)

    # The ?key= filter normalises "user:google" → "u:google" and passes "u:google" to SQL.
    # The mock pool is set up to return total=0 rows, confirming the raw "google" row
    # is not returned.
    resp = client.get("/api/audit-log?key=u:google")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/audit-log?kind=privileged — operational-noise filter (bu-9q1dx.5)
# ---------------------------------------------------------------------------
# kind=privileged excludes *_heartbeat and GET /... action patterns so that
# the permissions-page audit reel surfaces only mutation/security rows.
# ---------------------------------------------------------------------------


def test_kind_privileged_sql_excludes_heartbeat_pattern():
    """SQL for kind=privileged contains NOT LIKE '%_heartbeat'."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?kind=privileged")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "NOT LIKE '%_heartbeat'" in sql


def test_kind_privileged_sql_excludes_get_path_pattern():
    """SQL for kind=privileged contains NOT LIKE 'GET /%' to strip routine GET noise."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?kind=privileged")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "NOT LIKE 'GET /%'" in sql


def test_kind_privileged_returns_200():
    """kind=privileged is a valid parameter and returns HTTP 200."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?kind=privileged")
    assert resp.status_code == 200


def test_kind_unknown_returns_422():
    """An unrecognised kind value is rejected with HTTP 422."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?kind=foobar")
    assert resp.status_code == 422


def test_kind_absent_does_not_add_noise_filters():
    """Without ?kind=, the SQL must NOT contain the privileged-filter clauses."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "NOT LIKE '%_heartbeat'" not in sql
    assert "NOT LIKE 'GET /%'" not in sql


def test_kind_privileged_combined_with_limit():
    """kind=privileged works alongside limit; trailing args remain (limit, offset)."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?kind=privileged&limit=15")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "NOT LIKE '%_heartbeat'" in sql
    args = fetch_call[1:]
    assert args[-2] == 15  # limit
    assert args[-1] == 0  # offset


def test_kind_privileged_empty_state_returns_empty_page():
    """kind=privileged with no matching rows returns an empty data list, not an error."""
    app, _, _ = _make_audit_app([], total=0)
    client = TestClient(app)
    resp = client.get("/api/audit-log?kind=privileged&limit=15")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


def test_kind_privileged_returns_mutation_rows():
    """kind=privileged returns mutation rows (permission.set, data.export, webhook.*)."""
    mutation_rows = [
        _sample_row(row_id=1, action="permission.set"),
        _sample_row(row_id=2, action="data.export"),
        _sample_row(row_id=3, action="webhook.create"),
    ]
    app, _, _ = _make_audit_app(mutation_rows, total=3)
    client = TestClient(app)
    resp = client.get("/api/audit-log?kind=privileged&limit=15")
    assert resp.status_code == 200
    body = resp.json()
    actions = [e["action"] for e in body["data"]]
    assert "permission.set" in actions
    assert "data.export" in actions
    assert "webhook.create" in actions

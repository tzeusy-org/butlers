"""Integration tests for GET /api/secrets/audit/<scope>/<key>.

Covers the acceptance scenarios from bu-kjko6:
1. Hit — seeded audit rows returned in DESC order.
2. Miss — unknown key returns empty items, valid envelope.
3. Limit — ?limit is honoured.
4. deep_link — meta.deep_link contains correct canonical key.
5. Envelope conformance — {data: [...], meta: {deep_link: ...}}.
6. 422 on invalid scope.
7. Timestamp pre-formatting — ts is a human-friendly string, not a raw ISO datetime.

Plus the content-blind contract from bu-rh8z5: the stored audit note — provider
and exception text on a failed probe — never reaches this endpoint's response.

Spec anchor
-----------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
openspec/changes/project-audit-history-content-blind/specs/dashboard-api/spec.md
§Audit history endpoint
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import (
    _AUDIT_DEFAULT_LIMIT,
    _VALID_SCOPES,
    AuditEvent,
    _get_db_manager,
    _write_cli_audit,
    _write_credential_audit,
    _write_system_audit,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)

# Fixed noon-UTC instant for freezing the formatter's clock in tests that
# assert "today"/"yesterday".  Noon UTC means no calendar-day boundary
# ambiguity regardless of when CI runs.
_FROZEN_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)


@contextmanager
def _freeze_time(frozen_now: datetime = _FROZEN_NOW):
    """Freeze ``butlers.api.routers.secrets_v2.datetime.now`` to *frozen_now*.

    Wraps ``datetime`` so all construction/comparison helpers remain intact;
    only ``.now()`` is replaced.  Use this in tests that assert
    ``'today'``/``'yesterday'`` labels from ``_format_probe_time``.
    """
    frozen_dt = MagicMock(wraps=datetime)
    frozen_dt.now = MagicMock(return_value=frozen_now)
    with patch("butlers.api.routers.secrets_v2.datetime", frozen_dt):
        yield frozen_now


def _make_audit_row(
    *,
    ts: datetime | None = None,
    actor: str = "owner",
    action: str = "rotated",
    note: str | None = None,
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for audit rows."""
    m = MagicMock()
    data: dict = {
        "ts": ts or _NOW,
        "actor": actor,
        "action": action,
        "note": note,
    }
    m.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return m


def _make_db_manager_with_audit_rows(
    rows: list[MagicMock],
) -> MagicMock:
    """Build a mock DatabaseManager whose shared pool returns the given audit rows."""
    shared_pool = AsyncMock()
    shared_pool.fetch = AsyncMock(return_value=rows)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    """Create a TestClient with the given mock DatabaseManager."""
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Module-level constant assertions
# ---------------------------------------------------------------------------


def test_valid_scopes_contains_expected_values():
    """_VALID_SCOPES must include exactly user, system, cli."""
    assert _VALID_SCOPES == frozenset({"user", "system", "cli"})


@pytest.mark.parametrize(
    ("writer", "writer_kwargs"),
    [
        (_write_credential_audit, {"provider": "spotify"}),
        (_write_system_audit, {"key": "SPOTIFY_ACCESS_TOKEN"}),
        (_write_cli_audit, {"credential_id": "codex"}),
    ],
)
async def test_credential_failure_audit_writers_persist_error_outcome(
    monkeypatch,
    writer,
    writer_kwargs,
) -> None:
    """Every credential audit helper must make probe-style failures visible to /issues.

    This is deliberately an append-boundary contract rather than a source-text
    assertion: a new helper must carry the same durable `result="error"` /
    diagnostic pair before it can claim to have written a failed credential
    event.
    """
    from butlers.api.routers import secrets_v2

    append = AsyncMock(return_value=1)
    monkeypatch.setattr(secrets_v2.audit_router, "append", append)

    await writer(
        AsyncMock(),
        action="failed",
        note="Probe failed: invalid token; probe_status=401",
        error="invalid token",
        **writer_kwargs,
    )

    append.assert_awaited_once()
    call = append.await_args
    assert call.args[2] == "failed"
    assert call.kwargs["result"] == "error"
    assert call.kwargs["error"] == "invalid token"


@pytest.mark.parametrize(
    ("writer", "writer_kwargs", "action"),
    [
        (_write_credential_audit, {"provider": "spotify"}, "verified"),
        (_write_system_audit, {"key": "SPOTIFY_ACCESS_TOKEN"}, "rotated"),
        (_write_cli_audit, {"credential_id": "codex"}, "attempted"),
    ],
)
async def test_credential_success_audit_writers_persist_success_outcome(
    monkeypatch,
    writer,
    writer_kwargs,
    action,
) -> None:
    """Credential lifecycle success events must not masquerade as unknown."""
    from butlers.api.routers import secrets_v2

    append = AsyncMock(return_value=1)
    monkeypatch.setattr(secrets_v2.audit_router, "append", append)

    await writer(AsyncMock(), action=action, note="completed", **writer_kwargs)

    append.assert_awaited_once()
    call = append.await_args
    assert call.args[2] == action
    assert call.kwargs["result"] == "success"
    assert call.kwargs["error"] is None


# ---------------------------------------------------------------------------
# Scenario 1: Hit — seeded rows returned in DESC order
# ---------------------------------------------------------------------------


def test_audit_history_hit_returns_rows_desc():
    """Rows returned for a known key, newest first.

    The formatter's clock is frozen to noon UTC so both timestamps are
    on the same calendar day and the 'today' assertion is always correct.
    """
    older = _FROZEN_NOW - timedelta(hours=2)
    newer = _FROZEN_NOW - timedelta(minutes=5)

    rows = [
        _make_audit_row(ts=newer, actor="owner", action="rotated"),
        _make_audit_row(ts=older, actor="system", action="connected"),
    ]
    mock_db = _make_db_manager_with_audit_rows(rows)
    client = _build_app(mock_db)

    with _freeze_time():
        resp = client.get("/api/secrets/audit/system/SOME_KEY")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "data" in body
    assert "meta" in body
    data = body["data"]
    assert len(data) == 2

    # First row is newest — uses "today" relative format
    assert "today" in data[0]["ts"]
    assert data[0]["actor"] == "owner"
    assert data[0]["action"] == "rotated"

    assert data[1]["actor"] == "system"
    assert data[1]["action"] == "connected"


def test_audit_history_never_publishes_the_stored_note():
    """The stored audit note must not reach the wire (bu-rh8z5).

    Absence sentinel.  A failed probe plants free provider text in the audit
    row (``_write_credential_audit`` persists ``"Probe failed: <text>"``), so
    the seeded rows here carry an obviously-synthetic stand-in for it.  The
    endpoint is content-blind: that stand-in must appear nowhere in the
    response bytes, and no event may carry a ``note`` key at all.

    The stand-in is generated per run rather than written as a literal —
    a test proving free text absent must not itself spell that text out.
    """
    sentinel = f"synthetic-audit-diagnostic-{uuid4().hex}"
    rows = [
        _make_audit_row(action="failed", note=sentinel),
        _make_audit_row(action="connected", note=None),
    ]
    mock_db = _make_db_manager_with_audit_rows(rows)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/audit/system/KEY")
    assert resp.status_code == 200

    if sentinel.encode() in resp.content:
        pytest.fail("Audit endpoint published the stored audit note in its response body.")

    data = resp.json()["data"]
    assert len(data) == 2
    for event in data:
        assert "note" not in event, "AuditEvent published a note field"


# ---------------------------------------------------------------------------
# Scenario 2: Miss — unknown key, empty items, valid envelope
# ---------------------------------------------------------------------------


def test_audit_history_miss_returns_empty_list():
    """Unknown credential key returns empty data list with HTTP 200."""
    mock_db = _make_db_manager_with_audit_rows([])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/audit/system/DOES_NOT_EXIST")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["data"] == []
    assert "meta" in body
    assert "deep_link" in body["meta"]


def test_audit_history_miss_envelope_valid():
    """Empty response still conforms to ApiResponse<list[AuditEvent]> envelope."""
    mock_db = _make_db_manager_with_audit_rows([])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/audit/user/nonexistent")
    assert resp.status_code == 200
    body = resp.json()
    # Envelope: only data and meta at top level
    assert set(body.keys()) <= {"data", "meta", "error"}
    assert isinstance(body["data"], list)
    assert isinstance(body["meta"], dict)


# ---------------------------------------------------------------------------
# Scenario 3: Limit honoured
# ---------------------------------------------------------------------------


def test_audit_history_limit_default_used():
    """Default limit=10 is the query default (validated by constant)."""
    # The mock always returns what fetch() provides; we verify the SQL is called
    # with the correct limit by checking mock call args.
    shared_pool = AsyncMock()
    shared_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)

    resp = client.get("/api/secrets/audit/system/SOME_KEY")
    assert resp.status_code == 200

    # Verify fetch was called with limit=10 (default)
    call_args = shared_pool.fetch.call_args
    # Second positional arg after the SQL string and canonical key is limit
    args = call_args[0]  # positional args tuple
    assert args[2] == _AUDIT_DEFAULT_LIMIT


def test_audit_history_limit_custom_respected():
    """?limit=3 is passed through to the SQL query."""
    shared_pool = AsyncMock()
    shared_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)

    resp = client.get("/api/secrets/audit/system/SOME_KEY?limit=3")
    assert resp.status_code == 200

    call_args = shared_pool.fetch.call_args
    args = call_args[0]
    assert args[2] == 3


def test_audit_history_limit_max_enforced():
    """?limit= values above 50 are rejected by FastAPI validation (422)."""
    mock_db = _make_db_manager_with_audit_rows([])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/audit/system/KEY?limit=999")
    assert resp.status_code == 422


def test_audit_history_limit_zero_rejected():
    """?limit=0 is below ge=1 and must return 422."""
    mock_db = _make_db_manager_with_audit_rows([])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/audit/system/KEY?limit=0")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Error-handling: pool unavailable → 503
# ---------------------------------------------------------------------------


def test_audit_history_pool_unavailable_returns_503():
    """If credential_shared_pool() raises KeyError, the endpoint returns 503."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.credential_shared_pool = MagicMock(
        side_effect=KeyError("Shared credential pool is not configured")
    )

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)

    resp = client.get("/api/secrets/audit/system/SOME_KEY")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Scenario 4: deep_link contains canonical key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "key", "expected_deep_link"),
    [
        ("system", "BUTLER_TELEGRAM_TOKEN", "/audit-log?key=s:BUTLER_TELEGRAM_TOKEN"),
        ("user", "google", "/audit-log?key=u:google"),
        ("cli", "claude", "/audit-log?key=c:claude"),
    ],
)
def test_audit_history_deep_link_canonical_key(scope, key, expected_deep_link):
    """meta.deep_link normalises scope to its s:/u:/c: prefix; present even with no rows."""
    mock_db = _make_db_manager_with_audit_rows([])
    client = _build_app(mock_db)

    resp = client.get(f"/api/secrets/audit/{scope}/{key}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["deep_link"] == expected_deep_link


# ---------------------------------------------------------------------------
# Scenario 5: Envelope conformance
# ---------------------------------------------------------------------------


def test_audit_history_envelope_has_data_and_meta():
    """Response always has {data: [...], meta: {deep_link: ...}}."""
    mock_db = _make_db_manager_with_audit_rows([_make_audit_row()])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/audit/system/KEY")
    assert resp.status_code == 200
    body = resp.json()

    # Top-level envelope
    assert "data" in body
    assert "meta" in body
    # No extra top-level keys
    assert set(body.keys()) <= {"data", "meta", "error"}

    # data is a list
    assert isinstance(body["data"], list)

    # meta has deep_link
    assert "deep_link" in body["meta"]


# ---------------------------------------------------------------------------
# Scenario 6: Invalid scope → 422
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_scope",
    ["unknown", "admin", "USER", "System", "CLI", "u"],
)
def test_audit_history_invalid_scope_returns_422(bad_scope: str):
    """Scope values not in {user, system, cli} must return 422."""
    mock_db = _make_db_manager_with_audit_rows([])
    client = _build_app(mock_db)

    resp = client.get(f"/api/secrets/audit/{bad_scope}/some-key")
    assert resp.status_code == 422, f"Expected 422 for scope={bad_scope!r}, got {resp.status_code}"


def test_audit_history_empty_scope_not_routed():
    """An empty scope path segment results in a non-2xx response (404 or 422)."""
    mock_db = _make_db_manager_with_audit_rows([])
    client = _build_app(mock_db)

    # Empty path segment "/api/secrets/audit//some-key" does not match the route
    # (FastAPI strips it), returning 404 — this is an acceptable rejection.
    resp = client.get("/api/secrets/audit//some-key")
    assert resp.status_code in (404, 422), (
        f"Expected 404 or 422 for empty scope, got {resp.status_code}"
    )


@pytest.mark.parametrize("valid_scope", ["user", "system", "cli"])
def test_audit_history_valid_scopes_accepted(valid_scope: str):
    """All three valid scopes return HTTP 200."""
    mock_db = _make_db_manager_with_audit_rows([])
    client = _build_app(mock_db)

    resp = client.get(f"/api/secrets/audit/{valid_scope}/some-key")
    assert resp.status_code == 200, (
        f"Expected 200 for scope={valid_scope!r}, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Unit-level: AuditEvent model
# ---------------------------------------------------------------------------


def test_audit_event_model_carries_no_note_field():
    """AuditEvent round-trips ts/actor/action and has no note field at all.

    Pinned at the model rather than only at the route: the free text has no
    wire representation to reintroduce by accident (bu-rh8z5).
    """
    event = AuditEvent(ts="yesterday 09:08", actor="system", action="connected")
    assert event.ts == "yesterday 09:08"
    assert event.actor == "system"
    assert event.action == "connected"

    assert set(AuditEvent.model_fields) == {"ts", "actor", "action"}
    assert "note" not in event.model_dump()

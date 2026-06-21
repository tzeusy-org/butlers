"""Tests for CLI runtime credential mutation endpoints.

Covers bu-ygrbk: POST /api/secrets/cli/<id>/rotate and
POST /api/secrets/cli/<id>/revoke.

Test matrix:
- rotate returns 200 with {value, fingerprint} envelope
- rotate value is non-empty and fingerprint matches sha256[:8] of value
- rotate writes 'rotated' audit row with canonical key 'c:<id>'
- rotate 404 on unknown id
- rotate does NOT expose raw value via subsequent GET /api/secrets/cli/<id>
- revoke returns 200 with {status: "revoked"}
- revoke writes 'disconnected' audit row
- revoke 404 on unknown id
- envelope conformance: data + meta present on all 200 responses

Spec anchor
-----------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
§CLI runtime mutations
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    return m


def _make_cli_row(
    *,
    key: str = "cli-token-abc123",
    value: str = "old_cli_secret_value",
    description: str | None = "My CLI Token",
    last_verified: datetime | None = None,
    last_test_ok: bool | None = None,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
    expires_at: datetime | None = None,
) -> MagicMock:
    return _make_row(
        secret_key=key,
        secret_value=value,
        category="cli",
        description=description,
        last_verified=last_verified,
        last_test_ok=last_test_ok,
        last_test_code=last_test_code,
        last_test_message=last_test_message,
        expires_at=expires_at,
        created_at=_NOW,
    )


def _make_shared_pool(
    *,
    cli_row: MagicMock | None = None,
    probe_row: MagicMock | None = None,
    execute_ok: bool = True,
    execute_return: str = "UPDATE 1",
) -> AsyncMock:
    """Build a mock shared-pool that supports fetchrow and execute."""
    shared_pool = AsyncMock()

    async def _fetchrow(sql, *args):
        if "secret_probe_log" in sql:
            return probe_row
        if "category = 'cli'" in sql:
            return cli_row
        return None

    shared_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    shared_pool.fetch = AsyncMock(return_value=[])

    if execute_ok:
        shared_pool.execute = AsyncMock(return_value=execute_return)
    else:
        shared_pool.execute = AsyncMock(side_effect=Exception("DB error"))

    # Fake transaction context manager (unused by CLI mutations, but required
    # by the pool mock infrastructure).
    fake_conn = AsyncMock()
    fake_conn.fetchrow = shared_pool.fetchrow
    fake_conn.fetch = shared_pool.fetch
    fake_conn.execute = shared_pool.execute

    @asynccontextmanager
    async def _transaction():
        yield

    fake_conn.transaction = _transaction

    @asynccontextmanager
    async def _acquire():
        yield fake_conn

    shared_pool.acquire = _acquire

    return shared_pool


def _make_db(
    *,
    cli_row: MagicMock | None = None,
    probe_row: MagicMock | None = None,
    shared_pool_available: bool = True,
    execute_ok: bool = True,
) -> MagicMock:
    """Build a mock DatabaseManager for CLI mutation endpoint tests."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=AsyncMock())

    if shared_pool_available:
        shared_pool = _make_shared_pool(
            cli_row=cli_row,
            probe_row=probe_row,
            execute_ok=execute_ok,
        )
        mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    else:
        mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no shared pool"))

    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    """Create a TestClient with the given mock DatabaseManager."""
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/cli/<id>/rotate
# ---------------------------------------------------------------------------


def test_rotate_cli_returns_value_and_fingerprint_envelope():
    """rotate returns 200 with a {data, meta} envelope whose value is fresh and
    whose fingerprint is sha256[:8] of that value."""
    old_value = "old_cli_secret_value"
    cli_row = _make_cli_row(key="cli-token-abc123", value=old_value)
    mock_db = _make_db(cli_row=cli_row)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/cli-token-abc123/rotate")
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    data = body["data"]
    value = data["value"]
    # Freshly generated, non-empty, and different from the old stored value.
    assert value
    assert value != old_value
    # Fingerprint is sha256[:8] hex of the returned value.
    assert data["fingerprint"] == hashlib.sha256(value.encode()).hexdigest()[:8]


def test_rotate_cli_writes_rotated_audit_row_with_canonical_target(monkeypatch):
    """rotate appends a 'rotated' audit row targeting the canonical key 'c:<id>'."""
    cli_row = _make_cli_row(key="cli-token-abc123")
    mock_db = _make_db(cli_row=cli_row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/cli/cli-token-abc123/rotate")
    assert resp.status_code == 200

    rotated = [c for c in audit_calls if c["action"] == "rotated"]
    assert rotated, f"Expected 'rotated' audit action; got: {audit_calls}"
    assert rotated[0].get("target") == "c:cli-token-abc123"


def test_rotate_cli_404_on_unknown_id():
    """rotate returns 404 when no CLI token with the given id exists."""
    mock_db = _make_db(cli_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/does-not-exist/rotate")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: paste-to-save (owner-supplied value) — bu-f63t9
#
# Regression: the token-mode "set token" textarea posts the pasted value to the
# rotate endpoint. Previously the endpoint discarded it and minted a random one
# (silent data loss), and 404'd for a never_set provider. These tests pin the
# fix: the exact supplied value is persisted, and first-time save works.
# ---------------------------------------------------------------------------


def _persisted_secret_value(shared_pool) -> str | None:
    """Return the secret_value passed to the INSERT/UPDATE on butler_secrets."""
    for call in shared_pool.execute.await_args_list:
        sql = call.args[0] if call.args else ""
        if "butler_secrets" in sql and (
            "INSERT INTO butler_secrets" in sql or "UPDATE butler_secrets" in sql
        ):
            # secret_key is $1, secret_value is $2 for both INSERT and UPDATE.
            return call.args[2]
    return None


def test_rotate_cli_persists_user_supplied_value_verbatim():
    """When a value is supplied, that EXACT value is persisted (not a random one)."""
    cli_row = _make_cli_row(key="cli-token-abc123", value="old_cli_secret_value")
    mock_db = _make_db(cli_row=cli_row)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    supplied = "my-own-pasted-token-XYZ"
    resp = client.post(
        "/api/secrets/cli/cli-token-abc123/rotate",
        json={"value": supplied},
    )
    assert resp.status_code == 200
    # Response echoes back the supplied value, not a fresh random one.
    assert resp.json()["data"]["value"] == supplied
    # And the supplied value is what got persisted to butler_secrets.
    assert _persisted_secret_value(shared_pool) == supplied


def test_rotate_cli_fingerprint_matches_user_supplied_value():
    """Fingerprint is sha256[:8] of the SUPPLIED value, confirming it was kept."""
    cli_row = _make_cli_row(key="cli-token-abc123")
    mock_db = _make_db(cli_row=cli_row)
    client = _build_app(mock_db)

    supplied = "another-pasted-token"
    resp = client.post(
        "/api/secrets/cli/cli-token-abc123/rotate",
        json={"value": supplied},
    )
    assert resp.status_code == 200
    expected_fp = hashlib.sha256(supplied.encode()).hexdigest()[:8]
    assert resp.json()["data"]["fingerprint"] == expected_fp


def test_rotate_cli_first_save_never_set_succeeds():
    """First-time owner-supplied save for a never_set provider returns 200 (no 404)."""
    # No existing row → previously this 404'd. With a supplied value it UPSERTs.
    mock_db = _make_db(cli_row=None)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    supplied = "first-time-token"
    resp = client.post(
        "/api/secrets/cli/cli-auth-new-provider/rotate",
        json={"value": supplied},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["value"] == supplied
    assert _persisted_secret_value(shared_pool) == supplied


def test_rotate_cli_empty_supplied_value_falls_back_to_generate():
    """An empty/whitespace value is treated as 'generate for me' (random)."""
    cli_row = _make_cli_row(key="cli-token-abc123", value="old_cli_secret_value")
    mock_db = _make_db(cli_row=cli_row)
    client = _build_app(mock_db)

    resp = client.post(
        "/api/secrets/cli/cli-token-abc123/rotate",
        json={"value": "   "},
    )
    assert resp.status_code == 200
    # Falls back to generate: value is non-empty and differs from old.
    new_value = resp.json()["data"]["value"]
    assert new_value
    assert new_value != "old_cli_secret_value"


def test_rotate_cli_no_body_still_generates_and_requires_existing():
    """No body → generate path: still 404s for an unknown id (rotate semantics)."""
    mock_db = _make_db(cli_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/does-not-exist/rotate")
    assert resp.status_code == 404


def test_rotate_cli_get_does_not_return_raw_value_after_rotate():
    """Subsequent GET /api/secrets/cli/<id> does NOT return a raw value field.

    This verifies the 'exactly once' contract: the raw value is only
    available in the rotate response body; GET endpoints return fingerprint
    only and never expose the raw value.
    """
    cli_row = _make_cli_row(key="cli-token-abc123", value="stored_tok", last_test_ok=True)
    mock_db = _make_db(cli_row=cli_row)
    client = _build_app(mock_db)

    # GET the credential — verify no 'value' field is exposed.
    resp = client.get("/api/secrets/cli/cli-token-abc123")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "value" not in data, (
        f"GET /api/secrets/cli/<id> must NOT expose raw value; got keys: {list(data.keys())}"
    )
    # Fingerprint is present (not None) since the credential is set.
    assert "fingerprint" in data


def test_rotate_cli_get_inventory_does_not_return_raw_value():
    """GET /api/secrets/inventory does NOT return raw values for CLI tokens."""
    cli_row = _make_cli_row(key="cli-token-abc123", value="stored_tok")

    # Build a DB mock with the CLI row returned in the fetch list.
    # _fetch_cli_secrets uses: WHERE category = 'cli'
    # _fetch_user_secrets does NOT filter by category, so we must return []
    # for the user-secrets query to avoid a KeyError on missing 'value' field.
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    shared_pool = AsyncMock()

    async def _shared_fetch(sql, *args):
        if "category = 'cli'" in sql:
            return [cli_row]
        # User-secrets and any other query — return empty list.
        return []

    shared_pool.fetch = AsyncMock(side_effect=_shared_fetch)
    shared_pool.fetchrow = AsyncMock(return_value=None)
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    mock_db.pool = MagicMock(side_effect=KeyError("no butler pool"))

    client = _build_app(mock_db)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    body = resp.json()
    cli_list = body["data"]["cli"]
    assert len(cli_list) > 0, "Expected at least one CLI row in inventory"
    for row in cli_list:
        assert "value" not in row, (
            f"Inventory must not expose raw value; CLI row keys: {list(row.keys())}"
        )


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/cli/<id>/revoke
# ---------------------------------------------------------------------------


def test_revoke_cli_returns_revoked_status_envelope():
    """revoke returns 200 with a {data, meta} envelope and {status: 'revoked'}."""
    cli_row = _make_cli_row(key="cli-token-abc123")
    mock_db = _make_db(cli_row=cli_row)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/cli-token-abc123/revoke")
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    assert body["data"]["status"] == "revoked"


def test_revoke_cli_writes_disconnected_audit_row_with_canonical_target(monkeypatch):
    """revoke appends a 'disconnected' audit row targeting the canonical key 'c:<id>'."""
    cli_row = _make_cli_row(key="cli-token-abc123")
    mock_db = _make_db(cli_row=cli_row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/cli/cli-token-abc123/revoke")
    assert resp.status_code == 200

    disconnected = [c for c in audit_calls if c["action"] == "disconnected"]
    assert disconnected, f"Expected 'disconnected' audit action; got: {audit_calls}"
    assert disconnected[0].get("target") == "c:cli-token-abc123"


def test_revoke_cli_404_on_unknown_id():
    """revoke returns 404 when no CLI token with the given id exists."""
    mock_db = _make_db(cli_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/does-not-exist/revoke")
    assert resp.status_code == 404

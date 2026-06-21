"""Tests for system-credential mutation endpoints.

Covers bu-kw2am:
- POST   /api/secrets/system/<key>          — set / rotate / override
- POST   /api/secrets/system/<key>/probe    — probe with rate-limit
- DELETE /api/secrets/system/<key>?target=  — disconnect / revoke

Test matrix:
- set new (audit 'set'), rotate existing (audit 'rotated'), override add (audit 'overrode')
- override remove (audit 'revoked'), shared delete (audit 'disconnected')
- probe ok (audit 'verified'), probe fail (audit 'failed')
- probe rate-limit enforced (429 on second call within TTL)
- 404 on unknown key for probe and delete
- Envelope conformance (data + meta keys present)

Spec anchor
-----------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
§System credential mutations
openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md
§Audit Action Enum Extension
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import _get_db_manager, _system_probe_timestamps

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


def _make_butler_secrets_row(
    *,
    secret_key: str = "MY_API_KEY",
    secret_value: str = "s3cr3t",
    category: str = "general",
    description: str | None = None,
    expires_at: datetime | None = None,
    last_verified: datetime | None = None,
    last_test_ok: bool | None = True,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    return _make_row(
        secret_key=secret_key,
        secret_value=secret_value,
        category=category,
        description=description,
        expires_at=expires_at,
        last_verified=last_verified,
        last_test_ok=last_test_ok,
        last_test_code=last_test_code,
        last_test_message=last_test_message,
        created_at=created_at or _NOW,
        is_sensitive=True,
        updated_at=_NOW,
    )


def _make_butler_pool(
    *,
    existing_row: MagicMock | None = None,
    probe_row: MagicMock | None = None,
    execute_ok: bool = True,
) -> AsyncMock:
    """Build a mock butler pool for butler_secrets operations."""
    pool = AsyncMock()

    async def _fetchrow(sql, *args):
        if "secret_probe_log" in sql:
            return probe_row
        # For any butler_secrets SELECT (including the existence check)
        return existing_row

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(return_value=[])

    if execute_ok:
        pool.execute = AsyncMock(return_value="OK")
    else:
        pool.execute = AsyncMock(side_effect=Exception("DB error"))

    # Fake acquire/transaction for probe endpoint.
    fake_conn = AsyncMock()
    fake_conn.fetchrow = pool.fetchrow
    fake_conn.fetch = pool.fetch
    fake_conn.execute = pool.execute
    fake_conn.fetchval = AsyncMock(return_value=1)

    @asynccontextmanager
    async def _transaction():
        yield

    fake_conn.transaction = _transaction

    @asynccontextmanager
    async def _acquire():
        yield fake_conn

    pool.acquire = _acquire

    return pool


def _make_db(
    *,
    switchboard_row: MagicMock | None = None,
    butler_row: MagicMock | None = None,
    probe_row: MagicMock | None = None,
    switchboard_available: bool = True,
    butler_names: list[str] | None = None,
    execute_ok: bool = True,
    shared_pool_available: bool = True,
) -> MagicMock:
    """Build a mock DatabaseManager for system mutation endpoint tests.

    Registers:
    - "switchboard" pool (if switchboard_available) with switchboard_row
    - Extra butlers in butler_names with butler_row
    - Shared credential pool (if shared_pool_available) for probe_log writes
    """
    mock_db = MagicMock(spec=DatabaseManager)
    all_butler_names: list[str] = []

    switchboard_pool = _make_butler_pool(
        existing_row=switchboard_row,
        probe_row=probe_row,
        execute_ok=execute_ok,
    )
    butler_pool = _make_butler_pool(
        existing_row=butler_row,
        probe_row=probe_row,
        execute_ok=execute_ok,
    )

    if butler_names:
        all_butler_names.extend(butler_names)

    if switchboard_available:
        all_butler_names = ["switchboard"] + [b for b in all_butler_names if b != "switchboard"]

    mock_db.butler_names = all_butler_names

    def _pool(name: str) -> AsyncMock:
        if name == "switchboard":
            if not switchboard_available:
                raise KeyError("switchboard not available")
            return switchboard_pool
        if butler_names and name in butler_names:
            return butler_pool
        raise KeyError(f"No pool for butler: {name}")

    mock_db.pool = MagicMock(side_effect=_pool)

    # Shared credential pool (used by probe_log INSERT).
    shared_pool = _make_butler_pool(
        existing_row=switchboard_row,
        probe_row=probe_row,
        execute_ok=execute_ok,
    )
    if shared_pool_available:
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
# Tests: POST /api/secrets/system/<key>  — set new (first-time)
# ---------------------------------------------------------------------------


def test_set_new_returns_200_and_writes_set_audit_canonical(monkeypatch):
    """POST /api/secrets/system/<key> for a new key returns 200 with
    ApiResponse<SystemSecretDetail> and appends a 'set' audit row targeting 's:<key>'."""
    # No existing row → INSERT path
    new_row = _make_butler_secrets_row(secret_key="MY_KEY", last_test_ok=True)
    mock_db = _make_db(switchboard_row=None)
    # After INSERT we need the re-fetch to return the new row.
    switchboard_pool = mock_db.pool("switchboard")

    call_count = [0]

    async def _fetchrow_side_effect(sql, *args):
        if "secret_probe_log" in sql:
            return None
        call_count[0] += 1
        # First call (existence check) → None; re-fetch after write → new_row.
        return None if call_count[0] == 1 else new_row

    switchboard_pool.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/MY_KEY", json={"value": "s3cr3t"})
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body

    set_rows = [c for c in audit_calls if c["action"] == "set"]
    assert set_rows, f"Expected 'set' audit action; got: {audit_calls}"
    assert set_rows[0].get("target") == "s:MY_KEY"


def test_set_new_persists_category_on_insert():
    """POST with a category threads it into the first-time INSERT (bu-occhw)."""
    new_row = _make_butler_secrets_row(secret_key="MY_API_KEY", last_test_ok=True)
    mock_db = _make_db(switchboard_row=None)
    switchboard_pool = mock_db.pool("switchboard")

    call_count = [0]

    async def _fetchrow_side_effect(sql, *args):
        if "secret_probe_log" in sql:
            return None
        call_count[0] += 1
        return None if call_count[0] == 1 else new_row

    switchboard_pool.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

    insert_calls: list[tuple] = []
    orig_execute = switchboard_pool.execute

    async def _spy_execute(sql, *args):
        if "INSERT INTO butler_secrets" in sql:
            insert_calls.append((sql, args))
        return await orig_execute(sql, *args)

    switchboard_pool.execute = AsyncMock(side_effect=_spy_execute)

    client = _build_app(mock_db)
    resp = client.post(
        "/api/secrets/system/MY_API_KEY",
        json={"value": "s3cr3t", "category": "telegram"},
    )
    assert resp.status_code == 200
    assert insert_calls, "Expected an INSERT INTO butler_secrets on first-time create"
    sql, args = insert_calls[0]
    assert "category" in sql
    # category is the third positional arg ($3) in the INSERT.
    assert "telegram" in args


def test_set_new_category_defaults_to_general():
    """POST without a category defaults to 'general' on the INSERT (bu-occhw)."""
    new_row = _make_butler_secrets_row(secret_key="MY_API_KEY", last_test_ok=True)
    mock_db = _make_db(switchboard_row=None)
    switchboard_pool = mock_db.pool("switchboard")

    call_count = [0]

    async def _fetchrow_side_effect(sql, *args):
        if "secret_probe_log" in sql:
            return None
        call_count[0] += 1
        return None if call_count[0] == 1 else new_row

    switchboard_pool.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)

    insert_calls: list[tuple] = []
    orig_execute = switchboard_pool.execute

    async def _spy_execute(sql, *args):
        if "INSERT INTO butler_secrets" in sql:
            insert_calls.append((sql, args))
        return await orig_execute(sql, *args)

    switchboard_pool.execute = AsyncMock(side_effect=_spy_execute)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/MY_API_KEY", json={"value": "s3cr3t"})
    assert resp.status_code == 200
    assert insert_calls
    _sql, args = insert_calls[0]
    assert "general" in args


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/system/<key>  — rotate existing
# ---------------------------------------------------------------------------


def test_rotate_existing_returns_200_and_writes_rotated_audit_canonical(monkeypatch):
    """POST on an existing key returns 200 and appends a 'rotated' audit row
    targeting the canonical key 's:<key>'."""
    existing_row = _make_butler_secrets_row(secret_key="EXISTING_KEY", last_test_ok=True)
    mock_db = _make_db(switchboard_row=existing_row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/EXISTING_KEY", json={"value": "new-val"})
    assert resp.status_code == 200
    assert "meta" in resp.json()

    rotated = [c for c in audit_calls if c["action"] == "rotated"]
    assert rotated, f"Expected 'rotated' audit action; got: {audit_calls}"
    assert rotated[0].get("target") == "s:EXISTING_KEY"


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/system/<key>  — per-butler override
# ---------------------------------------------------------------------------


def test_override_add_returns_200_and_writes_overrode_audit(monkeypatch):
    """POST with target=<butler> creates an override row, returns 200, and writes an
    'overrode' audit action."""
    butler_row = _make_butler_secrets_row(secret_key="MY_KEY", last_test_ok=True)
    mock_db = _make_db(butler_row=butler_row, butler_names=["health"])

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    resp = client.post(
        "/api/secrets/system/MY_KEY", json={"value": "override-val", "target": "health"}
    )
    assert resp.status_code == 200
    assert "meta" in resp.json()
    assert any(c["action"] == "overrode" for c in audit_calls), (
        f"Expected 'overrode' audit action; got: {audit_calls}"
    )


def test_override_add_404_on_unknown_butler():
    """POST with target=<unregistered-butler> returns 404."""
    mock_db = _make_db()  # no butler named "unknown"
    client = _build_app(mock_db)
    resp = client.post(
        "/api/secrets/system/MY_KEY", json={"value": "val", "target": "unknown_butler"}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: DELETE /api/secrets/system/<key>?target=shared  — disconnect
# ---------------------------------------------------------------------------


def test_delete_shared_returns_200_disconnected_and_writes_audit(monkeypatch):
    """DELETE ?target=shared returns 200 with {status: 'disconnected'} and writes a
    'disconnected' audit action."""
    existing_row = _make_butler_secrets_row(secret_key="DEL_KEY")
    mock_db = _make_db(switchboard_row=existing_row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    resp = client.delete("/api/secrets/system/DEL_KEY?target=shared")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"
    assert any(c["action"] == "disconnected" for c in audit_calls), (
        f"Expected 'disconnected' audit action; got: {audit_calls}"
    )


def test_delete_shared_404_on_unknown_key():
    """DELETE ?target=shared returns 404 when key does not exist."""
    mock_db = _make_db(switchboard_row=None)
    client = _build_app(mock_db)
    resp = client.delete("/api/secrets/system/NO_SUCH_KEY?target=shared")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: DELETE /api/secrets/system/<key>?target=<butler>  — revoke override
# ---------------------------------------------------------------------------


def test_delete_override_returns_200_revoked_and_writes_revoked_audit(monkeypatch):
    """DELETE ?target=<butler> returns 200 with {status: 'revoked'} and writes a
    'revoked' audit action."""
    butler_row = _make_butler_secrets_row(secret_key="OVR_KEY")
    mock_db = _make_db(butler_row=butler_row, butler_names=["health"])

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    resp = client.delete("/api/secrets/system/OVR_KEY?target=health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "revoked"
    assert any(c["action"] == "revoked" for c in audit_calls), (
        f"Expected 'revoked' audit action; got: {audit_calls}"
    )


def test_delete_override_404_on_unknown_butler():
    """DELETE ?target=<unregistered-butler> returns 404."""
    mock_db = _make_db()
    client = _build_app(mock_db)
    resp = client.delete("/api/secrets/system/OVR_KEY?target=unknown_butler")
    assert resp.status_code == 404


def test_delete_override_404_on_unknown_key():
    """DELETE ?target=<butler> returns 404 when override row does not exist."""
    mock_db = _make_db(butler_row=None, butler_names=["health"])
    client = _build_app(mock_db)
    resp = client.delete("/api/secrets/system/NO_SUCH_KEY?target=health")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/system/<key>/probe  — probe ok
# ---------------------------------------------------------------------------


def test_probe_returns_200_with_test_result():
    """Probe returns 200 with ApiResponse<TestResult> envelope; ok=True when the
    credential's last_test_ok=True."""
    existing_row = _make_butler_secrets_row(
        secret_key="PROBE_KEY", last_test_ok=True, secret_value="val"
    )
    mock_db = _make_db(switchboard_row=existing_row)
    # Clear rate-limit state before test.
    _system_probe_timestamps.pop("PROBE_KEY", None)
    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/PROBE_KEY/probe")
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    assert "at" in body["data"]
    assert body["data"]["ok"] is True


def test_probe_fail_credential_returns_false():
    """Probe returns ok=False when credential last_test_ok=False."""
    existing_row = _make_butler_secrets_row(
        secret_key="FAIL_KEY",
        last_test_ok=False,
        secret_value="val",
        last_test_message="Connection refused",
    )
    mock_db = _make_db(switchboard_row=existing_row)
    _system_probe_timestamps.pop("FAIL_KEY", None)
    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/FAIL_KEY/probe")
    assert resp.status_code == 200
    assert resp.json()["data"]["ok"] is False


def test_probe_verified_action_when_ok(monkeypatch):
    """Probe writes 'verified' audit action when credential is ok."""
    existing_row = _make_butler_secrets_row(
        secret_key="VFY_KEY", last_test_ok=True, secret_value="val"
    )
    mock_db = _make_db(switchboard_row=existing_row)
    _system_probe_timestamps.pop("VFY_KEY", None)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    client.post("/api/secrets/system/VFY_KEY/probe")
    assert any(c["action"] == "verified" for c in audit_calls)


def test_probe_failed_action_when_not_ok(monkeypatch):
    """Probe writes 'failed' audit action when credential is in failing state."""
    existing_row = _make_butler_secrets_row(
        secret_key="FAIL2_KEY",
        last_test_ok=False,
        secret_value="val",
        last_test_message="Token expired",
    )
    mock_db = _make_db(switchboard_row=existing_row)
    _system_probe_timestamps.pop("FAIL2_KEY", None)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    client.post("/api/secrets/system/FAIL2_KEY/probe")
    assert any(c["action"] == "failed" for c in audit_calls)


def test_probe_writes_probe_log_insert():
    """Probe inserts one row into public.secret_probe_log."""
    existing_row = _make_butler_secrets_row(
        secret_key="PL_KEY", last_test_ok=True, secret_value="val"
    )
    mock_db = _make_db(switchboard_row=existing_row)
    _system_probe_timestamps.pop("PL_KEY", None)

    shared_pool = mock_db.credential_shared_pool()
    probe_log_inserts: list[str] = []

    @asynccontextmanager
    async def _acquire_tracking():
        conn = AsyncMock()
        conn.fetchrow = shared_pool.fetchrow
        conn.fetch = shared_pool.fetch
        conn.fetchval = AsyncMock(return_value=1)

        @asynccontextmanager
        async def _transaction():
            yield

        conn.transaction = _transaction

        async def _conn_execute(sql, *args, **kwargs):
            if "secret_probe_log" in sql:
                probe_log_inserts.append(sql)
            return "OK"

        conn.execute = _conn_execute
        yield conn

    shared_pool.acquire = _acquire_tracking

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/PL_KEY/probe")
    assert resp.status_code == 200
    assert probe_log_inserts, "Expected INSERT into public.secret_probe_log"


def test_probe_404_on_unknown_key():
    """Probe returns 404 when no credential exists for the given key."""
    # Empty switchboard (no existing row) — credential_shared_pool available.
    mock_db = _make_db(switchboard_row=None)
    _system_probe_timestamps.pop("MISSING_KEY", None)
    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/MISSING_KEY/probe")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: probe rate-limit enforcement
# ---------------------------------------------------------------------------


def test_probe_rate_limit_429_on_second_call():
    """Probe rate-limit returns 429 when the same key is probed twice within TTL."""
    existing_row = _make_butler_secrets_row(
        secret_key="RL_KEY", last_test_ok=True, secret_value="val"
    )
    mock_db = _make_db(switchboard_row=existing_row)
    # Ensure rate-limit state is fresh.
    _system_probe_timestamps.pop("RL_KEY", None)
    client = _build_app(mock_db)

    # First call should succeed.
    resp1 = client.post("/api/secrets/system/RL_KEY/probe")
    assert resp1.status_code == 200

    # Second call immediately after should be rate-limited.
    resp2 = client.post("/api/secrets/system/RL_KEY/probe")
    assert resp2.status_code == 429


def test_probe_rate_limit_different_keys_not_affected():
    """Probe rate-limit on KEY_A does not block probes of KEY_B."""
    row_a = _make_butler_secrets_row(secret_key="KEY_A", last_test_ok=True, secret_value="val")
    row_b = _make_butler_secrets_row(secret_key="KEY_B", last_test_ok=True, secret_value="val")
    mock_db = _make_db(switchboard_row=row_a)
    _system_probe_timestamps.pop("KEY_A", None)
    _system_probe_timestamps.pop("KEY_B", None)
    client = _build_app(mock_db)

    resp_a = client.post("/api/secrets/system/KEY_A/probe")
    assert resp_a.status_code == 200

    # KEY_B uses a different pool row — need its own mock_db.
    mock_db_b = _make_db(switchboard_row=row_b)
    client_b = _build_app(mock_db_b)
    resp_b = client_b.post("/api/secrets/system/KEY_B/probe")
    assert resp_b.status_code == 200


# ---------------------------------------------------------------------------
# Helpers: shared-public pool fixture
# ---------------------------------------------------------------------------


def _make_db_with_shared_public(
    *,
    public_row: MagicMock | None = None,
    execute_ok: bool = True,
) -> MagicMock:
    """Build a mock DatabaseManager where the key lives ONLY in the public pool.

    butler_names is empty (no per-butler schema), switchboard is not registered.
    credential_shared_pool() returns a pool seeded with public_row.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []

    mock_db.pool = MagicMock(side_effect=KeyError("no butler pools"))

    public_pool = _make_butler_pool(existing_row=public_row, execute_ok=execute_ok)
    mock_db.credential_shared_pool = MagicMock(return_value=public_pool)

    return mock_db


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/system/<key>  target="shared-public"
# — verify writes land in public pool, NOT switchboard
# ---------------------------------------------------------------------------


def test_set_shared_public_insert_goes_to_public_pool_not_switchboard():
    """POST target=shared-public → 200 with SystemSecretDetail envelope; INSERT executed
    on the public pool, switchboard untouched."""
    new_row = _make_butler_secrets_row(secret_key="PUB_KEY", last_test_ok=True)
    mock_db = _make_db_with_shared_public(public_row=None)
    public_pool = mock_db.credential_shared_pool()

    call_count = [0]

    async def _fetchrow_se(sql, *args):
        if "secret_probe_log" in sql:
            return None
        call_count[0] += 1
        return None if call_count[0] == 1 else new_row

    public_pool.fetchrow = AsyncMock(side_effect=_fetchrow_se)

    client = _build_app(mock_db)
    resp = client.post(
        "/api/secrets/system/PUB_KEY",
        json={"value": "pub-val", "target": "shared-public"},
    )
    assert resp.status_code == 200
    assert "meta" in resp.json()

    # The public pool must have had execute() called (INSERT or UPDATE).
    assert public_pool.execute.called, "Expected execute() on the public pool"

    # The switchboard pool must NOT have been touched (it was never instantiated
    # via db.pool("switchboard") — confirm db.pool was not called).
    mock_db.pool.assert_not_called()


def test_set_shared_public_rotate_existing_writes_rotated_audit(monkeypatch):
    """POST target=shared-public on existing row → audit action is 'rotated'."""
    existing_row = _make_butler_secrets_row(secret_key="PUB_ROTATE", last_test_ok=True)
    mock_db = _make_db_with_shared_public(public_row=existing_row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    resp = client.post(
        "/api/secrets/system/PUB_ROTATE",
        json={"value": "new-val", "target": "shared-public"},
    )
    assert resp.status_code == 200
    assert any(c["action"] == "rotated" for c in audit_calls), (
        f"Expected 'rotated' audit action; got: {audit_calls}"
    )


def test_set_shared_public_new_writes_set_audit(monkeypatch):
    """POST target=shared-public on missing row → audit action is 'set'."""
    new_row = _make_butler_secrets_row(secret_key="PUB_SET")
    mock_db = _make_db_with_shared_public(public_row=None)
    public_pool = mock_db.credential_shared_pool()

    call_count = [0]

    async def _fetchrow_se(sql, *args):
        if "secret_probe_log" in sql:
            return None
        call_count[0] += 1
        return None if call_count[0] == 1 else new_row

    public_pool.fetchrow = AsyncMock(side_effect=_fetchrow_se)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    resp = client.post(
        "/api/secrets/system/PUB_SET",
        json={"value": "first-val", "target": "shared-public"},
    )
    assert resp.status_code == 200
    assert any(c["action"] == "set" for c in audit_calls), (
        f"Expected 'set' audit action; got: {audit_calls}"
    )


def test_set_shared_public_503_when_pool_unavailable():
    """POST target=shared-public returns 503 when the shared pool is not configured."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.pool = MagicMock(side_effect=KeyError("no pools"))
    mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no shared pool"))

    client = _build_app(mock_db)
    resp = client.post(
        "/api/secrets/system/PUB_KEY",
        json={"value": "val", "target": "shared-public"},
    )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: shared target still routes to switchboard (backwards-compat)
# ---------------------------------------------------------------------------


def test_set_shared_target_still_uses_switchboard():
    """POST target=shared (default) → writes to switchboard pool, NOT the public pool."""
    existing_row = _make_butler_secrets_row(secret_key="SW_KEY", last_test_ok=True)
    mock_db = _make_db(switchboard_row=existing_row)
    switchboard_pool = mock_db.pool("switchboard")

    client = _build_app(mock_db)
    resp = client.post(
        "/api/secrets/system/SW_KEY",
        json={"value": "sw-val", "target": "shared"},
    )
    assert resp.status_code == 200
    # switchboard pool must have had execute() called.
    assert switchboard_pool.execute.called, "Expected execute() on the switchboard pool"


# ---------------------------------------------------------------------------
# Tests: DELETE /api/secrets/system/<key>?target=shared-public
# ---------------------------------------------------------------------------


def test_delete_shared_public_goes_to_public_pool_not_switchboard():
    """DELETE ?target=shared-public → 200 with {status: 'disconnected'}; DELETE executed
    on the public pool, switchboard untouched."""
    existing_row = _make_butler_secrets_row(secret_key="PUB_DEL2")
    mock_db = _make_db_with_shared_public(public_row=existing_row)
    public_pool = mock_db.credential_shared_pool()

    client = _build_app(mock_db)
    resp = client.delete("/api/secrets/system/PUB_DEL2?target=shared-public")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"

    # Public pool execute() must have been called (DELETE).
    assert public_pool.execute.called, "Expected execute() on the public pool"
    # switchboard pool must NOT have been accessed.
    mock_db.pool.assert_not_called()


def test_delete_shared_public_404_when_key_missing():
    """DELETE ?target=shared-public → 404 when key does not exist in public pool."""
    mock_db = _make_db_with_shared_public(public_row=None)
    client = _build_app(mock_db)
    resp = client.delete("/api/secrets/system/NO_PUB_KEY?target=shared-public")
    assert resp.status_code == 404


def test_delete_shared_public_writes_disconnected_audit(monkeypatch):
    """DELETE ?target=shared-public → audit action is 'disconnected'."""
    existing_row = _make_butler_secrets_row(secret_key="PUB_AUDIT_DEL")
    mock_db = _make_db_with_shared_public(public_row=existing_row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    resp = client.delete("/api/secrets/system/PUB_AUDIT_DEL?target=shared-public")
    assert resp.status_code == 200
    assert any(c["action"] == "disconnected" for c in audit_calls), (
        f"Expected 'disconnected' audit action; got: {audit_calls}"
    )


# ---------------------------------------------------------------------------
# Tests: probe finds credentials in the shared-public pool
# ---------------------------------------------------------------------------


def test_probe_finds_credential_in_public_pool():
    """Probe locates a key in the public pool when no per-butler schema has it."""
    existing_row = _make_butler_secrets_row(
        secret_key="PUB_PROBE", last_test_ok=True, secret_value="val"
    )
    mock_db = _make_db_with_shared_public(public_row=existing_row)
    _system_probe_timestamps.pop("PUB_PROBE", None)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/PUB_PROBE/probe")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["data"]["ok"] is True


def test_probe_shared_public_writes_to_public_pool_not_switchboard():
    """Probe on a public-pool key must not call db.pool() (no switchboard lookup)."""
    existing_row = _make_butler_secrets_row(
        secret_key="PUB_PROBE2", last_test_ok=True, secret_value="val"
    )
    mock_db = _make_db_with_shared_public(public_row=existing_row)
    _system_probe_timestamps.pop("PUB_PROBE2", None)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/system/PUB_PROBE2/probe")
    assert resp.status_code == 200

    # db.pool() must not have been called — the key was found via the public pool.
    mock_db.pool.assert_not_called()

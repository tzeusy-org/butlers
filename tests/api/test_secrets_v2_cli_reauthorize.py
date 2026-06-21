"""Tests for POST /api/secrets/cli/<id>/reauthorize endpoint.

Covers bu-ayp6v.10: Bridge endpoint that initiates (or resumes) re-authentication
for a CLI runtime provider via the existing cli-auth subsystem.

Test matrix
-----------
device_code branch:
  - 200 with device_code envelope when provider is device-code mode and binary available
  - Response contains auth_mode='device_code', provider, session_id, session_state
  - 503 when device-code binary is not on PATH
  - Writes 'attempted' audit row with canonical key 'c:<id>'

api_key branch:
  - 200 with api_key envelope when provider is api_key mode
  - Response contains auth_mode='api_key', provider, env_var, prompt
  - Writes 'attempted' audit row with canonical key 'c:<id>'

shared:
  - 404 on unknown provider id
  - Envelope conformance: data + meta on all 200 responses
  - No shared_pool → audit skipped but response still returned (no 503)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shared_pool(*, execute_ok: bool = True) -> AsyncMock:
    """Build a mock shared-pool sufficient for the reauthorize endpoint."""
    shared_pool = AsyncMock()
    if execute_ok:
        shared_pool.execute = AsyncMock(return_value="INSERT 1")
    else:
        shared_pool.execute = AsyncMock(side_effect=Exception("DB error"))
    shared_pool.fetch = AsyncMock(return_value=[])
    shared_pool.fetchrow = AsyncMock(return_value=None)

    fake_conn = AsyncMock()
    fake_conn.execute = shared_pool.execute
    fake_conn.fetch = shared_pool.fetch
    fake_conn.fetchrow = shared_pool.fetchrow

    @asynccontextmanager
    async def _transaction():
        yield

    fake_conn.transaction = _transaction

    @asynccontextmanager
    async def _acquire():
        yield fake_conn

    shared_pool.acquire = _acquire
    return shared_pool


def _make_db(*, shared_pool_available: bool = True, execute_ok: bool = True) -> MagicMock:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.pool = MagicMock(side_effect=KeyError("no butler pool"))

    if shared_pool_available:
        shared_pool = _make_shared_pool(execute_ok=execute_ok)
        mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    else:
        mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no shared pool"))

    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests: 404 on unknown provider id
# ---------------------------------------------------------------------------


def test_reauthorize_cli_404_on_unknown_id():
    """Returns 404 when the credential_id does not map to any known provider."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/totally-unknown-provider/reauthorize")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: api_key branch (claude provider — auth_mode='api_key')
# ---------------------------------------------------------------------------


def test_reauthorize_cli_apikey_returns_200_envelope():
    """api_key provider: 200 with {data, meta}; auth_mode/provider/env_var/prompt set,
    session_id None (no device-code session started)."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    data = body["data"]
    assert data["auth_mode"] == "api_key"
    assert data["provider"] == "claude"
    assert data.get("env_var")
    assert data.get("prompt")
    assert data.get("session_id") is None


def test_reauthorize_cli_apikey_writes_attempted_audit_canonical_target(monkeypatch):
    """api_key provider: writes an 'attempted' audit row targeting canonical 'c:<id>'."""
    mock_db = _make_db()
    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200

    attempted = [c for c in audit_calls if c["action"] == "attempted"]
    assert attempted, f"No 'attempted' audit row found; got: {audit_calls}"
    assert attempted[0].get("target") == "c:claude"


def test_reauthorize_cli_apikey_no_shared_pool_still_returns_200():
    """api_key provider: returns 200 even when shared pool is unavailable (audit skipped)."""
    mock_db = _make_db(shared_pool_available=False)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200
    assert resp.json()["data"]["auth_mode"] == "api_key"


# ---------------------------------------------------------------------------
# Tests: device_code branch (codex provider — auth_mode='device_code')
# ---------------------------------------------------------------------------


def test_reauthorize_cli_devicecode_returns_200_envelope_and_session(monkeypatch):
    """device_code provider: 200 with {data, meta}; auth_mode='device_code',
    session_id non-empty, and an 'attempted' audit row targeting canonical 'c:<id>'."""
    mock_db = _make_db()
    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    with (
        patch("butlers.api.routers.secrets_v2.PROVIDERS") as mock_providers,
        patch("butlers.api.routers.secrets_v2.CLIAuthSession") as mock_session_cls,
        patch("butlers.api.routers.secrets_v2.store_session"),
        patch("butlers.api.routers.cli_auth._build_on_success", return_value=None),
    ):
        fake_provider = MagicMock()
        fake_provider.name = "codex"
        fake_provider.auth_mode = "device_code"
        fake_provider.binary.return_value = "codex"
        fake_provider.is_available.return_value = True
        mock_providers.get.return_value = fake_provider

        session_instance = AsyncMock()
        session_instance.id = "sess-001"
        session_instance.state = "awaiting_auth"
        session_instance.auth_url = "https://auth.openai.com/codex/device"
        session_instance.device_code = "ABCD-1234"
        session_instance.message = "Waiting for authorization."
        mock_session_cls.return_value = session_instance

        resp = client.post("/api/secrets/cli/codex/reauthorize")
        assert resp.status_code == 200
        body = resp.json()
        assert "meta" in body
        data = body["data"]
        assert data["auth_mode"] == "device_code"
        assert data["session_id"], "Expected non-empty session_id for device_code provider"

    attempted = [c for c in audit_calls if c["action"] == "attempted"]
    assert attempted, f"No 'attempted' audit row found; got: {audit_calls}"
    assert attempted[0].get("target") == "c:codex"


def test_reauthorize_cli_devicecode_503_when_binary_not_available():
    """device_code provider: returns 503 when the CLI binary is not on PATH."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    with patch("butlers.api.routers.secrets_v2.PROVIDERS") as mock_providers:
        fake_provider = MagicMock()
        fake_provider.name = "codex"
        fake_provider.auth_mode = "device_code"
        fake_provider.binary.return_value = "codex"
        fake_provider.is_available.return_value = False
        mock_providers.get.return_value = fake_provider

        resp = client.post("/api/secrets/cli/codex/reauthorize")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: full credential id (cli-auth/<provider>) — regression for bu-afzzq
#
# CLI runtime tokens are persisted under the key convention
# `cli-auth/<provider>` (butlers.cli_auth.persistence), so the secrets
# inventory exposes them with id `cli-auth/codex`. The frontend posts that
# full id, URL-encoded, to the reauthorize endpoint
# (`/api/secrets/cli/cli-auth%2Fcodex/reauthorize`). The slash must survive
# route matching AND the handler must resolve the bare provider name against
# PROVIDERS (which is keyed on the bare name).
# ---------------------------------------------------------------------------


def test_reauthorize_cli_apikey_full_credential_id_encoded_slash():
    """The frontend's exact payload (encoded slash) reaches the handler, not 404."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    # %2F is the encoded slash the frontend's encodeURIComponent produces.
    resp = client.post("/api/secrets/cli/cli-auth%2Fclaude/reauthorize")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["auth_mode"] == "api_key"


def test_reauthorize_cli_apikey_full_credential_id_literal_slash():
    """A literal slash in the credential id is captured by the route param."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/cli-auth/claude/reauthorize")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["provider"] == "claude"


def test_reauthorize_cli_full_credential_id_audit_keeps_full_id(monkeypatch):
    """Audit rows retain the full `cli-auth/<provider>` id, not the bare name."""
    captured: dict[str, object] = {}

    async def _fake_write_cli_audit(_pool, *, action, credential_id, note):
        captured["action"] = action
        captured["credential_id"] = credential_id

    monkeypatch.setattr("butlers.api.routers.secrets_v2._write_cli_audit", _fake_write_cli_audit)

    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/cli-auth%2Fclaude/reauthorize")
    assert resp.status_code == 200, resp.text
    assert captured.get("credential_id") == "cli-auth/claude"
    assert captured.get("action") == "attempted"


def test_reauthorize_cli_404_encoded_slash_unknown_provider():
    """An unknown encoded-slash id is captured by the route and 404s at the handler."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/cli-auth%2Fnope/reauthorize")
    assert resp.status_code == 404

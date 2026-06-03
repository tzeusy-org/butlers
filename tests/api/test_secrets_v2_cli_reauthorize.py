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
from datetime import UTC, datetime
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

_NOW = datetime.now(tz=UTC)


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
# Minimal CLIAuthSession stub for device-code tests
# ---------------------------------------------------------------------------


class _FakeSession:
    """Synchronous-friendly stub for CLIAuthSession used in device-code tests."""

    def __init__(self, session_id: str, provider):
        self.id = session_id
        self.provider = provider
        self.state = "awaiting_auth"
        self.auth_url = "https://auth.openai.com/codex/device"
        self.device_code = "ABCD-1234"
        self.message = "Waiting for authorization."

    async def start(self) -> None:
        pass

    async def wait(self, timeout: float = 10.0) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests: 404 on unknown provider id
# ---------------------------------------------------------------------------


def test_reauthorize_cli_404_on_unknown_id():
    """Returns 404 when the credential_id does not map to any known provider."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/totally-unknown-provider/reauthorize")
    assert resp.status_code == 404


def test_reauthorize_cli_404_detail_mentions_unknown_provider():
    """404 detail message mentions 'provider'."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/no-such-thing/reauthorize")
    assert resp.status_code == 404
    assert "provider" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Tests: api_key branch (claude provider — auth_mode='api_key')
# ---------------------------------------------------------------------------


def test_reauthorize_cli_apikey_returns_200():
    """api_key provider: returns HTTP 200."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200


def test_reauthorize_cli_apikey_auth_mode_field():
    """api_key provider: response data.auth_mode == 'api_key'."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["auth_mode"] == "api_key"


def test_reauthorize_cli_apikey_provider_field():
    """api_key provider: response data.provider matches requested id."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["provider"] == "claude"


def test_reauthorize_cli_apikey_env_var_present():
    """api_key provider: response data.env_var is non-empty."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data.get("env_var"), "Expected non-empty env_var for api_key provider"


def test_reauthorize_cli_apikey_prompt_present():
    """api_key provider: response data.prompt is non-empty."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data.get("prompt"), "Expected non-empty prompt for api_key provider"


def test_reauthorize_cli_apikey_no_session_id():
    """api_key provider: session_id is None (no device-code session started)."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data.get("session_id") is None


def test_reauthorize_cli_apikey_writes_attempted_audit(monkeypatch):
    """api_key provider: writes an 'attempted' audit row."""
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

    assert any(c["action"] == "attempted" for c in audit_calls), (
        f"Expected 'attempted' audit action; got: {audit_calls}"
    )


def test_reauthorize_cli_apikey_audit_target_canonical(monkeypatch):
    """api_key provider: audit target is canonical 'c:<id>'."""
    mock_db = _make_db()
    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)
    client.post("/api/secrets/cli/claude/reauthorize")

    attempted = [c for c in audit_calls if c["action"] == "attempted"]
    assert attempted, "No 'attempted' audit row found"
    assert attempted[0].get("target") == "c:claude", (
        f"Expected canonical target 'c:claude'; got: {attempted[0].get('target')!r}"
    )


def test_reauthorize_cli_apikey_envelope_conformance():
    """api_key provider: response has {data, meta} envelope shape."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/cli/claude/reauthorize")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body, "Expected 'data' key in response envelope"
    assert "meta" in body, "Expected 'meta' key in response envelope"


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


def test_reauthorize_cli_devicecode_returns_200():
    """device_code provider: returns HTTP 200 when binary is available."""
    mock_db = _make_db()
    client = _build_app(mock_db)

    fake_session = None

    def _fake_store_session(session):
        nonlocal fake_session
        fake_session = _FakeSession(session.id, session.provider)
        # Replace the real session with the fake one in the same store
        from butlers.cli_auth.session import _sessions

        _sessions[session.id] = fake_session

    with (
        patch("butlers.api.routers.secrets_v2.PROVIDERS") as mock_providers,
        patch("butlers.api.routers.secrets_v2.CLIAuthSession") as mock_session_cls,
        patch("butlers.api.routers.secrets_v2.store_session", side_effect=_fake_store_session),
        patch("butlers.api.routers.cli_auth._build_on_success", return_value=None),
    ):
        # Build a fake device_code provider definition
        fake_provider = MagicMock()
        fake_provider.name = "codex"
        fake_provider.display_name = "Codex (OpenAI)"
        fake_provider.auth_mode = "device_code"
        fake_provider.binary.return_value = "codex"
        fake_provider.is_available.return_value = True
        mock_providers.get.return_value = fake_provider

        session_instance = AsyncMock()
        session_instance.id = "test-session-id"
        session_instance.state = "awaiting_auth"
        session_instance.auth_url = "https://auth.openai.com/codex/device"
        session_instance.device_code = "ABCD-1234"
        session_instance.message = "Waiting for authorization."
        session_instance.start = AsyncMock()
        session_instance.wait = AsyncMock()
        mock_session_cls.return_value = session_instance

        resp = client.post("/api/secrets/cli/codex/reauthorize")
        assert resp.status_code == 200


def test_reauthorize_cli_devicecode_auth_mode_field():
    """device_code provider: response data.auth_mode == 'device_code'."""
    mock_db = _make_db()
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
        data = resp.json()["data"]
        assert data["auth_mode"] == "device_code"


def test_reauthorize_cli_devicecode_session_id_present():
    """device_code provider: response data.session_id is non-empty."""
    mock_db = _make_db()
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
        session_instance.id = "sess-abc"
        session_instance.state = "awaiting_auth"
        session_instance.auth_url = "https://auth.openai.com/"
        session_instance.device_code = "XY-99"
        session_instance.message = None
        mock_session_cls.return_value = session_instance

        resp = client.post("/api/secrets/cli/codex/reauthorize")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["session_id"], "Expected non-empty session_id for device_code provider"


def test_reauthorize_cli_devicecode_writes_attempted_audit(monkeypatch):
    """device_code provider: writes an 'attempted' audit row."""
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
        session_instance.id = "sess-x"
        session_instance.state = "awaiting_auth"
        session_instance.auth_url = "https://auth.openai.com/"
        session_instance.device_code = "ZZ-00"
        session_instance.message = None
        mock_session_cls.return_value = session_instance

        resp = client.post("/api/secrets/cli/codex/reauthorize")
        assert resp.status_code == 200

    assert any(c["action"] == "attempted" for c in audit_calls), (
        f"Expected 'attempted' audit action; got: {audit_calls}"
    )


def test_reauthorize_cli_devicecode_audit_target_canonical(monkeypatch):
    """device_code provider: audit target is canonical 'c:<id>'."""
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
        session_instance.id = "sess-y"
        session_instance.state = "awaiting_auth"
        session_instance.auth_url = "https://auth.openai.com/"
        session_instance.device_code = "AB-12"
        session_instance.message = None
        mock_session_cls.return_value = session_instance

        client.post("/api/secrets/cli/codex/reauthorize")

    attempted = [c for c in audit_calls if c["action"] == "attempted"]
    assert attempted, "No 'attempted' audit row found"
    assert attempted[0].get("target") == "c:codex", (
        f"Expected canonical target 'c:codex'; got: {attempted[0].get('target')!r}"
    )


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


def test_reauthorize_cli_devicecode_envelope_conformance():
    """device_code provider: response has {data, meta} envelope shape."""
    mock_db = _make_db()
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
        session_instance.id = "sess-z"
        session_instance.state = "awaiting_auth"
        session_instance.auth_url = "https://auth.openai.com/"
        session_instance.device_code = "XZ-55"
        session_instance.message = None
        mock_session_cls.return_value = session_instance

        resp = client.post("/api/secrets/cli/codex/reauthorize")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body, "Expected 'data' key in response envelope"
        assert "meta" in body, "Expected 'meta' key in response envelope"

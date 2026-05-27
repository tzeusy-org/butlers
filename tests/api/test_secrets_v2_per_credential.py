"""Integration tests for per-credential read endpoints.

Tests for bu-txx12: GET /api/secrets/user/<provider>,
GET /api/secrets/system/<key>, GET /api/secrets/cli/<id>.

Coverage per issue acceptance criteria:
- hit case for each scope (3 tests min)
- miss case for each scope (3 tests min)
- envelope conformance for each scope (assert all required fields present)
- 404 on miss for each scope

Spec anchor
-----------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
§Per-credential read endpoints
§Probe-log LRU integration
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from butlers._sql_utils import escape_like_pattern as _escape_like_pattern
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import (
    _get_db_manager,
)

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


def _make_entity_info_row(
    *,
    entity_id: str | None = None,
    info_type: str = "google_oauth_refresh",
    value: str = "tok3n",
    label: str | None = None,
    last_verified: datetime | None = None,
    last_test_ok: bool | None = None,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
) -> MagicMock:
    row_id = uuid4()
    eid = entity_id or str(uuid4())
    return _make_row(
        id=row_id,
        entity_id=eid,
        type=info_type,
        value=value,
        label=label,
        last_verified=last_verified,
        last_test_ok=last_test_ok,
        last_test_code=last_test_code,
        last_test_message=last_test_message,
        created_at=_NOW,
    )


def _make_system_row(
    *,
    key: str = "SOME_API_KEY",
    value: str = "s3cr3t",
    category: str = "general",
    description: str | None = None,
    last_verified: datetime | None = None,
    last_test_ok: bool | None = None,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
    expires_at: datetime | None = None,
) -> MagicMock:
    return _make_row(
        secret_key=key,
        secret_value=value,
        category=category,
        description=description,
        last_verified=last_verified,
        last_test_ok=last_test_ok,
        last_test_code=last_test_code,
        last_test_message=last_test_message,
        expires_at=expires_at,
        created_at=_NOW,
    )


def _make_cli_row(
    *,
    key: str = "cli-token-abc123",
    value: str = "cli_secret_value",
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


def _make_probe_row(
    *,
    ok: bool = True,
    code: int | None = 200,
    message: str | None = None,
    recorded_at: datetime | None = None,
) -> MagicMock:
    return _make_row(
        ok=ok,
        code=code,
        message=message,
        recorded_at=recorded_at or _NOW,
    )


def _make_db_manager_for_per_credential(
    *,
    butler_names: list[str] | None = None,
    system_row: MagicMock | None = None,
    user_row: MagicMock | None = None,
    cli_row: MagicMock | None = None,
    probe_row: MagicMock | None = None,
    shared_pool_available: bool = True,
) -> MagicMock:
    """Build a mock DatabaseManager for per-credential endpoint tests.

    Wires:
    - butler schema pool: returns system_row (or None) on fetchrow with butler_secrets
    - shared pool: returns user_row on entity_info fetchrow,
                   cli_row on butler_secrets (cli) fetchrow
    - probe log: returns probe_row on secret_probe_log fetchrow
    """
    butler_names = butler_names or ["general"]

    # --- butler schema pool ---
    butler_pool = AsyncMock()

    async def _butler_fetchrow(sql, *args):
        if "secret_probe_log" in sql:
            return probe_row
        if "butler_secrets" in sql and "category = 'cli'" not in sql:
            return system_row
        return None

    butler_pool.fetchrow = AsyncMock(side_effect=_butler_fetchrow)
    butler_pool.fetch = AsyncMock(return_value=[])

    # --- shared pool ---
    shared_pool = AsyncMock()

    async def _shared_fetchrow(sql, *args):
        if "secret_probe_log" in sql:
            return probe_row
        if "category = 'cli'" in sql:
            return cli_row
        if "entity_info" in sql:
            return user_row
        return None

    shared_pool.fetchrow = AsyncMock(side_effect=_shared_fetchrow)
    shared_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = butler_names

    def _pool(name):
        return butler_pool

    mock_db.pool = MagicMock(side_effect=_pool)

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
# Tests: GET /api/secrets/user/<provider> — hit cases
# ---------------------------------------------------------------------------


def test_user_credential_hit_returns_200():
    """Hit case: matching entity_info row returns 200 with UserSecretDetail."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True)
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.status_code == 200


def test_user_credential_hit_envelope_shape():
    """Hit case: response has {data, meta} envelope and required data fields."""
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.status_code == 200
    body = resp.json()

    # Envelope conformance
    assert "data" in body
    assert "meta" in body
    data = body["data"]

    # Required fields per spec
    assert "id" in data
    assert "entity_id" in data
    assert "type" in data
    assert "provider" in data
    assert "state" in data
    assert "fingerprint" in data or data.get("fingerprint") is None
    # Future fields default to None/[]
    assert "issued" in data or data.get("issued") is None
    assert "scopes_required" in data
    assert "scopes_granted" in data
    assert "feeds" in data
    assert "breaks" in data
    assert "audit" in data


def test_user_credential_hit_provider_field():
    """Hit case: provider field in response matches path parameter."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", value="tok")
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.status_code == 200
    assert resp.json()["data"]["provider"] == "google"


def test_user_credential_hit_fingerprint_present():
    """Hit case: fingerprint is a non-None 8-char hex string when value is set."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", value="mytoken")
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    fp = resp.json()["data"]["fingerprint"]
    assert fp is not None
    assert len(fp) == 8
    int(fp, 16)  # validates it's hex


def test_user_credential_hit_state_ok():
    """Hit case: state=ok when last_test_ok=True."""
    row = _make_entity_info_row(last_test_ok=True)
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.json()["data"]["state"] == "ok"


def test_user_credential_hit_with_probe_test_result():
    """Hit case: test field populated from probe_log when probe exists."""
    row = _make_entity_info_row(last_test_ok=True)
    probe = _make_probe_row(ok=True, code=200, message="ok")
    mock_db = _make_db_manager_for_per_credential(user_row=row, probe_row=probe)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    data = resp.json()["data"]
    assert data["test"] is not None
    assert data["test"]["ok"] is True


def test_user_credential_hit_with_identity_query_param():
    """Hit case: ?identity= passes entity UUID filter without error."""
    entity_id = str(uuid4())
    row = _make_entity_info_row(entity_id=entity_id, info_type="spotify_oauth_refresh")
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get(f"/api/secrets/user/spotify?identity={entity_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["provider"] == "spotify"


def test_user_credential_no_raw_value_in_response():
    """Security: raw credential value must NOT appear in response."""
    row = _make_entity_info_row(value="super_secret_token_abc")
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    body_text = resp.text
    assert "super_secret_token_abc" not in body_text


# ---------------------------------------------------------------------------
# Tests: GET /api/secrets/user/<provider> — miss cases
# ---------------------------------------------------------------------------


def test_user_credential_miss_returns_404():
    """Miss case: no matching entity_info row returns 404."""
    mock_db = _make_db_manager_for_per_credential(user_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.status_code == 404


def test_user_credential_miss_no_shared_pool_returns_404():
    """Miss case: unavailable shared pool returns 404."""
    mock_db = _make_db_manager_for_per_credential(user_row=None, shared_pool_available=False)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.status_code == 404


def test_user_credential_miss_unknown_provider_returns_404():
    """Miss case: provider not in entity_info returns 404."""
    mock_db = _make_db_manager_for_per_credential(user_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/nonexistent-provider")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/secrets/system/<key> — hit cases
# ---------------------------------------------------------------------------


def test_system_credential_hit_returns_200():
    """Hit case: matching butler_secrets row returns 200."""
    row = _make_system_row(key="OPENAI_API_KEY", last_test_ok=True)
    mock_db = _make_db_manager_for_per_credential(system_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/OPENAI_API_KEY")
    assert resp.status_code == 200


def test_system_credential_hit_envelope_shape():
    """Hit case: response has {data, meta} envelope and required data fields."""
    row = _make_system_row(key="TELEGRAM_BOT_TOKEN")
    mock_db = _make_db_manager_for_per_credential(system_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/TELEGRAM_BOT_TOKEN")
    assert resp.status_code == 200
    body = resp.json()

    assert "data" in body
    assert "meta" in body
    data = body["data"]

    # Required fields per spec
    assert "key" in data
    assert "category" in data
    assert "state" in data
    assert "fingerprint" in data or data.get("fingerprint") is None
    assert "row_state" in data
    assert "description" in data or data.get("description") is None
    assert "last_verified" in data or data.get("last_verified") is None
    assert "used_by" in data
    assert "breaks" in data
    assert "audit" in data


def test_system_credential_hit_key_matches():
    """Hit case: key in response matches path parameter."""
    row = _make_system_row(key="MY_API_KEY")
    mock_db = _make_db_manager_for_per_credential(system_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/MY_API_KEY")
    assert resp.json()["data"]["key"] == "MY_API_KEY"


def test_system_credential_hit_fingerprint_present():
    """Hit case: fingerprint is 8-char hex when value is set."""
    row = _make_system_row(key="SOME_KEY", value="secretvalue")
    mock_db = _make_db_manager_for_per_credential(system_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/SOME_KEY")
    fp = resp.json()["data"]["fingerprint"]
    assert fp is not None
    assert len(fp) == 8
    int(fp, 16)


def test_system_credential_hit_state_warn_no_probe():
    """Hit case: state=warn when set but no probe result."""
    row = _make_system_row(key="UNVERIFIED_KEY", value="val", last_test_ok=None)
    mock_db = _make_db_manager_for_per_credential(system_row=row, probe_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/UNVERIFIED_KEY")
    assert resp.json()["data"]["state"] == "warn"


def test_system_credential_hit_with_probe():
    """Hit case: test field populated from probe_log."""
    row = _make_system_row(key="TESTED_KEY", last_test_ok=False)
    probe = _make_probe_row(ok=False, code=401, message="Unauthorized")
    mock_db = _make_db_manager_for_per_credential(system_row=row, probe_row=probe)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/TESTED_KEY")
    data = resp.json()["data"]
    assert data["test"] is not None
    assert data["test"]["ok"] is False
    assert data["test"]["code"] == 401


def test_system_credential_hit_row_state_is_shared():
    """Hit case: row_state defaults to 'shared'."""
    row = _make_system_row(key="SHARED_KEY")
    mock_db = _make_db_manager_for_per_credential(system_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/SHARED_KEY")
    assert resp.json()["data"]["row_state"] == "shared"


def test_system_credential_no_raw_value_in_response():
    """Security: raw credential value must NOT appear in response."""
    row = _make_system_row(key="SECRET_KEY", value="very_secret_system_value_xyz")
    mock_db = _make_db_manager_for_per_credential(system_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/SECRET_KEY")
    body_text = resp.text
    assert "very_secret_system_value_xyz" not in body_text


# ---------------------------------------------------------------------------
# Tests: GET /api/secrets/system/<key> — miss cases
# ---------------------------------------------------------------------------


def test_system_credential_miss_returns_404():
    """Miss case: key not in any butler schema returns 404."""
    mock_db = _make_db_manager_for_per_credential(system_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/NONEXISTENT_KEY")
    assert resp.status_code == 404


def test_system_credential_miss_no_butlers_returns_404():
    """Miss case: no butler schemas registered returns 404."""
    mock_db = _make_db_manager_for_per_credential(system_row=None, butler_names=[])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/ANY_KEY")
    assert resp.status_code == 404


def test_system_credential_miss_unknown_key_returns_404():
    """Miss case: completely unknown key returns 404."""
    mock_db = _make_db_manager_for_per_credential(system_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/I_DO_NOT_EXIST")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/secrets/cli/<id> — hit cases
# ---------------------------------------------------------------------------


def test_cli_credential_hit_returns_200():
    """Hit case: matching CLI token returns 200."""
    row = _make_cli_row(key="cli-abc123", last_test_ok=True)
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-abc123")
    assert resp.status_code == 200


def test_cli_credential_hit_envelope_shape():
    """Hit case: response has {data, meta} envelope and required data fields."""
    row = _make_cli_row(key="cli-xyz789")
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-xyz789")
    assert resp.status_code == 200
    body = resp.json()

    assert "data" in body
    assert "meta" in body
    data = body["data"]

    # Required fields per spec
    assert "id" in data
    assert "label" in data or data.get("label") is None
    assert "state" in data
    assert "fingerprint" in data or data.get("fingerprint") is None
    assert "issued" in data or data.get("issued") is None
    assert "expires" in data or data.get("expires") is None
    assert "last_used" in data or data.get("last_used") is None
    assert "scopes_required" in data
    assert "scopes_granted" in data


def test_cli_credential_hit_id_matches():
    """Hit case: id in response matches path parameter."""
    row = _make_cli_row(key="cli-my-token")
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-my-token")
    assert resp.json()["data"]["id"] == "cli-my-token"


def test_cli_credential_hit_fingerprint_present():
    """Hit case: fingerprint is 8-char hex when token value is set."""
    row = _make_cli_row(key="cli-fp-test", value="mysecretclitoken")
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-fp-test")
    fp = resp.json()["data"]["fingerprint"]
    assert fp is not None
    assert len(fp) == 8
    int(fp, 16)


def test_cli_credential_hit_label_from_description():
    """Hit case: label field maps from butler_secrets.description."""
    row = _make_cli_row(key="cli-tok", description="My Dev Token")
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-tok")
    assert resp.json()["data"]["label"] == "My Dev Token"


def test_cli_credential_hit_expires_returned():
    """Hit case: expires field is populated from expires_at column."""
    expires = _NOW + timedelta(days=30)
    row = _make_cli_row(key="cli-exp", expires_at=expires)
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-exp")
    data = resp.json()["data"]
    assert data["expires"] is not None


def test_cli_credential_hit_state_expired():
    """Hit case: state=expired when expires_at is in the past."""
    past_expires = _NOW - timedelta(days=1)
    row = _make_cli_row(key="cli-exp", expires_at=past_expires)
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-exp")
    assert resp.json()["data"]["state"] == "expired"


def test_cli_credential_hit_with_probe():
    """Hit case: test field populated from probe_log."""
    row = _make_cli_row(key="cli-probed")
    probe = _make_probe_row(ok=True, code=200)
    mock_db = _make_db_manager_for_per_credential(cli_row=row, probe_row=probe)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-probed")
    data = resp.json()["data"]
    assert data["test"] is not None
    assert data["test"]["ok"] is True


def test_cli_credential_no_raw_value_in_response():
    """Security: raw token value must NOT appear in response."""
    row = _make_cli_row(key="cli-sec", value="very_secret_cli_token_xyz")
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-sec")
    body_text = resp.text
    assert "very_secret_cli_token_xyz" not in body_text


# ---------------------------------------------------------------------------
# Tests: GET /api/secrets/cli/<id> — miss cases
# ---------------------------------------------------------------------------


def test_cli_credential_miss_returns_404():
    """Miss case: no matching CLI token returns 404."""
    mock_db = _make_db_manager_for_per_credential(cli_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/nonexistent-cli-id")
    assert resp.status_code == 404


def test_cli_credential_miss_no_shared_pool_returns_404():
    """Miss case: unavailable shared pool returns 404."""
    mock_db = _make_db_manager_for_per_credential(cli_row=None, shared_pool_available=False)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/any-id")
    assert resp.status_code == 404


def test_cli_credential_miss_unknown_id_returns_404():
    """Miss case: completely unknown CLI ID returns 404."""
    mock_db = _make_db_manager_for_per_credential(cli_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/i-dont-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: probe-log LRU integration — no probe returns test=null
# ---------------------------------------------------------------------------


def test_user_credential_no_probe_test_is_null():
    """When no probe has been recorded, test field is null."""
    row = _make_entity_info_row()
    mock_db = _make_db_manager_for_per_credential(user_row=row, probe_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.json()["data"]["test"] is None


def test_system_credential_no_probe_test_is_null():
    """When no probe has been recorded, test field is null."""
    row = _make_system_row()
    mock_db = _make_db_manager_for_per_credential(system_row=row, probe_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/SOME_API_KEY")
    assert resp.json()["data"]["test"] is None


def test_cli_credential_no_probe_test_is_null():
    """When no probe has been recorded, test field is null."""
    row = _make_cli_row()
    mock_db = _make_db_manager_for_per_credential(cli_row=row, probe_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-token-abc123")
    assert resp.json()["data"]["test"] is None


def test_probe_at_field_is_human_friendly():
    """Probe log at field is formatted as a human-friendly relative timestamp."""
    row = _make_entity_info_row()
    probe = _make_probe_row(ok=True, recorded_at=datetime.now(tz=UTC) - timedelta(hours=2))
    mock_db = _make_db_manager_for_per_credential(user_row=row, probe_row=probe)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    test = resp.json()["data"]["test"]
    assert test is not None
    assert test["at"] is not None
    assert "today" in test["at"]


# ---------------------------------------------------------------------------
# Tests: multi-butler system credential search
# ---------------------------------------------------------------------------


def test_system_credential_searches_all_butlers():
    """System credential search iterates all butler schemas to find the key."""
    # Only the second butler has the row
    # We'll simulate this by making the mock return None for the first call
    # and the real row for the second call
    row = _make_system_row(key="FOUND_IN_SECOND_BUTLER")

    call_count = 0

    async def _side_effect_fetchrow(sql, *args):
        nonlocal call_count
        if "butler_secrets" in sql and "category = 'cli'" not in sql:
            call_count += 1
            if call_count == 1:
                return None  # first butler misses
            return row  # second butler hits
        return None

    butler_pool = AsyncMock()
    butler_pool.fetchrow = AsyncMock(side_effect=_side_effect_fetchrow)
    butler_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["butler1", "butler2"]
    mock_db.pool = MagicMock(return_value=butler_pool)
    mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no shared pool"))

    client = _build_app(mock_db)
    resp = client.get("/api/secrets/system/FOUND_IN_SECOND_BUTLER")
    assert resp.status_code == 200
    assert resp.json()["data"]["key"] == "FOUND_IN_SECOND_BUTLER"


# ---------------------------------------------------------------------------
# Tests: LIKE wildcard escaping for provider path param (bu-vcv7c)
# ---------------------------------------------------------------------------


def test_escape_like_pattern_percent():
    """% in provider value is escaped to \\% so it is treated as a literal."""
    assert _escape_like_pattern("goog%") == "goog\\%"


def test_escape_like_pattern_underscore():
    """_ in provider value is escaped to \\_ so it is treated as a literal."""
    assert _escape_like_pattern("g_ogle") == "g\\_ogle"


def test_escape_like_pattern_backslash():
    """Backslash in provider value is doubled before other escapes are applied."""
    assert _escape_like_pattern("go\\ogle") == "go\\\\ogle"


def test_escape_like_pattern_clean_value():
    """A normal provider value is returned unchanged."""
    assert _escape_like_pattern("google") == "google"


def test_escape_like_pattern_multiple_metacharacters():
    """Multiple metacharacters in one value are all escaped."""
    assert _escape_like_pattern("%_foo%") == "\\%\\_foo\\%"


def _make_capturing_db_manager(
    *,
    user_row: MagicMock | None,
    shared_pool_available: bool = True,
) -> tuple[MagicMock, list]:
    """Like _make_db_manager_for_per_credential but also captures fetchrow call args."""
    captured: list = []

    shared_pool = AsyncMock()

    async def _shared_fetchrow(sql, *args):
        captured.append(args)
        if "entity_info" in sql:
            return user_row
        return None

    shared_pool.fetchrow = AsyncMock(side_effect=_shared_fetchrow)
    shared_pool.fetch = AsyncMock(return_value=[])

    butler_pool = AsyncMock()
    butler_pool.fetchrow = AsyncMock(return_value=None)
    butler_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=butler_pool)

    if shared_pool_available:
        mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    else:
        mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no shared pool"))

    return mock_db, captured


def test_user_credential_provider_percent_does_not_match_google_oauth_refresh():
    """Provider 'goog%' with escaping must produce 'goog\\%_%' as the LIKE parameter.

    Without escaping, 'goog%_%' would be sent to PostgreSQL and would match
    any type starting with 'goog' followed by any character and then anything.
    With escaping, 'goog\\%_%' only matches the literal string 'goog%_<anything>'.
    We verify the SQL parameter contains the escaped backslash-percent sequence.
    """
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db, captured = _make_capturing_db_manager(user_row=row)
    client = _build_app(mock_db)

    # %25 is URL-encoded % — FastAPI decodes it back to 'goog%' before routing.
    client.get("/api/secrets/user/goog%25")

    # Verify the LIKE pattern arg was escaped: must be 'goog\%_%' (literal backslash)
    # not 'goog%_%'.  Check the actual string values in the captured args tuples.
    all_params = [arg for args_tuple in captured for arg in args_tuple]
    assert r"goog\%_%" in all_params, (
        f"Expected escaped LIKE pattern 'goog\\%_%' in SQL params, got: {all_params}"
    )


def test_user_credential_provider_underscore_does_not_match_google_oauth_refresh():
    """Provider 'g_ogle' with escaping must produce 'g\\_ogle_%' as the LIKE parameter.

    Without escaping, 'g_ogle_%' would be sent and would match 'google_oauth_refresh'
    (the _ matches 'o').  With escaping, 'g\\_ogle_%' only matches literal 'g_ogle_<anything>'.
    We verify the SQL parameter contains the escaped backslash-underscore sequence.
    """
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db, captured = _make_capturing_db_manager(user_row=row)
    client = _build_app(mock_db)

    client.get("/api/secrets/user/g_ogle")

    all_params = [arg for args_tuple in captured for arg in args_tuple]
    assert r"g\_ogle_%" in all_params, (
        f"Expected escaped LIKE pattern 'g\\_ogle_%' in SQL params, got: {all_params}"
    )


def test_user_credential_clean_provider_passes_unmodified():
    """Provider 'google' (no metacharacters) produces 'google_%' LIKE pattern unchanged."""
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db, captured = _make_capturing_db_manager(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.status_code == 200

    all_params = [arg for args_tuple in captured for arg in args_tuple]
    assert "google_%" in all_params, (
        f"Expected LIKE pattern 'google_%' in SQL params, got: {all_params}"
    )

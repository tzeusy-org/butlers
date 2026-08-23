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

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from asyncpg.exceptions import UndefinedTableError
from fastapi.testclient import TestClient

from butlers._sql_utils import escape_like_pattern as _escape_like_pattern
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import (
    CliRuntimeDetail,
    SystemSecretDetail,
    _content_blind_cli_detail,
    _content_blind_system_detail,
    _get_db_manager,
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
    latency_ms: int | None = None,
) -> MagicMock:
    return _make_row(
        ok=ok,
        code=code,
        message=message,
        recorded_at=recorded_at or _NOW,
        latency_ms=latency_ms,
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
        if "butler_secrets" in sql and "category IN ('cli', 'cli-auth')" not in sql:
            return system_row
        return None

    butler_pool.fetchrow = AsyncMock(side_effect=_butler_fetchrow)
    butler_pool.fetch = AsyncMock(return_value=[])

    # --- shared pool ---
    shared_pool = AsyncMock()

    async def _shared_fetchrow(sql, *args):
        if "secret_probe_log" in sql:
            return probe_row
        if "category IN ('cli', 'cli-auth')" in sql:
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


def test_user_credential_hit_returns_200_envelope():
    """Hit case: 200 with {data, meta} envelope; required UserSecretDetail fields
    present, provider matches path, fingerprint is 8-char hex, state=ok."""
    row = _make_entity_info_row(
        info_type="google_oauth_refresh", value="mytoken", last_test_ok=True
    )
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    data = body["data"]

    # Required fields per spec
    for field in (
        "id",
        "entity_id",
        "provider",
        "state",
        "capabilities_required",
        "capabilities_granted",
        "capabilities",
        "audit",
    ):
        assert field in data, f"missing field {field!r}"

    # Content-bearing fields the content-blind contract removed.
    for field in ("type", "label", "failure_tail", "scopes_required", "scopes_granted"):
        assert field not in data, f"content-bearing field {field!r} must not be published"

    assert data["provider"] == "google"
    assert data["state"] == "ok"
    fp = data["fingerprint"]
    assert fp is not None and len(fp) == 8
    int(fp, 16)  # validates it's hex


def test_user_credential_stale_successful_probe_is_warn():
    """A stale successful user probe is an unknown, not a healthy verdict."""
    row = _make_entity_info_row(last_verified=_NOW - timedelta(days=2), last_test_ok=True)
    mock_db = _make_db_manager_for_per_credential(user_row=row)

    response = _build_app(mock_db).get("/api/secrets/user/google")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "warn"


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


def test_user_credential_detail_publishes_capability_categories_not_scopes():
    """Google scope evidence reaches the wire only as capability categories."""
    entity_id = str(uuid4())
    refreshed_at = _NOW - timedelta(days=2)
    user_row = _make_entity_info_row(entity_id=entity_id, last_test_ok=True)
    catalogue_row = _make_row(
        provider="google",
        required_scopes=[
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
    )
    granted_row = _make_row(
        entity_id=entity_id,
        granted_scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    expiry_row = _make_row(entity_id=entity_id, last_token_refresh_at=refreshed_at)
    audit_row = _make_row(
        target="u:google",
        ts=_NOW,
        actor="owner",
        action="verified",
        note="checked",
    )
    mock_db = _make_db_manager_for_per_credential(user_row=user_row)
    shared_pool = mock_db.credential_shared_pool.return_value

    async def _fetch(sql, *args):
        if "provider_feature_catalogue" in sql:
            return [catalogue_row]
        if "granted_scopes" in sql:
            return [granted_row]
        if "last_token_refresh_at" in sql:
            return [expiry_row]
        if "public.audit_log" in sql:
            assert args == (["u:google"], 10)
            return [audit_row]
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)

    response = _build_app(mock_db).get("/api/secrets/user/google")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["capabilities_required"] == ["calendar", "gmail"]
    assert data["capabilities_granted"] == ["calendar"]
    assert datetime.fromisoformat(data["expires"]) == refreshed_at + timedelta(days=7)
    assert [event["action"] for event in data["audit"]] == ["verified"]


def test_user_credential_detail_maps_unknown_google_scope_to_other():
    """An unrecognised Google scope falls to 'other' rather than leaking through."""
    user_row = _make_entity_info_row(last_test_ok=True)
    catalogue_row = _make_row(
        provider="google",
        required_scopes=["https://www.googleapis.com/auth/sentinel-unmapped-scope"],
    )
    mock_db = _make_db_manager_for_per_credential(user_row=user_row)
    shared_pool = mock_db.credential_shared_pool.return_value

    async def _fetch(sql, *args):
        if "provider_feature_catalogue" in sql:
            return [catalogue_row]
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)

    response = _build_app(mock_db).get("/api/secrets/user/google")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["capabilities_required"] == ["other"]
    assert "sentinel-unmapped-scope" not in response.text


def test_user_credential_detail_maps_non_google_scopes_to_connectivity():
    """Non-Google providers have one generic live check: 'connectivity'."""
    user_row = _make_entity_info_row(info_type="telegram_bot_token", last_test_ok=True)
    catalogue_row = _make_row(provider="telegram_bot", required_scopes=["telegram:bot-token"])
    mock_db = _make_db_manager_for_per_credential(user_row=user_row)
    shared_pool = mock_db.credential_shared_pool.return_value

    async def _fetch(sql, *args):
        if "provider_feature_catalogue" in sql:
            return [catalogue_row]
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)

    response = _build_app(mock_db).get("/api/secrets/user/telegram_bot")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["capabilities_required"] == ["connectivity"]
    assert data["capabilities_granted"] == []
    assert "telegram:bot-token" not in response.text


def test_user_credential_detail_omits_every_raw_evidence_sentinel():
    """No raw scope, credential type, label, or audit/probe/failure text ships.

    Each evidence source is planted with a distinct sentinel; none may appear
    anywhere in the response bytes (owner decision, 2026-08-13).
    """
    entity_id = str(uuid4())
    user_row = _make_entity_info_row(
        entity_id=entity_id,
        info_type="google_oauth_refresh",
        label="sentinel-label",
        last_test_ok=False,
        last_test_message="sentinel-failure-tail",
    )
    probe_row = _make_probe_row(ok=False, code=401, message="sentinel-probe-message")
    catalogue_row = _make_row(
        provider="google",
        required_scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    granted_row = _make_row(
        entity_id=entity_id,
        granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    audit_row = _make_row(
        target="u:google",
        ts=_NOW,
        actor="owner",
        action="verified",
        note="sentinel-audit-note",
    )
    capability_row = _make_row(
        credential_key="google_oauth_refresh:sentinel-capability",
        ok=False,
        code=403,
        message="sentinel-capability-message",
        recorded_at=_NOW,
        latency_ms=12,
    )
    mock_db = _make_db_manager_for_per_credential(user_row=user_row, probe_row=probe_row)
    shared_pool = mock_db.credential_shared_pool.return_value

    async def _fetch(sql, *args):
        if "provider_feature_catalogue" in sql:
            return [catalogue_row]
        if "granted_scopes" in sql:
            return [granted_row]
        if "public.audit_log" in sql:
            return [audit_row]
        if "secret_probe_log" in sql:
            return [capability_row]
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)

    response = _build_app(mock_db).get("/api/secrets/user/google")

    assert response.status_code == 200, response.text
    body = response.text
    for sentinel in (
        "calendar.readonly",  # raw required scope
        "gmail.readonly",  # raw granted scope
        "googleapis.com",  # provider-supplied scope namespace
        "google_oauth_refresh",  # persisted credential type
        "sentinel-label",
        "sentinel-failure-tail",
        "sentinel-probe-message",
        "sentinel-capability-message",
        "sentinel-audit-note",
        "sentinel-capability",  # unmapped capability name
    ):
        assert sentinel not in body, f"raw evidence {sentinel!r} leaked into the response"

    data = response.json()["data"]
    assert data["capabilities_required"] == ["calendar"]
    assert data["capabilities_granted"] == ["gmail"]
    assert [entry["capability"] for entry in data["capabilities"]] == ["other"]
    assert data["capabilities"][0]["test"] == {
        "ok": False,
        "code": 403,
        "at": data["capabilities"][0]["test"]["at"],
        "latency_ms": 12,
    }
    assert set(data["test"]) == {"ok", "code", "at", "latency_ms"}
    assert set(data["audit"][0]) == {"ts", "actor", "action"}


def test_user_credential_returns_503_when_audit_source_fails():
    """An audit-read failure is unavailable evidence, never empty history."""
    mock_db = _make_db_manager_for_per_credential(user_row=_make_entity_info_row())
    shared_pool = mock_db.credential_shared_pool.return_value

    async def _fetch(sql, *args):
        if "public.audit_log" in sql:
            raise RuntimeError("audit storage unavailable")
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)

    response = _build_app(mock_db).get("/api/secrets/user/google")

    assert response.status_code == 503
    assert "audit" in response.json()["detail"].lower()
    assert "storage unavailable" not in response.text


def test_user_credential_returns_503_when_audit_table_is_unavailable():
    """A missing audit table is unavailable evidence, not empty history."""
    mock_db = _make_db_manager_for_per_credential(user_row=_make_entity_info_row())
    shared_pool = mock_db.credential_shared_pool.return_value

    async def _fetch(sql, *args):
        if "public.audit_log" in sql:
            raise UndefinedTableError("audit table unavailable")
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)

    response = _build_app(mock_db).get("/api/secrets/user/google")

    assert response.status_code == 503
    assert "audit" in response.json()["detail"].lower()
    assert "table unavailable" not in response.text


def test_spotify_detail_is_excluded_before_identity_lookup():
    """Connector-managed Spotify never resolves through generic detail."""
    entity_id = str(uuid4())
    row = _make_entity_info_row(entity_id=entity_id, info_type="spotify_oauth_refresh")
    mock_db = _make_db_manager_for_per_credential(user_row=row)
    client = _build_app(mock_db)

    resp = client.get(f"/api/secrets/user/spotify?identity={entity_id}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Credential not found"}


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


# ---------------------------------------------------------------------------
# Tests: GET /api/secrets/system/<key> — hit cases
# ---------------------------------------------------------------------------


def test_system_credential_hit_returns_200_envelope():
    """Hit case: 200 with {data, meta} envelope; required SystemCredentialDetail
    fields present, key matches path, fingerprint is 8-char hex, row_state='shared'."""
    row = _make_system_row(key="OPENAI_API_KEY", value="secretvalue", last_test_ok=True)
    mock_db = _make_db_manager_for_per_credential(system_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/OPENAI_API_KEY")
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    data = body["data"]

    for field in ("key", "category", "state", "row_state", "used_by", "audit", "butler"):
        assert field in data, f"missing field {field!r}"

    # Dropped by the content-blind projection: 'breaks' was an always-empty
    # passthrough of unallowlisted dicts (each of which carries raw scopes),
    # and last_test_message is probe free text.
    for field in ("breaks", "last_test_message", "last_test_ok", "last_test_code"):
        assert field not in data, f"unallowlisted field {field!r} must not be published"

    assert data["key"] == "OPENAI_API_KEY"
    assert data["row_state"] == "shared"
    fp = data["fingerprint"]
    assert fp is not None and len(fp) == 8
    int(fp, 16)


def test_system_credential_hit_state_warn_no_probe():
    """Hit case: state=warn when set but no probe result."""
    row = _make_system_row(key="UNVERIFIED_KEY", value="val", last_test_ok=None)
    mock_db = _make_db_manager_for_per_credential(system_row=row, probe_row=None)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/UNVERIFIED_KEY")
    assert resp.json()["data"]["state"] == "warn"


def test_system_credential_stale_successful_probe_is_warn():
    """A stale successful system probe is an unknown, not a healthy verdict."""
    row = _make_system_row(
        key="STALE_SYSTEM_KEY", last_verified=_NOW - timedelta(days=2), last_test_ok=True
    )
    mock_db = _make_db_manager_for_per_credential(system_row=row)

    response = _build_app(mock_db).get("/api/secrets/system/STALE_SYSTEM_KEY")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "warn"


def test_system_credential_hit_with_probe():
    """Hit case: test field populated from probe_log, without its free text."""
    row = _make_system_row(key="TESTED_KEY", last_test_ok=False)
    probe = _make_probe_row(ok=False, code=401, message="Unauthorized")
    mock_db = _make_db_manager_for_per_credential(system_row=row, probe_row=probe)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/TESTED_KEY")
    data = resp.json()["data"]
    assert data["test"] is not None
    assert data["test"]["ok"] is False
    assert data["test"]["code"] == 401
    assert "message" not in data["test"]


def test_system_credential_no_raw_value_in_response():
    """Security: raw credential value must NOT appear in response."""
    row = _make_system_row(key="SECRET_KEY", value="very_secret_system_value_xyz")
    mock_db = _make_db_manager_for_per_credential(system_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/system/SECRET_KEY")
    body_text = resp.text
    assert "very_secret_system_value_xyz" not in body_text


def test_system_credential_omits_every_free_text_sentinel():
    """No probe or cached failure text reaches the system detail response bytes.

    Each free-text source the row can carry is planted with a distinct
    sentinel; none may appear anywhere in the response (owner decision,
    2026-08-13).
    """
    row = _make_system_row(
        key="SENTINEL_KEY",
        last_test_ok=False,
        last_test_code=401,
        last_test_message="sentinel-system-failure-tail",
    )
    probe = _make_probe_row(ok=False, code=401, message="sentinel-system-probe-message")
    mock_db = _make_db_manager_for_per_credential(system_row=row, probe_row=probe)

    response = _build_app(mock_db).get("/api/secrets/system/SENTINEL_KEY")

    assert response.status_code == 200, response.text
    for sentinel in ("sentinel-system-failure-tail", "sentinel-system-probe-message"):
        assert sentinel not in response.text, f"{sentinel!r} leaked onto the wire"


def test_content_blind_system_detail_drops_audit_notes_and_breaks():
    """The projector holds even when a future writer populates audit or breaks.

    ``_fetch_single_system_secret`` populates neither today, which is exactly
    why the allowlist is asserted against the projector rather than only
    end-to-end: the endpoint cannot yet plant these, but a later change can.
    """
    record = SystemSecretDetail(
        key="SENTINEL_KEY",
        state="failing",
        butler="general",
        test={"ok": False, "code": 401, "message": "sentinel-probe-message"},
        audit=[
            {
                "ts": "12:00 today",
                "actor": "owner",
                "action": "rotated",
                "note": "sentinel-audit-note",
            }
        ],
        breaks=[
            {
                "butler": "general",
                "feature": "sentinel-feature-label",
                "severity": "high",
                "required_scopes": ["https://www.googleapis.com/auth/sentinel-scope"],
            }
        ],
    )

    published = _content_blind_system_detail(record).model_dump_json()

    for sentinel in (
        "sentinel-probe-message",
        "sentinel-audit-note",
        "sentinel-feature-label",
        "sentinel-scope",
    ):
        assert sentinel not in published, f"{sentinel!r} leaked onto the wire"
    assert '"action":"rotated"' in published


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


# ---------------------------------------------------------------------------
# Tests: GET /api/secrets/cli/<id> — hit cases
# ---------------------------------------------------------------------------


def test_cli_credential_hit_returns_200_envelope():
    """Hit case: 200 with {data, meta} envelope; required CliCredentialDetail
    fields present, id matches path, fingerprint 8-char hex, label from
    description, expires set."""
    expires = _NOW + timedelta(days=30)
    row = _make_cli_row(
        key="cli-xyz789", value="mysecretclitoken", description="My Dev Token", expires_at=expires
    )
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-xyz789")
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    data = body["data"]

    for field in ("id", "state", "capabilities_required", "capabilities_granted"):
        assert field in data, f"missing field {field!r}"

    # Raw scope arrays are replaced by the fixed capability vocabulary, and
    # last_used is absent rather than an always-null placeholder.
    for field in ("scopes_required", "scopes_granted", "last_used", "last_test_message"):
        assert field not in data, f"unallowlisted field {field!r} must not be published"

    assert data["id"] == "cli-xyz789"
    assert data["label"] == "My Dev Token"
    assert data["expires"] is not None
    fp = data["fingerprint"]
    assert fp is not None and len(fp) == 8
    int(fp, 16)


def test_cli_credential_hit_state_expired():
    """Hit case: state=expired when expires_at is in the past."""
    past_expires = _NOW - timedelta(days=1)
    row = _make_cli_row(key="cli-exp", expires_at=past_expires)
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-exp")
    assert resp.json()["data"]["state"] == "expired"


def test_cli_credential_stale_successful_probe_is_warn():
    """A stale successful CLI probe is an unknown, not a healthy verdict."""
    row = _make_cli_row(key="cli-stale", last_verified=_NOW - timedelta(days=2), last_test_ok=True)
    mock_db = _make_db_manager_for_per_credential(cli_row=row)

    response = _build_app(mock_db).get("/api/secrets/cli/cli-stale")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "warn"


def test_cli_credential_hit_with_probe():
    """Hit case: test field populated from probe_log."""
    row = _make_cli_row(key="cli-probed", last_test_ok=True)
    probe = _make_probe_row(ok=True, code=200)
    mock_db = _make_db_manager_for_per_credential(cli_row=row, probe_row=probe)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-probed")
    data = resp.json()["data"]
    assert data["test"] is not None
    assert data["test"]["ok"] is True


def test_cli_credential_hides_prior_probe_after_credential_replacement():
    """A health-reset replacement token has no inherited probe result."""
    row = _make_cli_row(key="cli-auth/codex", last_test_ok=None)
    probe = _make_probe_row(ok=False, message="old token rejected")
    mock_db = _make_db_manager_for_per_credential(cli_row=row, probe_row=probe)

    response = _build_app(mock_db).get("/api/secrets/cli/cli-auth/codex")

    assert response.status_code == 200
    assert response.json()["data"]["test"] is None


def test_cli_credential_no_raw_value_in_response():
    """Security: raw token value must NOT appear in response."""
    row = _make_cli_row(key="cli-sec", value="very_secret_cli_token_xyz")
    mock_db = _make_db_manager_for_per_credential(cli_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/cli/cli-sec")
    body_text = resp.text
    assert "very_secret_cli_token_xyz" not in body_text


def test_cli_credential_omits_every_free_text_sentinel():
    """No probe or cached failure text reaches the CLI detail response bytes."""
    row = _make_cli_row(
        key="cli-sentinel",
        last_test_ok=False,
        last_test_code=401,
        last_test_message="sentinel-cli-failure-tail",
    )
    probe = _make_probe_row(ok=False, code=401, message="sentinel-cli-probe-message")
    mock_db = _make_db_manager_for_per_credential(cli_row=row, probe_row=probe)

    response = _build_app(mock_db).get("/api/secrets/cli/cli-sentinel")

    assert response.status_code == 200, response.text
    for sentinel in ("sentinel-cli-failure-tail", "sentinel-cli-probe-message"):
        assert sentinel not in response.text, f"{sentinel!r} leaked onto the wire"


def test_content_blind_cli_detail_publishes_capabilities_not_scopes():
    """Scopes recorded against a CLI token surface only as vocabulary names.

    Nothing persists CLI scopes today; the projector is asserted directly so
    the guarantee is pinned before a future writer starts populating them.
    Every CLI provider is verified by one generic liveness call, so any scope
    classifies as 'connectivity' — including a Google-shaped one, which must
    not be mistaken for a per-capability Google probe.
    """
    record = CliRuntimeDetail(
        id="cli-auth/codex",
        label="CLI auth token",
        state="ok",
        scopes_required=[
            "https://www.googleapis.com/auth/calendar.readonly",
            "sentinel-required-scope",
        ],
        scopes_granted=["sentinel-granted-scope"],
        test={"ok": True, "code": 200, "message": "sentinel-probe-message"},
    )

    published = _content_blind_cli_detail(record)
    payload = published.model_dump_json()

    assert published.capabilities_required == ["connectivity"]
    assert published.capabilities_granted == ["connectivity"]
    for sentinel in (
        "sentinel-required-scope",
        "sentinel-granted-scope",
        "sentinel-probe-message",
        "googleapis.com",
    ):
        assert sentinel not in payload, f"{sentinel!r} leaked onto the wire"


def test_content_blind_cli_detail_publishes_no_capability_without_scopes():
    """Empty scope inventories publish empty capability lists, not a placeholder."""
    record = CliRuntimeDetail(id="cli-auth/claude", state="ok")

    published = _content_blind_cli_detail(record)

    assert published.capabilities_required == []
    assert published.capabilities_granted == []


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
    """Probe log at field is formatted as a human-friendly relative timestamp.

    The formatter's clock is frozen to noon UTC so a 2h-ago probe timestamp is
    always on the same calendar day ("today"), regardless of when CI runs.
    """
    row = _make_entity_info_row()
    # Anchor the probe time relative to _FROZEN_NOW so "today" is always correct.
    probe = _make_probe_row(ok=True, recorded_at=_FROZEN_NOW - timedelta(hours=2))
    mock_db = _make_db_manager_for_per_credential(user_row=row, probe_row=probe)
    client = _build_app(mock_db)

    with _freeze_time():
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
        if "butler_secrets" in sql and "category IN ('cli', 'cli-auth')" not in sql:
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

    # Verify the LIKE pattern arg was escaped: must be 'goog\%\_%' (literal
    # backslash) not 'goog%_%'.  Patterns are now passed as a text[] parameter
    # (alias-aware matching via _provider_like_patterns).
    all_params = [arg for args_tuple in captured for arg in args_tuple]
    pattern_lists = [p for p in all_params if isinstance(p, list)]
    assert any(r"goog\%\_%" in patterns for patterns in pattern_lists), (
        f"Expected escaped LIKE pattern 'goog\\%\\_%' in SQL params, got: {all_params}"
    )


def test_user_credential_provider_underscore_does_not_match_google_oauth_refresh():
    """Provider 'g_ogle' with escaping must produce 'g\\_ogle\\_%' as the LIKE pattern.

    Without escaping, 'g_ogle_%' would be sent and would match 'google_oauth_refresh'
    (the _ matches 'o').  With escaping, 'g\\_ogle\\_%' only matches literal
    'g_ogle_<anything>'.  Patterns are now passed as a text[] parameter
    (alias-aware matching via _provider_like_patterns).
    """
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db, captured = _make_capturing_db_manager(user_row=row)
    client = _build_app(mock_db)

    client.get("/api/secrets/user/g_ogle")

    all_params = [arg for args_tuple in captured for arg in args_tuple]
    pattern_lists = [p for p in all_params if isinstance(p, list)]
    assert any(r"g\_ogle\_%" in patterns for patterns in pattern_lists), (
        f"Expected escaped LIKE pattern 'g\\_ogle\\_%' in SQL params, got: {all_params}"
    )


def test_user_credential_clean_provider_passes_unmodified():
    """Provider 'google' (no metacharacters) produces the single 'google\\_%' pattern."""
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db, captured = _make_capturing_db_manager(user_row=row)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/user/google")
    assert resp.status_code == 200

    all_params = [arg for args_tuple in captured for arg in args_tuple]
    pattern_lists = [p for p in all_params if isinstance(p, list)]
    assert any(r"google\_%" in patterns for patterns in pattern_lists), (
        f"Expected LIKE pattern 'google\\_%' in SQL params, got: {all_params}"
    )

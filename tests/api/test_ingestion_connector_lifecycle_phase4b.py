"""Tests for Phase 4b Wave 3 connector lifecycle endpoints: disconnect, rotate-token, reauth.

Covers §4.4 (disconnect), §4.5 (rotate-token), §4.6 (reauth).

§4.4 DISCONNECT — POST /api/ingestion/connectors/{type}/{identity}/disconnect
  - Approvals-gated: returns 202 with {status: "pending_approval", action_id: ...}
  - Creates pending_actions row in the switchboard pool
  - Emits audit entry with action='connector.disconnect'
  - Returns 404 if connector not found
  - Returns 503 if registry or approvals subsystem unavailable

§4.5 ROTATE-TOKEN — POST /api/ingestion/connectors/{type}/{identity}/rotate-token
  - Approvals-gated: returns 202 with {success: true, rotated_at: <iso8601>} ONLY
  - Response MUST NOT contain credential/token values
  - is_sensitive=True masking: credential never appears in response, request log, or audit log
  - Credential-masking test: greps full response body, mock audit call args, pending_action args
  - Returns 404 if connector not found

§4.6 REAUTH — POST /api/ingestion/connectors/{type}/{identity}/reauth
  - Returns HTTP 503 with body identifying 'connector-oauth-scope-surface' as blocking spec
  - NO Retry-After header
  - No Approvals entry created

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
      connector-lifecycle-ceremony/spec.md
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "gmail"
_ENDPOINT_IDENTITY = "user@example.com"
_CONNECTOR_TARGET = f"{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}"

# Pattern to detect any token-like value leaking into responses/logs
# Matches hex strings ≥16 chars, base64 segments ≥16 chars, or "secret" literals
_TOKEN_PATTERN = re.compile(
    r"(?:[A-Za-z0-9+/]{16,}={0,2}|[0-9a-fA-F]{16,}|sk-[A-Za-z0-9]+|Bearer\s+\S+)",
    re.IGNORECASE,
)

_FAKE_TOKEN = "supersecrettoken12345678abcdef"  # 30-char fake credential for masking test


def _make_existing_row(
    connector_type: str = _CONNECTOR_TYPE,
    endpoint_identity: str = _ENDPOINT_IDENTITY,
) -> MagicMock:
    """Build a mock asyncpg record for an existing connector."""
    row = MagicMock()
    row.__getitem__ = lambda self, k: {
        "connector_type": connector_type,
        "endpoint_identity": endpoint_identity,
    }[k]
    row.get = lambda k, d=None: {
        "connector_type": connector_type,
        "endpoint_identity": endpoint_identity,
    }.get(k, d)
    return row


def _make_pool(
    *,
    fetchrow_result=None,
    execute_result="INSERT 1",
    fetchrow_side_effect=None,
    execute_side_effect=None,
):
    """Build a mock asyncpg pool."""
    pool = AsyncMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    if execute_side_effect is not None:
        pool.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        pool.execute = AsyncMock(return_value=execute_result)
    return pool


def _wire_db(app, pool=None, *, pool_available=True):
    """Override _get_db_manager dependency with a mock DatabaseManager."""
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        if pool is None:
            pool = _make_pool()
        mock_db.pool.return_value = pool
    else:
        mock_db.pool.side_effect = KeyError("switchboard pool not available")
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db, pool


# ---------------------------------------------------------------------------
# §4.4 DISCONNECT
# ---------------------------------------------------------------------------


async def test_disconnect_202_pending_approval(app):
    """POST disconnect on an existing connector returns 202 with pending_approval status."""
    pool = _make_pool(fetchrow_result=_make_existing_row())
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/disconnect"
            )

    assert resp.status_code == 202
    body = resp.json()
    assert body["data"]["status"] == "pending_approval"
    assert "action_id" in body["data"]
    # action_id must be a valid UUID
    import uuid

    uuid.UUID(body["data"]["action_id"])  # raises if not valid UUID


async def test_disconnect_emits_audit_entry(app):
    """POST disconnect emits an audit entry with action='connector.disconnect'."""
    pool = _make_pool(fetchrow_result=_make_existing_row())
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/disconnect"
            )

    assert resp.status_code == 202
    mock_audit.assert_awaited_once()
    _, audit_kwargs = mock_audit.call_args
    assert audit_kwargs["action"] == "connector.disconnect"
    assert _CONNECTOR_TARGET in audit_kwargs["target"]


async def test_disconnect_404_not_found(app):
    """POST disconnect on a non-existent connector returns 404."""
    pool = _make_pool(fetchrow_result=None)
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/connectors/gmail/nonexistent@example.com/disconnect"
        )

    assert resp.status_code == 404


async def test_disconnect_503_pool_unavailable(app):
    """POST disconnect returns 503 when switchboard pool is unavailable."""
    _wire_db(app, pool_available=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/disconnect"
        )

    assert resp.status_code == 503


async def test_disconnect_503_registry_db_error(app):
    """POST disconnect returns 503 when the connector registry query fails."""
    pool = _make_pool(fetchrow_side_effect=RuntimeError("db down"))
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/disconnect"
        )

    assert resp.status_code == 503


async def test_disconnect_503_approvals_insert_error(app):
    """POST disconnect returns 503 when pending_actions insert fails."""
    pool = _make_pool(
        fetchrow_result=_make_existing_row(),
        execute_side_effect=RuntimeError("approvals db down"),
    )
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/disconnect"
        )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# §4.5 ROTATE-TOKEN
# ---------------------------------------------------------------------------


async def test_rotate_token_202_success_body(app):
    """POST rotate-token returns 202 with ONLY {success: true, rotated_at}."""
    pool = _make_pool(fetchrow_result=_make_existing_row())
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/rotate-token",
                json={"new_token": _FAKE_TOKEN},
            )

    assert resp.status_code == 202
    data = resp.json()["data"]
    # Response MUST contain ONLY success and rotated_at
    assert data["success"] is True
    assert "rotated_at" in data
    # No other fields — action_id, token, credential must not appear
    assert "action_id" not in data
    assert "token" not in data
    assert "credential" not in data


async def test_rotate_token_credential_masking(app):
    """CREDENTIAL MASKING TEST: token value MUST NOT appear in response, pending_action, or audit.

    This is the core masking assertion required by §4.5. It verifies:
    1. Response body contains no token pattern matching the fake credential
    2. pending_actions INSERT args contain no token value
    3. audit.append() call args contain no token value
    """
    pool = _make_pool(fetchrow_result=_make_existing_row())
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/rotate-token",
                # Send a fake token in the request body — must never appear in outputs
                json={"new_token": _FAKE_TOKEN, "credential": _FAKE_TOKEN},
            )

    assert resp.status_code == 202

    # 1. Grep the FULL response body text for the fake token
    response_text = resp.text
    assert _FAKE_TOKEN not in response_text, (
        f"CREDENTIAL LEAK: fake token '{_FAKE_TOKEN}' found in response body: {response_text!r}"
    )

    # 2. Grep the pending_actions INSERT args for the fake token
    execute_call_args = pool.execute.call_args
    all_execute_args = str(execute_call_args)
    assert _FAKE_TOKEN not in all_execute_args, (
        f"CREDENTIAL LEAK: fake token '{_FAKE_TOKEN}' found in pending_actions INSERT args: "
        f"{all_execute_args!r}"
    )

    # Also verify the tool_args JSON stored in pending_actions has no credential field
    # The 3rd positional arg (index 2) of execute() is the tool_args JSON string
    if execute_call_args and execute_call_args.args:
        # Find the tool_args JSON in the execute call arguments
        for arg in execute_call_args.args:
            if isinstance(arg, str) and arg.startswith("{"):
                try:
                    tool_args = json.loads(arg)
                    for field_name in _FAKE_TOKEN:
                        assert _FAKE_TOKEN not in str(tool_args), (
                            f"CREDENTIAL LEAK: fake token found in pending_action tool_args JSON: "
                            f"{tool_args!r}"
                        )
                except json.JSONDecodeError:
                    pass

    # 3. Grep the audit.append() call args for the fake token
    if mock_audit.called:
        audit_call_str = str(mock_audit.call_args_list)
        assert _FAKE_TOKEN not in audit_call_str, (
            f"CREDENTIAL LEAK: fake token '{_FAKE_TOKEN}' found in audit.append() args: "
            f"{audit_call_str!r}"
        )


async def test_rotate_token_emits_audit_without_credential(app):
    """rotate-token audit entry must not contain any credential value."""
    pool = _make_pool(fetchrow_result=_make_existing_row())
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post(
                f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/rotate-token",
                json={"new_token": _FAKE_TOKEN},
            )

    mock_audit.assert_awaited_once()
    _, audit_kwargs = mock_audit.call_args
    assert audit_kwargs["action"] == "connector.rotate_token"
    # Credential must not appear in audit note
    assert _FAKE_TOKEN not in str(audit_kwargs.get("note", ""))


async def test_rotate_token_404_not_found(app):
    """POST rotate-token on a non-existent connector returns 404."""
    pool = _make_pool(fetchrow_result=None)
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/connectors/gmail/nonexistent@example.com/rotate-token"
        )

    assert resp.status_code == 404


async def test_rotate_token_creates_pending_action_with_is_sensitive(app):
    """rotate-token pending_action must include is_sensitive=True in tool_args."""
    pool = _make_pool(fetchrow_result=_make_existing_row())
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post(
                f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/rotate-token",
            )

    pool.execute.assert_awaited_once()
    call_args = pool.execute.call_args
    # The 3rd positional arg is the tool_args JSON string
    tool_args_json = call_args.args[3]  # INSERT VALUES: $1=id, $2=tool_name, $3=tool_args, ...
    tool_args = json.loads(tool_args_json)
    assert tool_args.get("is_sensitive") is True, (
        f"rotate-token pending_action tool_args must have is_sensitive=True; got: {tool_args!r}"
    )


# ---------------------------------------------------------------------------
# §4.6 REAUTH
# ---------------------------------------------------------------------------


async def test_reauth_returns_503(app):
    """POST reauth always returns HTTP 503 — blocked until connector-oauth-scope-surface exists."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/reauth"
        )

    assert resp.status_code == 503


async def test_reauth_body_identifies_blocking_spec(app):
    """POST reauth response body identifies 'connector-oauth-scope-surface' as the blocking spec."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/reauth"
        )

    assert resp.status_code == 503
    body = resp.json()
    # The body must identify the blocking spec
    body_text = json.dumps(body)
    assert "connector-oauth-scope-surface" in body_text, (
        f"reauth 503 body must identify 'connector-oauth-scope-surface' as blocking spec; "
        f"got: {body_text!r}"
    )


async def test_reauth_no_retry_after_header(app):
    """POST reauth response must NOT include a Retry-After header."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/reauth"
        )

    assert resp.status_code == 503
    assert "retry-after" not in {h.lower() for h in resp.headers.keys()}, (
        "reauth 503 must NOT include a Retry-After header (recovery requires spec creation, not time)"
    )


async def test_reauth_no_pending_action_created(app):
    """POST reauth must NOT create any pending_actions row — rejected before approval entry."""
    pool = _make_pool(fetchrow_result=_make_existing_row())
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/reauth"
        )

    assert resp.status_code == 503
    # pool.execute must NOT have been called (no pending_actions insert)
    pool.execute.assert_not_awaited()


async def test_reauth_no_db_required(app):
    """POST reauth returns 503 even without any DB setup — rejected before registry access."""
    # No DB wired — endpoint must still return 503 without crashing
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/ingestion/connectors/{_CONNECTOR_TYPE}/{_ENDPOINT_IDENTITY}/reauth"
        )

    # Must be 503, not 500 (handler-level rejection)
    assert resp.status_code == 503

"""Tests for webhook CRUD API.

Covers:
- GET /api/webhooks returns list from DB.
- POST /api/webhooks creates a row with AES-GCM encrypted secret.
- DELETE /api/webhooks/{id} removes a row.
- POST /api/webhooks/{id}/test returns a test result.
- 503 when switchboard pool unavailable.
- Signing uses the plaintext secret (decrypt then HMAC).
- Missing WEBHOOK_SECRET_KEY causes encrypt to fail loudly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.webhooks import _get_db_manager
from butlers.core.crypto import aes_gcm

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_WH_ID = str(uuid.uuid4())

# Fixed test key — 32 bytes as 64 hex chars.
_TEST_KEY = bytes(range(32))
_TEST_KEY_HEX = _TEST_KEY.hex()


def _make_webhook_record(overrides: dict | None = None) -> dict:
    base = {
        "id": uuid.UUID(_WH_ID),
        "endpoint": "https://example.com/hook",
        "events": json.dumps(["data.export", "permission.set"]),
        "enabled": True,
        "secret_encrypted": None,
        "last_test_at": None,
        "last_test_ok": None,
        "retry_policy": json.dumps({"max_attempts": 3, "backoff_seconds": 2}),
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    if overrides:
        base.update(overrides)
    return base


def _make_record(row: dict) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k, _r=row: _r[k])
    return m


def _make_pool(
    *,
    rows: list[dict] | None = None,
    fetchrow_return: dict | None = None,
    execute_return: str = "DELETE 1",
) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_make_record(r) for r in (rows or [])])
    pool.fetchrow = AsyncMock(
        return_value=_make_record(fetchrow_return) if fetchrow_return else None
    )
    pool.execute = AsyncMock(return_value=execute_return)
    return pool


def _make_db(pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/webhooks
# ---------------------------------------------------------------------------


async def test_list_webhooks_empty(app):
    """Empty DB returns empty list."""
    pool = _make_pool(rows=[])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/webhooks")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_list_webhooks_returns_rows(app):
    """Rows from DB are returned as webhook objects."""
    row = _make_webhook_record()
    pool = _make_pool(rows=[row])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/webhooks")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["endpoint"] == "https://example.com/hook"


# ---------------------------------------------------------------------------
# POST /api/webhooks  (encryption path)
# ---------------------------------------------------------------------------


async def test_create_webhook(app, monkeypatch):
    """POST creates a webhook; secret is encrypted before insert."""
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", _TEST_KEY_HEX)

    created_row = _make_webhook_record()
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value=_make_record(created_row))
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.webhooks.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/webhooks",
                json={
                    "endpoint": "https://example.com/hook",
                    "events": ["permission.set"],
                    "enabled": True,
                    "secret": "mysecret",
                },
            )

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["endpoint"] == "https://example.com/hook"

    # Verify the INSERT was called with bytes (not a hash string).
    call_args = pool.fetchrow.call_args
    # The 5th positional arg ($5) is secret_encrypted.
    positional = call_args[0]
    # positional[0] = SQL, then $1..$8 follow
    secret_arg = positional[5]  # $5 = secret_encrypted
    assert isinstance(secret_arg, bytes), "secret should be stored as encrypted bytes"
    # Decrypt and verify round-trip.
    assert aes_gcm.decrypt(secret_arg, key=_TEST_KEY) == "mysecret"


async def test_create_webhook_no_secret(app, monkeypatch):
    """POST without a secret stores NULL for secret_encrypted."""
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", _TEST_KEY_HEX)

    created_row = _make_webhook_record()
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value=_make_record(created_row))
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.webhooks.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/webhooks",
                json={"endpoint": "https://example.com/hook"},
            )

    assert resp.status_code == 201
    # secret_encrypted arg should be None
    call_args = pool.fetchrow.call_args[0]
    secret_arg = call_args[5]
    assert secret_arg is None


# ---------------------------------------------------------------------------
# DELETE /api/webhooks/{id}
# ---------------------------------------------------------------------------


async def test_delete_webhook_success(app):
    """DELETE returns 200 and wiped=True when row exists."""
    pool = _make_pool(execute_return="DELETE 1")
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.webhooks.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(f"/api/webhooks/{_WH_ID}")

    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True


async def test_delete_webhook_not_found(app):
    """DELETE returns 404 when row does not exist."""
    pool = _make_pool(execute_return="DELETE 0")
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/webhooks/{_WH_ID}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/webhooks/{id}/test  (signing path)
# ---------------------------------------------------------------------------


async def test_test_webhook_not_found(app):
    """Test endpoint returns 404 when webhook does not exist."""
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value=None)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/webhooks/{_WH_ID}/test")

    assert resp.status_code == 404


async def test_test_webhook_returns_result(app):
    """Test endpoint dispatches and returns a result object."""
    row = _make_webhook_record()
    pool = _make_pool(fetchrow_return=row)
    pool.execute = AsyncMock(return_value=None)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    from butlers.api.routers.webhooks import WebhookTestResult

    fake_result = WebhookTestResult(
        webhook_id=uuid.UUID(_WH_ID),
        status_code=200,
        latency_ms=42.0,
        ok=True,
    )

    with (
        patch(
            "butlers.api.routers.webhooks._dispatch_webhook",
            new_callable=AsyncMock,
            return_value=fake_result,
        ),
        patch("butlers.api.routers.webhooks.audit.append", new_callable=AsyncMock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/api/webhooks/{_WH_ID}/test")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["status_code"] == 200


# ---------------------------------------------------------------------------
# Signing decrypts then HMAC-signs (not hash-of-hash)
# ---------------------------------------------------------------------------


async def test_dispatch_uses_plaintext_secret_for_signing(monkeypatch):
    """_dispatch_webhook decrypts the stored secret before HMAC signing.

    Verifies that the X-Butler-Signature header is HMAC-SHA256(plaintext, body)
    — the standard pattern a receiver can replicate with their shared plaintext
    secret.
    """
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", _TEST_KEY_HEX)

    from butlers.api.routers.webhooks import RetryPolicy, _dispatch_webhook

    plaintext = "correct-horse-battery-staple"
    encrypted = aes_gcm.encrypt(plaintext, key=_TEST_KEY)

    payload = {"event": "webhook.test", "webhook_id": str(uuid.uuid4()), "timestamp": "now"}
    raw = json.dumps(payload).encode()
    expected_sig = hmac.new(plaintext.encode(), raw, hashlib.sha256).hexdigest()

    captured_headers: dict = {}

    async def fake_post(self, url, *, content, headers, **kwargs):
        captured_headers.update(headers)
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        return mock_resp

    import httpx

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        await _dispatch_webhook(
            endpoint="https://example.com/hook",
            payload=payload,
            secret_encrypted=encrypted,
            retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0),
        )

    sig_header = captured_headers.get("X-Butler-Signature", "")
    assert sig_header == f"sha256={expected_sig}"


async def test_dispatch_no_secret_no_signature():
    """_dispatch_webhook with no secret does not add X-Butler-Signature header."""
    from butlers.api.routers.webhooks import RetryPolicy, _dispatch_webhook

    captured_headers: dict = {}

    async def fake_post(self, url, *, content, headers, **kwargs):
        captured_headers.update(headers)
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        return mock_resp

    import httpx

    payload = {"event": "webhook.test", "webhook_id": str(uuid.uuid4()), "timestamp": "now"}

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        await _dispatch_webhook(
            endpoint="https://example.com/hook",
            payload=payload,
            secret_encrypted=None,
            retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0),
        )

    assert "X-Butler-Signature" not in captured_headers


# ---------------------------------------------------------------------------
# Missing-key error path
# ---------------------------------------------------------------------------


async def test_create_webhook_missing_key_fails(app, monkeypatch):
    """POST /api/webhooks returns 500 when WEBHOOK_SECRET_KEY is absent.

    The encrypt helper raises RuntimeError (fail-loud); FastAPI converts
    unhandled exceptions to HTTP 500 responses.
    """
    monkeypatch.delenv("WEBHOOK_SECRET_KEY", raising=False)

    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/webhooks",
            json={"endpoint": "https://example.com/hook", "secret": "s"},
        )
    # Missing key is a hard fail — should not succeed.
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# 503 guard
# ---------------------------------------------------------------------------


async def test_list_webhooks_503_when_no_switchboard(app):
    """Returns 503 when switchboard pool is unavailable."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = KeyError("switchboard")
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/webhooks")

    assert resp.status_code == 503

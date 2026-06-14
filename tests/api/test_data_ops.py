"""Tests for data operations API (§6.5 + §6.7).

Covers:
- POST /api/data/export returns a signed URL and calls audit.append.
- GET /api/data/export/download/{id}: valid token returns 200 + NDJSON.
- GET /api/data/export/download/{id}: expired token returns 410.
- GET /api/data/export/download/{id}: bad signature returns 401.
- GET /api/data/export/download/{id}: wrong scope returns 401 (signature mismatch).
- DELETE /api/data/wipe: exact phrase passes.
- DELETE /api/data/wipe: trailing whitespace fails.
- DELETE /api/data/wipe: lowercase phrase fails.
- DELETE /api/data/wipe: missing phrase field returns 422.
- Startup warning for unset DASHBOARD_EXPORT_SECRET env var.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.data_ops import _get_db_manager, _sign_token

pytestmark = pytest.mark.unit

_EXACT_PHRASE = "WIPE EVERYTHING IRREVERSIBLY"


def _make_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])  # no butler schemas
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
# POST /api/data/export
# ---------------------------------------------------------------------------


async def test_export_returns_signed_url(app):
    """POST /api/data/export returns a signed URL with 60-minute TTL."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/data/export", json={"scope": "all"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "signed_url" in data
    assert data["scope"] == "all"
    assert "expires_at" in data


async def test_export_calls_audit(app):
    """POST /api/data/export calls audit.append with action=data.export."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock) as mock_audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/data/export", json={"scope": "contacts"})

    # The route emits an explicit audit entry with action "data.export"; the
    # dashboard_audit_middleware ALSO routes through the same canonical
    # audit.append() as a fire-and-forget task, so the total count races between
    # 1 and 2.  Assert on the route's specific call rather than the count.
    # pool, actor, action are positional; note is keyword-only.
    route_calls = [
        c for c in mock_audit.call_args_list if len(c.args) >= 3 and c.args[2] == "data.export"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'data.export', "
        f"got call list: {mock_audit.call_args_list}"
    )
    assert route_calls[0].kwargs["note"] == "contacts"


async def test_export_signed_url_includes_issued_at(app):
    """POST /api/data/export signed URL includes issued_at query parameter."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/data/export", json={"scope": "all"})

    signed_url = resp.json()["data"]["signed_url"]
    assert "issued_at=" in signed_url
    assert "token=" in signed_url
    assert "/api/data/export/download/" in signed_url


# ---------------------------------------------------------------------------
# GET /api/data/export/download/{export_id}
# ---------------------------------------------------------------------------


def _make_download_url(export_id: str, scope: str, issued_at: int | None = None) -> str:
    """Build a valid signed download URL for testing."""
    ts = issued_at if issued_at is not None else int(time.time())
    token = _sign_token(export_id, scope, ts)
    return f"/api/data/export/download/{export_id}?scope={scope}&issued_at={ts}&token={token}"


async def test_download_valid_token_returns_200(app):
    """Valid token within TTL returns 200 and NDJSON content."""
    pool = _make_pool()
    # Simulate one row in contacts
    pool.fetch = AsyncMock(return_value=[{"id": 1, "name": "Alice"}])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-1234"
    url = _make_download_url(export_id, "contacts")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    assert "ndjson" in resp.headers["content-type"]
    body = resp.text
    assert "contacts" in body  # table header comment
    assert '"Alice"' in body  # row data


async def test_download_all_scope_fetches_all_tables(app):
    """scope=all causes all exportable tables to be fetched."""
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-all"
    url = _make_download_url(export_id, "all")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    body = resp.text
    # Both exportable tables should appear as comments in the body.
    # Note: contact_info was removed from _EXPORTABLE_TABLES (bu-tv67t) —
    # channel identifiers are now stored in relationship.entity_facts.
    assert "// table=contacts" in body
    assert "// table=audit_log" in body
    assert "// table=contact_info" not in body


async def test_download_expired_token_returns_410(app):
    """Token older than 60 minutes returns 410 Gone."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-expired"
    # issued_at 61 minutes ago
    old_ts = int(time.time()) - 3661
    url = _make_download_url(export_id, "all", issued_at=old_ts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 410


async def test_download_bad_signature_returns_401(app):
    """Tampered token returns 401 Unauthorized."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-badsig"
    ts = int(time.time())
    bad_token = "deadbeefdeadbeefdeadbeefdeadbeef"
    url = f"/api/data/export/download/{export_id}?scope=all&issued_at={ts}&token={bad_token}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 401


async def test_download_wrong_scope_returns_401(app):
    """Token signed for scope=all but requested with scope=contacts returns 401.

    The signature covers the scope, so mismatched scope causes a signature
    verification failure (401), not a scope-specific rejection (403).
    """
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-wrongscope"
    ts = int(time.time())
    # Token is signed for scope=all
    token = _sign_token(export_id, "all", ts)
    # Request uses scope=contacts → signature mismatch
    url = f"/api/data/export/download/{export_id}?scope=contacts&issued_at={ts}&token={token}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 401


async def test_download_content_disposition_header(app):
    """Download response includes Content-Disposition attachment header."""
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-header"
    url = _make_download_url(export_id, "contacts")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "ndjson" in cd


async def test_download_future_issued_at_returns_401(app):
    """Token with far-future issued_at is rejected (clock-forward bypass attempt).

    A valid HMAC signed with issued_at far in the future would have a
    negative age_s, bypassing the TTL check.  The handler must reject it.
    """
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-future"
    # issued_at 1 year in the future
    future_ts = int(time.time()) + 365 * 24 * 3600
    token = _sign_token(export_id, "all", future_ts)
    url = f"/api/data/export/download/{export_id}?scope=all&issued_at={future_ts}&token={token}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 401


async def test_download_negative_issued_at_returns_401(app):
    """Token with negative issued_at is rejected."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-negative"
    negative_ts = -1
    token = _sign_token(export_id, "all", negative_ts)
    url = f"/api/data/export/download/{export_id}?scope=all&issued_at={negative_ts}&token={token}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/data/wipe — phrase validation (§6.7)
# ---------------------------------------------------------------------------


async def test_wipe_exact_phrase_passes(app):
    """Exact phrase allows the wipe to proceed."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.request(
                "DELETE",
                "/api/data/wipe",
                json={"phrase": _EXACT_PHRASE},
            )

    assert resp.status_code == 200
    assert resp.json()["data"]["wiped"] is True


async def test_wipe_trailing_whitespace_fails(app):
    """Phrase with trailing whitespace is rejected (no trim)."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(
            "DELETE",
            "/api/data/wipe",
            json={"phrase": _EXACT_PHRASE + " "},
        )

    assert resp.status_code == 422


async def test_wipe_lowercase_phrase_fails(app):
    """Lowercase phrase is rejected (no case-fold)."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(
            "DELETE",
            "/api/data/wipe",
            json={"phrase": _EXACT_PHRASE.lower()},
        )

    assert resp.status_code == 422


async def test_wipe_missing_phrase_returns_422(app):
    """Missing phrase field returns 422 (Pydantic validation error)."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(
            "DELETE",
            "/api/data/wipe",
            json={},
        )

    assert resp.status_code == 422


async def test_wipe_leading_whitespace_fails(app):
    """Phrase with leading whitespace is rejected."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(
            "DELETE",
            "/api/data/wipe",
            json={"phrase": " " + _EXACT_PHRASE},
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Startup warnings (§6.5.1)
# ---------------------------------------------------------------------------


async def test_startup_warns_when_dashboard_export_secret_unset(caplog):
    """Startup logs WARNING when DASHBOARD_EXPORT_SECRET env var is unset."""
    import os

    from butlers.api.app import lifespan

    with patch.dict("os.environ", {}, clear=False):
        # Explicitly remove the env var if it exists
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)

        with caplog.at_level(logging.WARNING):
            app = create_app()
            # Trigger the lifespan startup by using the lifespan context manager
            async with lifespan(app):
                pass

    # Check that the warning was emitted
    assert any(
        "DASHBOARD_EXPORT_SECRET env var is not set" in record.message
        and record.levelname == "WARNING"
        for record in caplog.records
    ), f"Expected warning not found in logs: {[r.message for r in caplog.records]}"
    assert any("insecure 'dev-secret' fallback" in record.message for record in caplog.records)


async def test_startup_no_warning_when_dashboard_export_secret_is_set(caplog):
    """Startup does NOT log warning when DASHBOARD_EXPORT_SECRET is set."""
    from butlers.api.app import lifespan

    with patch.dict("os.environ", {"DASHBOARD_EXPORT_SECRET": "prod-secret-key"}):
        with caplog.at_level(logging.WARNING):
            app = create_app()
            # Trigger the lifespan startup by using the lifespan context manager
            async with lifespan(app):
                pass

    # Check that the warning was NOT emitted for the env var
    assert not any(
        "DASHBOARD_EXPORT_SECRET env var is not set" in record.message for record in caplog.records
    )

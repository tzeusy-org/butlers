"""Tests for data operations API (§6.5 + §6.7).

Covers:
- POST /api/data/export returns a signed URL and calls audit.append.
- POST /api/data/export rejects unknown scopes with 400.
- GET /api/data/export/download/{id}: valid token returns 200 + encrypted ZIP.
- GET /api/data/export/download/{id}: each named scope (memory/audit/config/all/full)
  returns a non-empty encrypted ZIP whose decrypted contents include the expected
  table NDJSON files.
- GET /api/data/export/download/{id}: round-trip decrypt → valid ZIP with NDJSON data.
- GET /api/data/export/download/{id}: expired token returns 410.
- GET /api/data/export/download/{id}: bad signature returns 401.
- GET /api/data/export/download/{id}: wrong scope returns 401 (signature mismatch).
- DELETE /api/data/wipe: exact phrase passes.
- DELETE /api/data/wipe: trailing whitespace fails.
- DELETE /api/data/wipe: lowercase phrase fails.
- DELETE /api/data/wipe: missing phrase field returns 422.
- Startup warning/error for unset DASHBOARD_EXPORT_SECRET env var.
- _sign_token refuses to sign in production without DASHBOARD_EXPORT_SECRET.
- _sign_token uses dev-mode fallback (not 'dev-secret') when secret is unset in dev.
- _sign_token signs correctly when DASHBOARD_EXPORT_SECRET is set explicitly.
- Literal 'dev-secret' string is never used as the signing key.
- _encrypt_export / _decrypt_export round-trip correctness and error cases.
"""

from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.data_ops import (
    _DEV_EXPORT_ENCRYPTION_KEY,
    _DEV_EXPORT_SECRET,
    _KNOWN_SCOPES,
    _SCOPE_ALIASES,
    _SCOPE_MAP,
    _decrypt_export,
    _encrypt_export,
    _get_db_manager,
    _sign_token,
)

pytestmark = pytest.mark.unit

_EXACT_PHRASE = "WIPE EVERYTHING IRREVERSIBLY"


def _make_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])  # no butler schemas / empty tables by default
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
# Helper: decrypt response bytes → {filename: ndjson_text}
# ---------------------------------------------------------------------------


def _decrypt_zip_content(encrypted_bytes: bytes) -> dict[str, str]:
    """Decrypt export bytes and return {filename: ndjson_content} for each file."""
    zip_bytes = _decrypt_export(encrypted_bytes, key=_DEV_EXPORT_ENCRYPTION_KEY)
    result: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            result[name] = zf.read(name).decode()
    return result


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
            await client.post("/api/data/export", json={"scope": "audit"})

    # The route emits an explicit audit entry with action "data.export"; the
    # dashboard_audit_middleware ALSO routes through the same canonical
    # audit.append() as a fire-and-forget task, so the total count races between
    # 1 and 2.  Assert on the route's specific call rather than the count.
    route_calls = [
        c for c in mock_audit.call_args_list if len(c.args) >= 3 and c.args[2] == "data.export"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'data.export', "
        f"got call list: {mock_audit.call_args_list}"
    )
    assert route_calls[0].kwargs["note"] == "audit"


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


async def test_export_unknown_scope_returns_400(app):
    """POST /api/data/export rejects unknown scopes with 400 Bad Request."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/data/export", json={"scope": "entities"})

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"] == "unknown_scope"
    assert detail["scope"] == "entities"
    assert "valid_scopes" in detail


async def test_export_accepts_full_alias(app):
    """POST /api/data/export accepts 'full' as an alias for 'all'."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/data/export", json={"scope": "full"})

    assert resp.status_code == 200
    assert resp.json()["data"]["scope"] == "full"


# ---------------------------------------------------------------------------
# GET /api/data/export/download/{export_id}
# ---------------------------------------------------------------------------


def _make_download_url(export_id: str, scope: str, issued_at: int | None = None) -> str:
    """Build a valid signed download URL for testing."""
    ts = issued_at if issued_at is not None else int(time.time())
    token = _sign_token(export_id, scope, ts)
    return f"/api/data/export/download/{export_id}?scope={scope}&issued_at={ts}&token={token}"


async def test_download_valid_token_returns_200_encrypted(app):
    """Valid token within TTL returns 200 and application/octet-stream content."""
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[{"id": 1, "action": "data.export", "actor": "owner"}])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-1234"
    url = _make_download_url(export_id, "audit")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    # Content is an encrypted blob — must be at least nonce(12) + tag(16) bytes
    assert len(resp.content) > 28


async def test_download_audit_scope_returns_encrypted_zip_with_real_data(app):
    """scope=audit returns encrypted ZIP containing public_audit_log.ndjson with real rows."""
    pool = _make_pool()
    pool.fetch = AsyncMock(
        return_value=[
            {"id": 1, "action": "permission.set", "actor": "owner"},
            {"id": 2, "action": "data.export", "actor": "owner"},
        ]
    )
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-audit"
    url = _make_download_url(export_id, "audit")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    files = _decrypt_zip_content(resp.content)
    assert "public_audit_log.ndjson" in files
    rows = [json.loads(line) for line in files["public_audit_log.ndjson"].splitlines() if line]
    assert len(rows) == 2
    assert rows[0]["action"] == "permission.set"
    assert rows[1]["action"] == "data.export"


async def test_download_config_scope_returns_encrypted_zip_with_all_config_tables(app):
    """scope=config returns encrypted ZIP with runtime_config, model_catalog, permissions."""
    pool = _make_pool()
    # Return one row per table call (3 tables → fetch called 3 times)
    pool.fetch = AsyncMock(
        side_effect=[
            [{"id": 1, "butler": "general", "max_concurrent": 3}],  # runtime_config
            [{"id": "m1", "model_id": "claude-sonnet"}],  # model_catalog
            [{"id": "p1", "butler": "general", "perm": "notify"}],  # permissions
        ]
    )
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-config"
    url = _make_download_url(export_id, "config")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    files = _decrypt_zip_content(resp.content)
    assert "public_runtime_config.ndjson" in files
    assert "public_model_catalog.ndjson" in files
    assert "public_permissions.ndjson" in files

    for fname in (
        "public_runtime_config.ndjson",
        "public_model_catalog.ndjson",
        "public_permissions.ndjson",
    ):
        rows = [json.loads(line) for line in files[fname].splitlines() if line]
        assert len(rows) == 1, f"expected 1 row in {fname}, got {len(rows)}"


async def test_download_memory_scope_returns_encrypted_zip_with_memory_tables(app):
    """scope=memory returns encrypted ZIP with facts, rules, episodes NDJSON files."""
    pool = _make_pool()
    pool.fetch = AsyncMock(
        side_effect=[
            [{"id": "f1", "subject": "owner", "predicate": "name", "content": "Tze"}],
            [{"id": "r1", "content": "Always greet politely", "scope": "global"}],
            [{"id": "e1", "butler": "general", "content": "Session log entry"}],
        ]
    )
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-memory"
    url = _make_download_url(export_id, "memory")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    files = _decrypt_zip_content(resp.content)
    assert "memory_facts.ndjson" in files
    assert "memory_rules.ndjson" in files
    assert "memory_episodes.ndjson" in files

    facts = [json.loads(line) for line in files["memory_facts.ndjson"].splitlines() if line]
    assert facts[0]["predicate"] == "name"
    rules = [json.loads(line) for line in files["memory_rules.ndjson"].splitlines() if line]
    assert "politely" in rules[0]["content"]
    episodes = [json.loads(line) for line in files["memory_episodes.ndjson"].splitlines() if line]
    assert episodes[0]["butler"] == "general"


async def test_download_all_scope_includes_all_tables(app):
    """scope=all returns encrypted ZIP with tables from every sub-scope (7 total)."""
    pool = _make_pool()
    # 7 tables: memory.facts, memory.rules, memory.episodes,
    #           public.audit_log, public.runtime_config, public.model_catalog, public.permissions
    pool.fetch = AsyncMock(
        side_effect=[
            [{"id": "f1", "content": "fact-data"}],
            [{"id": "r1", "content": "rule-data"}],
            [{"id": "e1", "content": "episode-data"}],
            [{"id": 1, "action": "data.export"}],
            [{"id": 1, "butler": "general"}],
            [{"id": "m1", "model_id": "sonnet"}],
            [{"id": "p1", "butler": "general"}],
        ]
    )
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-all"
    url = _make_download_url(export_id, "all")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    files = _decrypt_zip_content(resp.content)
    expected_files = {
        "memory_facts.ndjson",
        "memory_rules.ndjson",
        "memory_episodes.ndjson",
        "public_audit_log.ndjson",
        "public_runtime_config.ndjson",
        "public_model_catalog.ndjson",
        "public_permissions.ndjson",
    }
    assert expected_files == set(files.keys()), (
        f"Expected {expected_files}, got {set(files.keys())}"
    )
    for fname in expected_files:
        rows = [json.loads(line) for line in files[fname].splitlines() if line]
        assert len(rows) >= 1, f"expected ≥1 row in {fname}, got 0"


async def test_download_full_alias_same_as_all(app):
    """scope=full (alias for all) returns the same 7-table set as scope=all."""
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[{"id": 1}])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-full"
    url = _make_download_url(export_id, "full")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    files = _decrypt_zip_content(resp.content)
    assert len(files) == 7  # same as scope=all


async def test_download_round_trip_decrypt(app):
    """Round-trip: POST → get signed_url → GET download → decrypt → valid ZIP with rows."""
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[{"id": 99, "action": "webhook.test", "actor": "owner"}])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    # Step 1: POST to get the signed URL
    with patch("butlers.api.routers.data_ops.audit.append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post_resp = await client.post("/api/data/export", json={"scope": "audit"})

    assert post_resp.status_code == 200
    signed_url = post_resp.json()["data"]["signed_url"]

    # Step 2: GET the signed URL
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_resp = await client.get(signed_url)

    assert get_resp.status_code == 200
    assert get_resp.headers["content-type"] == "application/octet-stream"

    # Step 3: Decrypt and verify ZIP structure
    zip_bytes = _decrypt_export(get_resp.content, key=_DEV_EXPORT_ENCRYPTION_KEY)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "public_audit_log.ndjson" in zf.namelist()
        rows = [
            json.loads(line)
            for line in zf.read("public_audit_log.ndjson").decode().splitlines()
            if line
        ]
        assert len(rows) == 1
        assert rows[0]["action"] == "webhook.test"


async def test_download_skip_columns_excludes_embeddings(app):
    """Embedding and search_vector columns are excluded from the exported NDJSON."""
    pool = _make_pool()
    pool.fetch = AsyncMock(
        return_value=[
            {
                "id": "f1",
                "content": "fact content",
                "embedding": [0.1] * 10,  # should be stripped
                "search_vector": "fat 'fact':1",  # should be stripped
                "description_embedding": [0.2] * 10,  # should be stripped
            }
        ]
    )
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-skip-cols"
    url = _make_download_url(export_id, "memory")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    files = _decrypt_zip_content(resp.content)
    facts_content = files["memory_facts.ndjson"]
    row = json.loads(facts_content.strip())
    # Retained columns
    assert row["id"] == "f1"
    assert row["content"] == "fact content"
    # Stripped columns
    assert "embedding" not in row
    assert "search_vector" not in row
    assert "description_embedding" not in row


async def test_download_table_fetch_failure_returns_500(app):
    """If a table fetch fails mid-export, the endpoint returns 500 (fail-fast, no silent truncation)."""
    pool = _make_pool()
    pool.fetch = AsyncMock(side_effect=RuntimeError("db error"))
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-fetch-fail"
    url = _make_download_url(export_id, "audit")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 500
    assert "Export failed" in resp.json()["detail"]


async def test_download_content_type_and_disposition(app):
    """Download response has application/octet-stream content-type and .enc filename."""
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-header"
    url = _make_download_url(export_id, "audit")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".enc" in cd


async def test_download_expired_token_returns_410(app):
    """Token older than 60 minutes returns 410 Gone."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-expired"
    old_ts = int(time.time()) - 3661  # 61 minutes ago
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
    """Token signed for scope=all but requested with scope=audit returns 401.

    The signature covers the scope, so mismatched scope causes a signature
    verification failure (401) before any scope-validity check.
    """
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-wrongscope"
    ts = int(time.time())
    token = _sign_token(export_id, "all", ts)
    # Request uses scope=audit → signature mismatch
    url = f"/api/data/export/download/{export_id}?scope=audit&issued_at={ts}&token={token}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 401


async def test_download_future_issued_at_returns_401(app):
    """Token with far-future issued_at is rejected (clock-forward bypass attempt)."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    export_id = "test-export-id-future"
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


async def test_startup_warns_when_dashboard_export_secret_unset_in_dev(caplog):
    """Startup logs WARNING (not ERROR) when DASHBOARD_EXPORT_SECRET is unset in dev mode."""
    import os

    from butlers.api.app import lifespan

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)
        os.environ.pop("ENV", None)

        with caplog.at_level(logging.WARNING):
            app = create_app()
            async with lifespan(app):
                pass

    assert any(
        "DASHBOARD_EXPORT_SECRET is not set" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    ), f"Expected WARNING not found in logs: {[r.message for r in caplog.records]}"
    assert any("dev-mode fallback" in record.message for record in caplog.records)
    assert not any("dev-secret" in record.message for record in caplog.records)


async def test_startup_logs_error_when_dashboard_export_secret_unset_in_production(caplog):
    """Startup logs ERROR (not just WARNING) when DASHBOARD_EXPORT_SECRET is unset in production."""
    import os

    from butlers.api.app import lifespan

    with patch.dict("os.environ", {"ENV": "prod"}, clear=False):
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)

        with caplog.at_level(logging.WARNING):
            app = create_app()
            async with lifespan(app):
                pass

    assert any(
        "DASHBOARD_EXPORT_SECRET is not set" in record.message and record.levelname == "ERROR"
        for record in caplog.records
    ), f"Expected ERROR not found in logs: {[r.message for r in caplog.records]}"
    assert any("REFUSED" in record.message for record in caplog.records)


async def test_startup_no_warning_when_dashboard_export_secret_is_set(caplog):
    """Startup does NOT log warning when DASHBOARD_EXPORT_SECRET is set."""
    from butlers.api.app import lifespan

    with patch.dict("os.environ", {"DASHBOARD_EXPORT_SECRET": "prod-secret-key"}):
        with caplog.at_level(logging.WARNING):
            app = create_app()
            async with lifespan(app):
                pass

    assert not any(
        "DASHBOARD_EXPORT_SECRET is not set" in record.message for record in caplog.records
    )


# ---------------------------------------------------------------------------
# _sign_token security contract tests
# ---------------------------------------------------------------------------


def test_sign_token_refuses_in_production_without_secret():
    """_sign_token raises RuntimeError in production when DASHBOARD_EXPORT_SECRET is unset."""
    import os

    with patch.dict("os.environ", {"ENV": "prod"}, clear=False):
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)
        with pytest.raises(RuntimeError, match="Refusing to sign export tokens"):
            _sign_token("export-id", "all", 1234567890)


def test_sign_token_refuses_in_production_variant():
    """_sign_token refuses for ENV=production (full spelling)."""
    import os

    with patch.dict("os.environ", {"ENV": "production"}, clear=False):
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)
        with pytest.raises(RuntimeError):
            _sign_token("export-id", "all", 1234567890)


def test_sign_token_uses_dev_fallback_outside_production():
    """_sign_token succeeds in dev mode without DASHBOARD_EXPORT_SECRET (uses dev fallback)."""
    import os

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)
        os.environ.pop("ENV", None)
        result = _sign_token("export-id", "all", 1234567890)
    assert len(result) == 32
    assert result.isalnum()


def test_sign_token_with_explicit_secret_round_trips():
    """_sign_token and _verify_token round-trip correctly with an explicit secret."""
    import time

    from butlers.api.routers.data_ops import _verify_token

    with patch.dict("os.environ", {"DASHBOARD_EXPORT_SECRET": "explicit-test-secret-value"}):
        export_id = "roundtrip-export-id"
        scope = "all"
        issued_at = int(time.time())
        token = _sign_token(export_id, scope, issued_at)
        _verify_token(export_id, scope, issued_at, token)


def test_sign_token_literal_dev_secret_never_used_in_production():
    """The literal string 'dev-secret' is never used as the signing key in production."""
    import hashlib as _hashlib
    import hmac as _hmac
    import os

    with patch.dict("os.environ", {"ENV": "prod"}, clear=False):
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)
        with pytest.raises(RuntimeError):
            _sign_token("export-id", "all", 1234567890)

    literal_dev_secret_output = _hmac.new(
        b"dev-secret", b"export-id:all:1234567890", _hashlib.sha256
    ).hexdigest()[:32]

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)
        os.environ.pop("ENV", None)
        actual = _sign_token("export-id", "all", 1234567890)

    assert actual != literal_dev_secret_output, (
        "The literal 'dev-secret' is being used as the signing key outside dev. "
        "Remove the 'dev-secret' fallback."
    )


def test_sign_token_dev_fallback_matches_dev_export_secret_constant():
    """Dev-mode fallback uses _DEV_EXPORT_SECRET (not 'dev-secret')."""
    import hashlib as _hashlib
    import hmac as _hmac
    import os

    expected = _hmac.new(
        _DEV_EXPORT_SECRET.encode(), b"export-id:contacts:1234567890", _hashlib.sha256
    ).hexdigest()[:32]

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("DASHBOARD_EXPORT_SECRET", None)
        os.environ.pop("ENV", None)
        actual = _sign_token("export-id", "contacts", 1234567890)

    assert actual == expected


# ---------------------------------------------------------------------------
# _encrypt_export / _decrypt_export unit tests
# ---------------------------------------------------------------------------


def test_encrypt_export_produces_non_deterministic_nonce():
    """Two calls to _encrypt_export produce different ciphertext (fresh nonce each time)."""
    plaintext = b"hello world"
    key = _DEV_EXPORT_ENCRYPTION_KEY
    c1 = _encrypt_export(plaintext, key=key)
    c2 = _encrypt_export(plaintext, key=key)
    assert c1 != c2, "ciphertexts must differ due to fresh nonce per call"


def test_encrypt_decrypt_round_trip():
    """_decrypt_export inverts _encrypt_export for arbitrary bytes."""
    key = bytes(range(32))  # deterministic test key
    plaintext = b"\x00\x01\x02" * 100
    encrypted = _encrypt_export(plaintext, key=key)
    assert len(encrypted) > len(plaintext)  # nonce + tag overhead
    recovered = _decrypt_export(encrypted, key=key)
    assert recovered == plaintext


def test_decrypt_export_rejects_short_blob():
    """_decrypt_export raises ValueError when blob is too short."""
    with pytest.raises(ValueError, match="too short"):
        _decrypt_export(b"\x00" * 10, key=_DEV_EXPORT_ENCRYPTION_KEY)


def test_decrypt_export_rejects_tampered_ciphertext():
    """_decrypt_export raises an error when ciphertext is tampered."""
    from cryptography.exceptions import InvalidTag

    key = _DEV_EXPORT_ENCRYPTION_KEY
    encrypted = _encrypt_export(b"secret data", key=key)
    tampered = bytearray(encrypted)
    tampered[-1] ^= 0xFF  # flip last byte of GCM tag
    with pytest.raises((InvalidTag, Exception)):
        _decrypt_export(bytes(tampered), key=key)


# ---------------------------------------------------------------------------
# Reconciliation: dashboard-permissions spec "Data Operations" (bu-9q1dx.8)
#
# These lock in the two spec guarantees the audit flagged as easy to silently
# regress:
#   1. "Encrypted export" — the served file is an encrypted blob, NOT plaintext
#      NDJSON / a plaintext ZIP, so the FE "AES-256-GCM encrypted" copy is true.
#   2. "Every export scope yields its real data" — a *known* scope MUST NOT
#      silently map to "no tables".
# ---------------------------------------------------------------------------


def test_every_known_scope_resolves_to_real_tables():
    """No known scope silently maps to 'no tables' (spec: a known scope MUST
    cover the data its name promises)."""
    assert len(_KNOWN_SCOPES) == 5, f"Expected 5 known scopes, got {len(_KNOWN_SCOPES)}"
    for scope in _KNOWN_SCOPES:
        resolved = _SCOPE_ALIASES.get(scope, scope)
        if resolved == "all":
            tables = [t for tbls in _SCOPE_MAP.values() for t in tbls]
        else:
            tables = _SCOPE_MAP[resolved]
        assert len(tables) >= 1, f"scope {scope!r} resolves to zero tables"


@pytest.mark.parametrize("scope", ["all", "memory", "audit", "config", "full"])
async def test_download_bytes_are_encrypted_not_plaintext(app, scope):
    """Each scope's download is an opaque encrypted blob — not plaintext NDJSON
    and not a plaintext ZIP — yet decrypts to the scope's real rows.

    A regression that served the raw NDJSON/ZIP (defeating the "encrypted"
    promise in the UI copy) would be caught here.
    """
    sentinel = "SENTINEL_PLAINTEXT_MARKER_permission.set"
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[{"id": 1, "action": sentinel, "actor": "owner"}])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db

    url = _make_download_url(f"test-enc-{scope}", scope)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url)

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    raw = resp.content
    # Not a plaintext ZIP archive (ZIP local-file magic is "PK\x03\x04").
    assert not raw.startswith(b"PK"), "served bytes look like a plaintext ZIP"
    # The plaintext sentinel must NOT appear in the served (encrypted) bytes.
    assert sentinel.encode() not in raw, "plaintext row content leaked into the export blob"
    # ...but once decrypted, the scope's real data is present.
    files = _decrypt_zip_content(raw)
    assert files, f"scope {scope!r} produced an empty archive"
    assert any(sentinel in content for content in files.values()), (
        f"scope {scope!r} decrypted archive missing the real row data"
    )

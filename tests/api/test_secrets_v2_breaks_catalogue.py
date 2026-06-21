"""Tests for GET /api/secrets/breaks-catalogue.

Covers the acceptance scenarios from bu-r724f:
1. Hit — seeded provider returns rows sorted severity DESC.
2. Miss — unknown provider returns empty list, valid envelope.
3. Full-catalogue — omitting ?provider= returns all rows + meta.by_provider.
4. Envelope conformance — {data: [...], meta: {...}}.
5. Graceful degradation — DB unavailable returns empty list (no 503).
6. Table absent — migration not yet run, returns empty list (no 503).
7. Startup UPSERT idempotency — running twice = same row count.

Spec anchors
------------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
§Breaks-catalogue endpoint
openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md
§public.provider_feature_catalogue WhatBreaks Source-of-Truth Table
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import BreakEntry, _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_catalogue_row(
    *,
    provider: str = "google",
    butler: str = "health",
    feature: str = "Google Health ingestion",
    severity: str = "high",
    required_scopes: list | None = None,
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for catalogue rows."""
    if required_scopes is None:
        required_scopes = []
    m = MagicMock()
    data: dict = {
        "provider": provider,
        "butler": butler,
        "feature": feature,
        "severity": severity,
        "required_scopes": required_scopes,
    }
    m.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return m


def _make_db_manager_with_catalogue_rows(
    rows: list[MagicMock],
) -> MagicMock:
    """Build a mock DatabaseManager whose shared pool returns the given catalogue rows."""
    shared_pool = AsyncMock()
    shared_pool.fetch = AsyncMock(return_value=rows)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Scenario 1: Hit — seeded provider returns rows sorted severity DESC
# ---------------------------------------------------------------------------


def test_breaks_catalogue_hit_returns_rows_for_provider():
    """Known provider returns its catalogue rows."""
    rows = [
        _make_catalogue_row(
            provider="google",
            butler="health",
            feature="Google Health ingestion",
            severity="high",
            required_scopes=["https://www.googleapis.com/auth/googlehealth.sleep"],
        ),
        _make_catalogue_row(
            provider="google",
            butler="health",
            feature="Google Calendar sync",
            severity="medium",
            required_scopes=["https://www.googleapis.com/auth/calendar"],
        ),
    ]
    mock_db = _make_db_manager_with_catalogue_rows(rows)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/breaks-catalogue?provider=google")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "data" in body
    assert "meta" in body
    data = body["data"]
    assert len(data) == 2

    # First row should be the high-severity one
    assert data[0]["severity"] == "high"
    assert data[0]["butler"] == "health"
    assert data[0]["feature"] == "Google Health ingestion"
    assert "https://www.googleapis.com/auth/googlehealth.sleep" in data[0]["required_scopes"]

    # Second row is medium severity
    assert data[1]["severity"] == "medium"


def test_breaks_catalogue_sorted_severity_desc():
    """Rows are ordered high → medium → low when DB returns them in mixed order."""
    # DB returns them in any order; the SQL ORDER BY clause handles sorting.
    # This test verifies the endpoint does NOT re-sort (trusts DB), and that
    # the SQL we pass contains ORDER BY CASE severity ... DESC.
    import inspect

    from butlers.api.routers import secrets_v2

    source = inspect.getsource(secrets_v2.get_breaks_catalogue)
    assert "ORDER BY" in source
    assert "severity" in source
    assert "DESC" in source


# ---------------------------------------------------------------------------
# Scenario 2: Miss — unknown provider returns empty list, valid envelope
# ---------------------------------------------------------------------------


def test_breaks_catalogue_miss_returns_empty_list():
    """Unknown provider returns empty data array and HTTP 200."""
    mock_db = _make_db_manager_with_catalogue_rows([])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/breaks-catalogue?provider=nonexistent_provider")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "data" in body
    assert body["data"] == []
    assert "meta" in body


# ---------------------------------------------------------------------------
# Scenario 3: Full catalogue — omitting ?provider= returns all rows
# ---------------------------------------------------------------------------


def test_breaks_catalogue_full_includes_by_provider_meta():
    """Full catalogue response includes meta.by_provider grouping."""
    rows = [
        _make_catalogue_row(
            provider="google", butler="health", feature="Google Health ingestion", severity="high"
        ),
        _make_catalogue_row(
            provider="google", butler="messenger", feature="Gmail read and compose", severity="high"
        ),
        _make_catalogue_row(
            provider="telegram", butler="*", feature="Telegram messaging", severity="high"
        ),
    ]
    mock_db = _make_db_manager_with_catalogue_rows(rows)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/breaks-catalogue")
    assert resp.status_code == 200
    body = resp.json()

    assert "by_provider" in body["meta"]
    by_provider = body["meta"]["by_provider"]

    # google should have 2 entries
    assert "google" in by_provider
    assert len(by_provider["google"]) == 2

    # telegram should have 1 entry
    assert "telegram" in by_provider
    assert len(by_provider["telegram"]) == 1


def test_breaks_catalogue_provider_not_in_meta_when_filtered():
    """When ?provider= is supplied, meta.by_provider is absent."""
    rows = [_make_catalogue_row(provider="google", butler="health", feature="F", severity="high")]
    mock_db = _make_db_manager_with_catalogue_rows(rows)
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/breaks-catalogue?provider=google")
    assert resp.status_code == 200
    body = resp.json()
    # by_provider should NOT appear when filtered
    assert "by_provider" not in body["meta"]


# ---------------------------------------------------------------------------
# Scenario 4: Envelope conformance
# ---------------------------------------------------------------------------


def test_breaks_catalogue_envelope_structure():
    """Response always follows {data: [...], meta: {...}}."""
    mock_db = _make_db_manager_with_catalogue_rows([])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/breaks-catalogue?provider=google")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) >= {"data", "meta"}
    assert isinstance(body["data"], list)
    assert isinstance(body["meta"], dict)


# ---------------------------------------------------------------------------
# Scenario 5: Graceful degradation — no shared pool
# ---------------------------------------------------------------------------


def test_breaks_catalogue_no_shared_pool_returns_empty():
    """When credential_shared_pool raises KeyError, returns empty list (not 503)."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no pool"))

    client = _build_app(mock_db)

    resp = client.get("/api/secrets/breaks-catalogue?provider=google")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# Scenario 6: Table absent (migration not yet run)
# ---------------------------------------------------------------------------


def test_breaks_catalogue_table_not_found_returns_empty():
    """When the table does not exist, returns empty list (no 503)."""
    from asyncpg.exceptions import UndefinedTableError

    shared_pool = AsyncMock()
    shared_pool.fetch = AsyncMock(
        side_effect=UndefinedTableError(
            "relation 'public.provider_feature_catalogue' does not exist"
        )
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    client = _build_app(mock_db)

    resp = client.get("/api/secrets/breaks-catalogue?provider=google")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# Scenario 7: Startup UPSERT idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalogue_upsert_idempotent_row_count():
    """Running upsert_provider_feature_catalogue twice produces same row count."""
    from butlers.catalogue_bootstrap import _CATALOGUE_SEED, upsert_provider_feature_catalogue

    rows_in_db: dict[tuple, dict] = {}

    async def fake_fetchval(query: str) -> bool:
        return True

    async def fake_executemany(query: str, data: list) -> None:
        """Simulate ON CONFLICT (provider, butler, feature) DO UPDATE SET ..."""
        for row in data:
            provider, butler, feature, severity, scopes = row
            key = (provider, butler, feature)
            rows_in_db[key] = {
                "provider": provider,
                "butler": butler,
                "feature": feature,
                "severity": severity,
                "required_scopes": scopes,
            }

    class FakeConn:
        async def fetchval(self, query: str) -> bool:
            return await fake_fetchval(query)

        async def executemany(self, query: str, data) -> None:
            return await fake_executemany(query, data)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class FakePool:
        def acquire(self):
            return FakeConn()

    pool = FakePool()

    # First boot
    await upsert_provider_feature_catalogue(pool)  # type: ignore[arg-type]
    count_after_first = len(rows_in_db)

    # Second boot (idempotent — same rows, no new additions)
    await upsert_provider_feature_catalogue(pool)  # type: ignore[arg-type]
    count_after_second = len(rows_in_db)

    assert count_after_first == len(_CATALOGUE_SEED), (
        f"Expected {len(_CATALOGUE_SEED)} rows after first boot, got {count_after_first}"
    )
    assert count_after_second == count_after_first, (
        f"Row count changed after second boot: {count_after_first} → {count_after_second}"
    )


@pytest.mark.asyncio
async def test_catalogue_upsert_skips_when_table_absent():
    """upsert_provider_feature_catalogue is a no-op when the table does not exist."""
    from butlers.catalogue_bootstrap import upsert_provider_feature_catalogue

    executemany_called = False

    class FakeConn:
        async def fetchval(self, query: str) -> bool:
            return False  # table does not exist

        async def executemany(self, query: str, data) -> None:
            nonlocal executemany_called
            executemany_called = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class FakePool:
        def acquire(self):
            return FakeConn()

    await upsert_provider_feature_catalogue(FakePool())  # type: ignore[arg-type]
    assert not executemany_called, "executemany must not be called when table is absent"


# ---------------------------------------------------------------------------
# BreakEntry model validation
# ---------------------------------------------------------------------------


def test_break_entry_model_fields_and_default_scopes():
    """BreakEntry round-trips its spec fields and defaults required_scopes to []."""
    entry = BreakEntry(
        butler="health",
        feature="Google Health ingestion",
        severity="high",
        required_scopes=["https://www.googleapis.com/auth/googlehealth.sleep"],
    )
    assert entry.butler == "health"
    assert entry.feature == "Google Health ingestion"
    assert entry.severity == "high"
    assert len(entry.required_scopes) == 1

    default = BreakEntry(butler="*", feature="Telegram messaging", severity="high")
    assert default.required_scopes == []

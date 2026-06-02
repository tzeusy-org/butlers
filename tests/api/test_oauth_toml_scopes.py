"""Tests for butler.toml OAuth scope resolution.

Spec: "OAuth Per-Provider Generalisation §Provider scope resolution from butler.toml"
  - WHEN the OAuth begin endpoint is called for a provider whose scopes are
    declared in one or more butler.toml files
  - THEN the resolved scope-set is the union of all scopes declared by butlers
    that consume the provider
  - AND the resolved scope-set is the value passed to the OAuth authorization URL

Coverage:
  - collect_toml_scopes: returns union of scopes across multiple butler.toml files
  - collect_toml_scopes: returns empty list when no butler declares provider scopes
  - collect_toml_scopes: handles missing roster directory gracefully
  - collect_toml_scopes: deduplicates scopes (same scope in two butlers → appears once)
  - collect_toml_scopes: union is per-provider (other providers' scopes not included)
  - _compose_provider_default_scopes: uses toml scopes when present (override)
  - _compose_provider_default_scopes: falls back to default_scope_sets when no toml scopes
  - Integration: /api/oauth/{provider}/start uses toml-sourced scopes in auth URL
  - Integration: /api/oauth/{provider}/start falls back to hardcoded defaults when no toml
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from butlers.api.routers.oauth import (
    _PROVIDER_REGISTRY,
    _clear_toml_scope_cache,
    _compose_provider_default_scopes,
    collect_toml_scopes,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BUTLER_TOML_TEMPLATE = """\
[butler]
name = "{name}"
port = {port}

[butler.db]
name = "butlers"
"""

_OAUTH_SECTION_TEMPLATE = """\

[oauth.{provider}]
scopes = {scopes}
"""


def _write_butler_toml(directory: Path, name: str, port: int, oauth: dict) -> Path:
    """Write a butler.toml with optional [oauth.<provider>] sections."""
    content = _BUTLER_TOML_TEMPLATE.format(name=name, port=port)
    for provider, scopes in oauth.items():
        scopes_toml = "[" + ", ".join(f'"{s}"' for s in scopes) + "]"
        content += _OAUTH_SECTION_TEMPLATE.format(provider=provider, scopes=scopes_toml)
    butler_dir = directory / name
    butler_dir.mkdir(parents=True, exist_ok=True)
    (butler_dir / "butler.toml").write_text(content)
    return butler_dir


@pytest.fixture(autouse=True)
def clear_scope_cache():
    """Ensure a clean cache state before and after each test."""
    _clear_toml_scope_cache()
    yield
    _clear_toml_scope_cache()


# ===========================================================================
# collect_toml_scopes: unit tests
# ===========================================================================


def test_collect_toml_scopes_empty_roster(tmp_path: Path):
    """Empty roster returns empty list for any provider."""
    result = collect_toml_scopes("google", roster_dir=tmp_path)
    assert result == []


def test_collect_toml_scopes_missing_roster(tmp_path: Path):
    """Non-existent roster directory returns empty list without raising."""
    missing = tmp_path / "does_not_exist"
    result = collect_toml_scopes("google", roster_dir=missing)
    assert result == []


def test_collect_toml_scopes_no_oauth_section(tmp_path: Path):
    """Butler with no [oauth.*] section returns empty list for that provider."""
    _write_butler_toml(tmp_path, "general", 41100, oauth={})
    result = collect_toml_scopes("google", roster_dir=tmp_path)
    assert result == []


def test_collect_toml_scopes_single_butler(tmp_path: Path):
    """Single butler with [oauth.google] scopes returns those scopes."""
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar",
    ]
    _write_butler_toml(tmp_path, "general", 41100, oauth={"google": scopes})
    result = collect_toml_scopes("google", roster_dir=tmp_path)
    assert set(result) == set(scopes)
    assert len(result) == len(scopes)  # no duplicates


def test_collect_toml_scopes_union_across_butlers(tmp_path: Path):
    """Scopes from multiple butlers are unioned; each scope appears once."""
    _write_butler_toml(
        tmp_path,
        "email",
        41101,
        oauth={"google": ["https://www.googleapis.com/auth/gmail.readonly"]},
    )
    _write_butler_toml(
        tmp_path,
        "calendar",
        41102,
        oauth={"google": ["https://www.googleapis.com/auth/calendar"]},
    )
    result = collect_toml_scopes("google", roster_dir=tmp_path)
    assert "https://www.googleapis.com/auth/gmail.readonly" in result
    assert "https://www.googleapis.com/auth/calendar" in result
    assert len(result) == 2


def test_collect_toml_scopes_deduplicates_across_butlers(tmp_path: Path):
    """The same scope declared by two butlers appears only once in the result."""
    shared_scope = "https://www.googleapis.com/auth/gmail.readonly"
    _write_butler_toml(tmp_path, "email", 41101, oauth={"google": [shared_scope]})
    _write_butler_toml(tmp_path, "general", 41102, oauth={"google": [shared_scope]})
    result = collect_toml_scopes("google", roster_dir=tmp_path)
    assert result.count(shared_scope) == 1


def test_collect_toml_scopes_per_provider_isolation(tmp_path: Path):
    """Scopes declared for spotify do not bleed into google results."""
    _write_butler_toml(
        tmp_path,
        "music",
        41101,
        oauth={
            "google": ["https://www.googleapis.com/auth/calendar"],
            "spotify": ["user-read-recently-played", "user-top-read"],
        },
    )
    google_result = collect_toml_scopes("google", roster_dir=tmp_path)
    spotify_result = collect_toml_scopes("spotify", roster_dir=tmp_path)
    assert google_result == ["https://www.googleapis.com/auth/calendar"]
    assert set(spotify_result) == {"user-read-recently-played", "user-top-read"}
    # No cross-contamination.
    assert all("spotify" not in s for s in google_result)
    assert all("google" not in s for s in spotify_result)


def test_collect_toml_scopes_unknown_provider_returns_empty(tmp_path: Path):
    """A provider not declared in any butler.toml returns empty list."""
    _write_butler_toml(
        tmp_path, "general", 41101, oauth={"google": ["https://www.googleapis.com/auth/calendar"]}
    )
    result = collect_toml_scopes("github", roster_dir=tmp_path)
    assert result == []


def test_collect_toml_scopes_cache_is_populated(tmp_path: Path):
    """Second call returns same result without re-reading files (cache hit)."""
    scopes = ["https://www.googleapis.com/auth/calendar"]
    _write_butler_toml(tmp_path, "general", 41100, oauth={"google": scopes})
    result1 = collect_toml_scopes("google", roster_dir=tmp_path)
    # Corrupt the toml file after first read to verify cache is used.
    (tmp_path / "general" / "butler.toml").write_text("[invalid")
    result2 = collect_toml_scopes("google", roster_dir=tmp_path)
    assert result1 == result2


def test_clear_toml_scope_cache_forces_re_read(tmp_path: Path):
    """After clearing the cache, the next call re-reads butler.toml files."""
    scopes_v1 = ["https://www.googleapis.com/auth/calendar"]
    scopes_v2 = ["https://www.googleapis.com/auth/gmail.readonly"]
    _write_butler_toml(tmp_path, "general", 41100, oauth={"google": scopes_v1})
    result1 = collect_toml_scopes("google", roster_dir=tmp_path)
    assert result1 == scopes_v1

    # Rewrite the toml with a different scope list.
    _write_butler_toml(tmp_path, "general", 41100, oauth={"google": scopes_v2})
    # Without clearing cache, still returns v1.
    result_cached = collect_toml_scopes("google", roster_dir=tmp_path)
    assert result_cached == scopes_v1

    # After cache clear, picks up v2.
    _clear_toml_scope_cache()
    result_fresh = collect_toml_scopes("google", roster_dir=tmp_path)
    assert result_fresh == scopes_v2


# ===========================================================================
# _compose_provider_default_scopes: override vs. fallback
# ===========================================================================


def test_compose_provider_default_scopes_uses_toml_when_present(tmp_path: Path):
    """When butler.toml declares scopes for a provider, they replace the hardcoded defaults."""
    toml_scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar",
    ]
    _write_butler_toml(tmp_path, "general", 41100, oauth={"google": toml_scopes})
    provider_cfg = _PROVIDER_REGISTRY["google"]
    result = _compose_provider_default_scopes(provider_cfg, "google", roster_dir=tmp_path)
    scope_list = result.split()
    # All toml scopes are present.
    assert all(s in scope_list for s in toml_scopes)
    # The result is exactly the toml scopes (not the full hardcoded set).
    assert len(scope_list) == len(toml_scopes)


def test_compose_provider_default_scopes_fallback_when_no_toml(tmp_path: Path):
    """When no butler.toml declares scopes for google, hardcoded defaults are used."""
    provider_cfg = _PROVIDER_REGISTRY["google"]
    result = _compose_provider_default_scopes(provider_cfg, "google", roster_dir=tmp_path)
    # Should include scopes from the hardcoded default sets (gmail, calendar, etc.).
    assert "https://www.googleapis.com/auth/gmail" in result
    assert "https://www.googleapis.com/auth/calendar" in result


def test_compose_provider_default_scopes_fallback_spotify(tmp_path: Path):
    """When no butler.toml declares spotify scopes, hardcoded Spotify defaults are used."""
    provider_cfg = _PROVIDER_REGISTRY["spotify"]
    result = _compose_provider_default_scopes(provider_cfg, "spotify", roster_dir=tmp_path)
    # Spotify default is 'base' + 'listening_history':
    #   user-read-email, user-read-private (base)
    #   user-read-recently-played, user-top-read, user-read-playback-state (listening_history)
    # This matches the five scopes declared as required in oauth_scope_registry.py.
    assert "user-read-email" in result
    assert "user-read-private" in result
    assert "user-read-recently-played" in result
    assert "user-top-read" in result
    assert "user-read-playback-state" in result


def test_compose_provider_default_scopes_toml_overrides_spotify(tmp_path: Path):
    """butler.toml-declared spotify scopes replace the hardcoded 'base' defaults."""
    toml_scopes = ["user-read-recently-played", "user-top-read"]
    _write_butler_toml(tmp_path, "music", 41101, oauth={"spotify": toml_scopes})
    provider_cfg = _PROVIDER_REGISTRY["spotify"]
    result = _compose_provider_default_scopes(provider_cfg, "spotify", roster_dir=tmp_path)
    scope_list = result.split()
    assert set(scope_list) == set(toml_scopes)
    # Hardcoded defaults (user-read-email) should NOT be present.
    assert "user-read-email" not in scope_list


# ===========================================================================
# Integration: /api/oauth/{provider}/start uses toml-sourced scopes
# ===========================================================================


def _make_app_with_mocked_creds(app):
    """Wire the shared app with a mocked DB manager for OAuth tests."""
    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": "test-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
        "SPOTIFY_OAUTH_CLIENT_ID": "test-client-id",
        "SPOTIFY_OAUTH_CLIENT_SECRET": "test-secret",
    }
    conn = AsyncMock()

    async def _fetchrow(query, *args):
        key = args[0] if args else None
        value = secrets.get(key) if key else None
        return {"secret_value": value} if value else None

    conn.fetchrow.side_effect = _fetchrow
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    pool.fetchval = AsyncMock(return_value=None)
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool

    from butlers.api.routers import oauth as oauth_module

    app.dependency_overrides[oauth_module._get_db_manager] = lambda: db_manager
    return app


async def test_start_uses_toml_scopes_in_auth_url(app, tmp_path: Path):
    """When butler.toml declares spotify scopes they appear in the auth URL scope param."""
    toml_scopes = ["user-read-recently-played", "user-top-read"]
    _write_butler_toml(tmp_path, "music", 41101, oauth={"spotify": toml_scopes})

    # Patch collect_toml_scopes to use our temp roster_dir.
    import butlers.api.routers.oauth as oauth_module

    _original = oauth_module.collect_toml_scopes

    def _patched_collect(provider, roster_dir=None):
        return _original(provider, roster_dir=tmp_path)

    oauth_module.collect_toml_scopes = _patched_collect
    try:
        _make_app_with_mocked_creds(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/spotify/start", params={"redirect": "false"})
        assert resp.status_code == 200
        auth_url = resp.json()["data"]["authorization_url"]
        qs = parse_qs(urlparse(auth_url).query)
        scope_str = qs.get("scope", [""])[0]
        assert "user-read-recently-played" in scope_str
        assert "user-top-read" in scope_str
        # Hardcoded default (user-read-email) should NOT appear when toml overrides.
        assert "user-read-email" not in scope_str
    finally:
        oauth_module.collect_toml_scopes = _original
        _clear_toml_scope_cache()


async def test_start_falls_back_to_hardcoded_scopes_without_toml(app, tmp_path: Path):
    """When no butler.toml declares spotify scopes, hardcoded defaults remain in auth URL."""
    # Use tmp_path as roster_dir — it has no butler.toml files.
    import butlers.api.routers.oauth as oauth_module

    _original = oauth_module.collect_toml_scopes

    def _patched_collect(provider, roster_dir=None):
        return _original(provider, roster_dir=tmp_path)

    oauth_module.collect_toml_scopes = _patched_collect
    try:
        _make_app_with_mocked_creds(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/spotify/start", params={"redirect": "false"})
        assert resp.status_code == 200
        auth_url = resp.json()["data"]["authorization_url"]
        qs = parse_qs(urlparse(auth_url).query)
        scope_str = qs.get("scope", [""])[0]
        # Hardcoded Spotify defaults (base + listening_history) should be present.
        assert "user-read-email" in scope_str
        assert "user-read-private" in scope_str
        assert "user-read-recently-played" in scope_str
        assert "user-top-read" in scope_str
        assert "user-read-playback-state" in scope_str
    finally:
        oauth_module.collect_toml_scopes = _original
        _clear_toml_scope_cache()

"""Tests for butlers.core.qa.repo_whitelist.

Covers:
- parse_repo_url: HTTPS, SSH, bare owner/repo, invalid inputs
- RepoWhitelist.is_allowed: hit, miss, empty whitelist (fail-closed)
- RepoWhitelist.ensure_loaded: DB loading, fail-open on DB error
- URL variants passed directly to is_allowed
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.repo_whitelist import (
    RepoWhitelist,
    normalise_owner_repo,
    parse_repo_url,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# parse_repo_url
# ---------------------------------------------------------------------------


class TestParseRepoUrl:
    def test_https_with_dot_git(self):
        result = parse_repo_url("https://github.com/acme/my-repo.git")
        assert result == ("acme", "my-repo")

    def test_https_without_dot_git(self):
        result = parse_repo_url("https://github.com/acme/my-repo")
        assert result == ("acme", "my-repo")

    def test_https_with_trailing_slash(self):
        result = parse_repo_url("https://github.com/acme/my-repo/")
        assert result == ("acme", "my-repo")

    def test_ssh_with_dot_git(self):
        result = parse_repo_url("git@github.com:acme/my-repo.git")
        assert result == ("acme", "my-repo")

    def test_ssh_without_dot_git(self):
        result = parse_repo_url("git@github.com:acme/my-repo")
        assert result == ("acme", "my-repo")

    def test_bare_owner_repo(self):
        result = parse_repo_url("acme/my-repo")
        assert result == ("acme", "my-repo")

    def test_uppercase_normalised_to_lowercase(self):
        result = parse_repo_url("https://github.com/ACME/MyRepo.git")
        assert result == ("acme", "myrepo")

    def test_non_github_https(self):
        """Any HTTPS host should be parsed (host-agnostic)."""
        result = parse_repo_url("https://gitlab.com/org/project.git")
        assert result == ("org", "project")

    def test_non_github_ssh(self):
        result = parse_repo_url("git@gitlab.com:org/project.git")
        assert result == ("org", "project")

    def test_invalid_bare_path_returns_none(self):
        assert parse_repo_url("not-a-repo") is None

    def test_empty_string_returns_none(self):
        assert parse_repo_url("") is None

    def test_just_slash_returns_none(self):
        assert parse_repo_url("/") is None

    def test_numeric_names(self):
        result = parse_repo_url("org123/repo456")
        assert result == ("org123", "repo456")


# ---------------------------------------------------------------------------
# normalise_owner_repo
# ---------------------------------------------------------------------------


class TestNormaliseOwnerRepo:
    def test_lowercases(self):
        assert normalise_owner_repo("ACME", "Repo") == "acme/repo"

    def test_already_lower(self):
        assert normalise_owner_repo("acme", "repo") == "acme/repo"


# ---------------------------------------------------------------------------
# RepoWhitelist
# ---------------------------------------------------------------------------


def _make_mock_pool(rows: list[dict]) -> MagicMock:
    """Build a mock asyncpg pool returning the given rows."""
    mock_pool = MagicMock()

    class _Row(dict):
        def __getattr__(self, name):
            return self[name]

    mock_pool.fetch = AsyncMock(return_value=[_Row(r) for r in rows])
    return mock_pool


class TestRepoWhitelistIsAllowed:
    """Tests for RepoWhitelist.is_allowed (synchronous, no DB needed)."""

    def _make_loaded_whitelist(self, repos: list[str]) -> RepoWhitelist:
        wl = RepoWhitelist(db_pool=None)
        # Bypass DB — directly set internal state
        wl._allowed = frozenset(repos)
        wl._loaded = True
        import time

        wl._last_loaded_at = time.monotonic()
        return wl

    def test_allowed_exact_match(self):
        wl = self._make_loaded_whitelist(["acme/repo"])
        allowed, reason = wl.is_allowed("acme/repo")
        assert allowed is True
        assert reason == "allowed"

    def test_allowed_https_url(self):
        wl = self._make_loaded_whitelist(["acme/repo"])
        allowed, reason = wl.is_allowed("https://github.com/acme/repo.git")
        assert allowed is True

    def test_allowed_ssh_url(self):
        wl = self._make_loaded_whitelist(["acme/repo"])
        allowed, reason = wl.is_allowed("git@github.com:acme/repo.git")
        assert allowed is True

    def test_not_allowed_unknown_repo(self):
        wl = self._make_loaded_whitelist(["acme/repo"])
        allowed, reason = wl.is_allowed("acme/other-repo")
        assert allowed is False
        assert reason == "not_in_whitelist"

    def test_empty_whitelist_blocks_all(self):
        """An empty whitelist must block ALL repos (fail-closed)."""
        wl = self._make_loaded_whitelist([])
        allowed, reason = wl.is_allowed("acme/repo")
        assert allowed is False
        assert reason == "whitelist_empty"

    def test_no_db_pool_blocks_all(self):
        """Without a DB pool the whitelist cannot load — fail-closed."""
        wl = RepoWhitelist(db_pool=None)
        # Not yet loaded — ensure ensure_loaded is not called
        wl._loaded = True  # simulate failed initial load with empty set
        allowed, reason = wl.is_allowed("acme/repo")
        assert allowed is False
        assert reason == "whitelist_empty"

    def test_case_insensitive(self):
        """Lookup should be case-insensitive (stored lowercase)."""
        wl = self._make_loaded_whitelist(["acme/repo"])
        allowed, _ = wl.is_allowed("ACME/REPO")
        assert allowed is True

    def test_multiple_repos(self):
        wl = self._make_loaded_whitelist(["acme/a", "acme/b", "other/c"])
        assert wl.is_allowed("acme/a")[0] is True
        assert wl.is_allowed("acme/b")[0] is True
        assert wl.is_allowed("other/c")[0] is True
        assert wl.is_allowed("acme/z")[0] is False


class TestRepoWhitelistLoad:
    """Tests for RepoWhitelist DB loading behaviour."""

    @pytest.mark.asyncio
    async def test_ensure_loaded_populates_allowed(self):
        rows = [{"owner": "acme", "repo": "repo"}]
        pool = _make_mock_pool(rows)

        wl = RepoWhitelist(db_pool=pool)
        await wl.ensure_loaded()

        assert "acme/repo" in wl.get_allowed_repos()
        assert wl._loaded is True

    @pytest.mark.asyncio
    async def test_ensure_loaded_idempotent(self):
        """ensure_loaded should only query the DB once."""
        rows = [{"owner": "acme", "repo": "repo"}]
        pool = _make_mock_pool(rows)

        wl = RepoWhitelist(db_pool=pool)
        await wl.ensure_loaded()
        await wl.ensure_loaded()

        pool.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_db_error_fail_open(self):
        """On DB error during initial load, whitelist stays empty (loaded=True)."""
        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("DB unavailable"))

        wl = RepoWhitelist(db_pool=pool)
        await wl.ensure_loaded()

        # Loaded flag is set so we don't hammer the DB
        assert wl._loaded is True
        # Fail-open on error: whitelist remains as-is (empty from start)
        # is_allowed returns whitelist_empty because the set IS empty
        allowed, reason = wl.is_allowed("acme/repo")
        assert allowed is False
        assert reason == "whitelist_empty"

    @pytest.mark.asyncio
    async def test_no_pool_sets_loaded(self):
        """Without a pool, ensure_loaded sets loaded=True with empty whitelist."""
        wl = RepoWhitelist(db_pool=None)
        await wl.ensure_loaded()
        assert wl._loaded is True
        assert len(wl.get_allowed_repos()) == 0

    @pytest.mark.asyncio
    async def test_multiple_repos_loaded(self):
        rows = [
            {"owner": "acme", "repo": "alpha"},
            {"owner": "acme", "repo": "beta"},
            {"owner": "other", "repo": "gamma"},
        ]
        pool = _make_mock_pool(rows)

        wl = RepoWhitelist(db_pool=pool)
        await wl.ensure_loaded()

        allowed_set = wl.get_allowed_repos()
        assert "acme/alpha" in allowed_set
        assert "acme/beta" in allowed_set
        assert "other/gamma" in allowed_set
        assert len(allowed_set) == 3

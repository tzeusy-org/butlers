"""Tests for butlers.core.qa.repo_clone.ManagedRepoClone.

Covers:
- ensure_cloned: clones fresh when no local clone exists
- ensure_cloned: reuses the existing clone when its origin matches config
- ensure_cloned: discards and re-clones when the configured repo_url has
  changed since the clone was made (bu-vr6sz — a stale clone silently kept
  pushing to the old github.com/tzeusy/butlers URL after qa_repo_config was
  updated to github.com/tzeusy-org/butlers)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.repo_clone import ManagedRepoClone

pytestmark = pytest.mark.unit


def _pool(repo_url: str) -> MagicMock:
    p = MagicMock()
    p.fetchrow = AsyncMock(return_value={"repo_url": repo_url})
    p.execute = AsyncMock(return_value=None)
    return p


def _fake_subprocess(responses: list[tuple[bytes, bytes, int]]):
    """Return a create_subprocess_exec side_effect yielding *responses* in order."""
    call_index = 0

    async def _run(*_args, **_kwargs):
        nonlocal call_index
        stdout, stderr, rc = responses[min(call_index, len(responses) - 1)]
        call_index += 1
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
        proc.returncode = rc
        return proc

    return _run


@pytest.mark.asyncio
async def test_ensure_cloned_clones_fresh_when_missing(tmp_path):
    """No local clone yet: git clone is invoked with the configured repo_url."""
    clone_dir = tmp_path / "qa-repo"
    pool = _pool("https://github.com/tzeusy-org/butlers")
    clone = ManagedRepoClone(pool=pool, clone_dir=clone_dir)

    calls: list[tuple] = []

    async def _run(*args, **kwargs):
        calls.append(args)
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    with patch("butlers.core.qa.repo_clone.asyncio.create_subprocess_exec", side_effect=_run):
        result = await clone.ensure_cloned()

    assert result == clone_dir
    clone_call = next(c for c in calls if c[0] == "git" and c[1] == "clone")
    assert clone_call[-2] == "https://github.com/tzeusy-org/butlers"


@pytest.mark.asyncio
async def test_ensure_cloned_reuses_clone_when_origin_matches(tmp_path):
    """An existing clone whose origin matches config is reused, not re-cloned."""
    clone_dir = tmp_path / "qa-repo"
    (clone_dir / ".git").mkdir(parents=True)
    pool = _pool("https://github.com/tzeusy-org/butlers")
    clone = ManagedRepoClone(pool=pool, clone_dir=clone_dir)

    run = _fake_subprocess([(b"https://github.com/tzeusy-org/butlers\n", b"", 0)])

    with patch(
        "butlers.core.qa.repo_clone.asyncio.create_subprocess_exec", side_effect=run
    ) as mocked:
        result = await clone.ensure_cloned()

    assert result == clone_dir
    assert (clone_dir / ".git").is_dir()
    clone_calls = [c for c in mocked.call_args_list if c.args[1:2] == ("clone",)]
    assert clone_calls == []


@pytest.mark.asyncio
async def test_ensure_cloned_reclones_when_repo_url_changed(tmp_path):
    """A stale clone whose origin no longer matches config is discarded and re-cloned.

    Regression test for bu-vr6sz: updating qa_repo_config.repo_url alone left the
    daemon pushing to the old remote until the on-disk clone was deleted by hand.
    """
    clone_dir = tmp_path / "qa-repo"
    (clone_dir / ".git").mkdir(parents=True)
    (clone_dir / "stale-marker").write_text("old clone")

    pool = _pool("https://github.com/tzeusy-org/butlers")
    clone = ManagedRepoClone(pool=pool, clone_dir=clone_dir)

    run = _fake_subprocess(
        [
            (b"https://github.com/Tzeusy/butlers\n", b"", 0),  # git remote get-url origin
            (b"", b"", 0),  # git clone
        ]
    )

    with patch("butlers.core.qa.repo_clone.asyncio.create_subprocess_exec", side_effect=run):
        result = await clone.ensure_cloned()

    assert result == clone_dir
    assert not (clone_dir / "stale-marker").exists()

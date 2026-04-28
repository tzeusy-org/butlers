"""Tests for Codex auth.json rotation detection and credential-store sync.

Covers:
- _compute_file_fingerprint: returns (mtime_ns, sha256_hex) or None
- record_auth_baseline: records stable baseline after restore_tokens
- _has_rotated: detects mtime/content changes; no-file → False; no-baseline → True
- check_and_persist_rotation: calls persist_token on rotation; skips on unchanged;
  swallows all exceptions; updates cache after persist
- CodexAdapter._schedule_auth_sync: no-op when no store; fires task when store present
- CodexAdapter.invoke: schedules auth sync after subprocess success and failure
- CodexAdapter.create_worker: propagates credential_store and butler_name
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.runtimes._codex_auth_sync import (
    _AUTH_SYNC_CACHE,
    _compute_file_fingerprint,
    _has_rotated,
    check_and_persist_rotation,
    record_auth_baseline,
)
from butlers.core.runtimes.codex import CodexAdapter

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"
_PERSIST = "butlers.cli_auth.persistence.persist_token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_auth(path: Path, payload: dict | None = None) -> None:
    """Write a JSON auth file at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload or {"access_token": "tok-1"}), encoding="utf-8")


def _make_ok_stdout() -> bytes:
    """Minimal Codex stdout for a zero-exit invocation."""
    return json.dumps({"type": "result", "result": "ok"}).encode()


def _mock_store() -> MagicMock:
    """Return a minimal mock CredentialStore."""
    store = MagicMock()
    store.store = AsyncMock(return_value=None)
    return store


# ---------------------------------------------------------------------------
# _compute_file_fingerprint
# ---------------------------------------------------------------------------


def test_compute_fingerprint_returns_none_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_file.json"
    assert _compute_file_fingerprint(missing) is None


def test_compute_fingerprint_returns_tuple(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    _write_auth(auth)
    fp = _compute_file_fingerprint(auth)
    assert fp is not None
    mtime_ns, digest = fp
    assert isinstance(mtime_ns, int) and mtime_ns > 0
    assert len(digest) == 64  # sha256 hex


def test_compute_fingerprint_changes_after_write(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    _write_auth(auth, {"access_token": "v1"})
    fp1 = _compute_file_fingerprint(auth)

    # Overwrite with different content; force mtime to advance
    import time

    time.sleep(0.01)
    _write_auth(auth, {"access_token": "v2"})
    fp2 = _compute_file_fingerprint(auth)

    assert fp1 != fp2


# ---------------------------------------------------------------------------
# record_auth_baseline
# ---------------------------------------------------------------------------


def test_record_auth_baseline_populates_cache(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    _write_auth(auth)

    key = str(auth)
    _AUTH_SYNC_CACHE.pop(key, None)
    record_auth_baseline(auth)

    assert key in _AUTH_SYNC_CACHE
    assert _AUTH_SYNC_CACHE[key] == _compute_file_fingerprint(auth)


def test_record_auth_baseline_noop_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_file.json"
    key = str(missing)
    _AUTH_SYNC_CACHE.pop(key, None)
    record_auth_baseline(missing)
    assert key not in _AUTH_SYNC_CACHE


# ---------------------------------------------------------------------------
# _has_rotated
# ---------------------------------------------------------------------------


def test_has_rotated_true_when_no_baseline(tmp_path: Path) -> None:
    """No cached baseline → treat as rotated so we persist on first encounter."""
    auth = tmp_path / "auth.json"
    _write_auth(auth)
    _AUTH_SYNC_CACHE.pop(str(auth), None)
    assert _has_rotated(auth) is True


def test_has_rotated_false_when_file_missing(tmp_path: Path) -> None:
    """File absent → cannot persist; return False."""
    missing = tmp_path / "auth.json"
    _AUTH_SYNC_CACHE.pop(str(missing), None)
    assert _has_rotated(missing) is False


def test_has_rotated_false_when_unchanged(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    _write_auth(auth)
    record_auth_baseline(auth)
    assert _has_rotated(auth) is False


def test_has_rotated_true_after_content_change(tmp_path: Path) -> None:
    import time

    auth = tmp_path / "auth.json"
    _write_auth(auth, {"access_token": "old"})
    record_auth_baseline(auth)

    time.sleep(0.01)
    _write_auth(auth, {"access_token": "new"})

    assert _has_rotated(auth) is True


# ---------------------------------------------------------------------------
# check_and_persist_rotation
# ---------------------------------------------------------------------------


async def test_check_and_persist_calls_persist_token_on_rotation(tmp_path: Path) -> None:
    """When the file changed, persist_token is called and cache is updated."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "old"})
    # No baseline → _has_rotated returns True

    store = _mock_store()

    with patch(_PERSIST, new=AsyncMock(return_value=True)) as mock_persist:
        await check_and_persist_rotation(auth, store, butler_name="test-butler")

    mock_persist.assert_awaited_once()
    # Cache should now reflect the current fingerprint
    assert _AUTH_SYNC_CACHE.get(str(auth)) == _compute_file_fingerprint(auth)


async def test_check_and_persist_skips_when_unchanged(tmp_path: Path) -> None:
    """When file fingerprint matches the cache, persist_token is NOT called."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "stable"})
    record_auth_baseline(auth)

    store = _mock_store()

    with patch(_PERSIST, new=AsyncMock(return_value=True)) as mock_persist:
        # Call twice; neither should trigger persist because file is unchanged.
        await check_and_persist_rotation(auth, store, butler_name="qa")
        await check_and_persist_rotation(auth, store, butler_name="qa")

    mock_persist.assert_not_awaited()


async def test_check_and_persist_skips_when_file_absent(tmp_path: Path) -> None:
    """When the token file doesn't exist, persist_token is NOT called."""
    missing = tmp_path / ".codex" / "auth.json"
    _AUTH_SYNC_CACHE.pop(str(missing), None)

    store = _mock_store()

    with patch(_PERSIST, new=AsyncMock(return_value=True)) as mock_persist:
        await check_and_persist_rotation(missing, store, butler_name="qa")

    mock_persist.assert_not_awaited()


async def test_check_and_persist_swallows_persist_exception(tmp_path: Path) -> None:
    """Exception from persist_token must not propagate."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth)
    _AUTH_SYNC_CACHE.pop(str(auth), None)

    store = _mock_store()

    async def _boom(*_a, **_k):
        raise RuntimeError("DB is down")

    with patch(_PERSIST, side_effect=_boom):
        # Must not raise
        await check_and_persist_rotation(auth, store, butler_name="qa")


async def test_check_and_persist_cache_updated_after_persist(tmp_path: Path) -> None:
    """After a successful persist, the cache is updated to the new fingerprint."""
    import time

    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "v1"})
    record_auth_baseline(auth)

    # Mutate the file to look like a rotation
    time.sleep(0.01)
    _write_auth(auth, {"access_token": "v2"})

    store = _mock_store()

    with patch(_PERSIST, new=AsyncMock(return_value=True)):
        await check_and_persist_rotation(auth, store, butler_name="qa")

    # Cache should now reflect v2 fingerprint; next call should NOT re-persist
    with patch(_PERSIST, new=AsyncMock(return_value=True)) as mock_no_call:
        await check_and_persist_rotation(auth, store, butler_name="qa")
    mock_no_call.assert_not_awaited()


async def test_check_and_persist_does_not_update_cache_when_persist_fails(
    tmp_path: Path,
) -> None:
    """When persist_token returns False, the cache is NOT updated so the next
    invocation will retry."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "v1"})
    _AUTH_SYNC_CACHE.pop(str(auth), None)  # no baseline → rotated

    store = _mock_store()

    with patch(_PERSIST, new=AsyncMock(return_value=False)):
        await check_and_persist_rotation(auth, store, butler_name="qa")

    # Cache should still be absent (or stale); next call should retry persist.
    assert _AUTH_SYNC_CACHE.get(str(auth)) is None or _has_rotated(auth)


# ---------------------------------------------------------------------------
# CodexAdapter._schedule_auth_sync
# ---------------------------------------------------------------------------


async def test_schedule_auth_sync_noop_when_no_store(tmp_path: Path) -> None:
    """No credential store → no asyncio task scheduled."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    assert adapter._credential_store is None

    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth)

    tasks_before = len(asyncio.all_tasks())
    adapter._schedule_auth_sync(auth)
    # Give the event loop a chance to run any scheduled tasks
    await asyncio.sleep(0)
    tasks_after = len(asyncio.all_tasks())
    assert tasks_after == tasks_before


async def test_schedule_auth_sync_noop_when_no_token_path() -> None:
    """No token path → no asyncio task scheduled even with a store wired."""
    store = _mock_store()
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store)

    tasks_before = len(asyncio.all_tasks())
    adapter._schedule_auth_sync(None)
    await asyncio.sleep(0)
    tasks_after = len(asyncio.all_tasks())
    assert tasks_after == tasks_before


async def test_schedule_auth_sync_fires_task_when_store_present(tmp_path: Path) -> None:
    """With a store and a token path, a background task is scheduled."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth)
    _AUTH_SYNC_CACHE.pop(str(auth), None)

    store = _mock_store()
    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )

    persist_calls: list[tuple] = []

    async def _fake_persist(provider, s):
        persist_calls.append((provider.name, str(provider.token_path)))
        return True

    with patch(_PERSIST, side_effect=_fake_persist):
        adapter._schedule_auth_sync(auth)
        # Yield so the task runs
        await asyncio.sleep(0.1)

    assert len(persist_calls) == 1
    assert persist_calls[0][0] == "codex"


# ---------------------------------------------------------------------------
# CodexAdapter.invoke: auth sync is scheduled after subprocess success/failure
# ---------------------------------------------------------------------------


async def test_invoke_schedules_auth_sync_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful invocation, the auth sync task is scheduled."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    auth = codex_dir / "auth.json"
    _write_auth(auth)
    monkeypatch.setenv("HOME", str(tmp_path))

    store = _mock_store()
    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    mock_proc.returncode = 0

    sync_calls: list[Path] = []

    async def _fake_sync(path, s, *, butler_name=""):
        sync_calls.append(path)

    with (
        patch(_EXEC, return_value=mock_proc),
        patch(
            "butlers.core.runtimes._codex_auth_sync.check_and_persist_rotation",
            side_effect=_fake_sync,
        ),
    ):
        await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.05)  # let background task run

    assert len(sync_calls) == 1
    assert sync_calls[0] == auth


async def test_invoke_schedules_auth_sync_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even on a failed invocation the auth sync task is scheduled."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    auth = codex_dir / "auth.json"
    _write_auth(auth)
    monkeypatch.setenv("HOME", str(tmp_path))

    store = _mock_store()
    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: something bad"))
    mock_proc.returncode = 1

    sync_calls: list[Path] = []

    async def _fake_sync(path, s, *, butler_name=""):
        sync_calls.append(path)

    with (
        patch(_EXEC, return_value=mock_proc),
        patch(
            "butlers.core.runtimes._codex_auth_sync.check_and_persist_rotation",
            side_effect=_fake_sync,
        ),
    ):
        with pytest.raises(RuntimeError):
            await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.05)

    assert len(sync_calls) == 1


async def test_invoke_no_auth_sync_without_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a credential store, no sync task is created."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    _write_auth(codex_dir / "auth.json")
    monkeypatch.setenv("HOME", str(tmp_path))

    adapter = CodexAdapter(codex_binary="/usr/bin/codex")  # no store

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    mock_proc.returncode = 0

    sync_calls: list[Path] = []

    async def _fake_sync(path, s, *, butler_name=""):
        sync_calls.append(path)

    with (
        patch(_EXEC, return_value=mock_proc),
        patch(
            "butlers.core.runtimes._codex_auth_sync.check_and_persist_rotation",
            side_effect=_fake_sync,
        ),
    ):
        await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.05)

    assert len(sync_calls) == 0


# ---------------------------------------------------------------------------
# Integration: two sequential invocations where auth.json mutates on second
# ---------------------------------------------------------------------------


async def test_two_invocations_only_persist_on_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First invocation: no prior baseline → persist.
    Second invocation: same file → no persist.
    Third invocation: file mutated → persist again.
    """
    import time

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    auth = codex_dir / "auth.json"
    _write_auth(auth, {"access_token": "v1"})
    # Clear any leftover cache entry
    _AUTH_SYNC_CACHE.pop(str(auth), None)
    monkeypatch.setenv("HOME", str(tmp_path))

    store = _mock_store()
    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )

    persist_calls: list[str] = []

    async def _fake_persist(provider, s):
        persist_calls.append(provider.name)
        return True

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc), patch(_PERSIST, side_effect=_fake_persist):
        # First invocation: no baseline → should persist
        await adapter.invoke(prompt="a", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.05)
        assert len(persist_calls) == 1, "Expected persist on first call (no baseline)"

        # Second invocation: file unchanged → should NOT persist
        mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
        await adapter.invoke(prompt="b", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.05)
        assert len(persist_calls) == 1, "Unchanged file should not trigger second persist"

        # Mutate the auth.json to simulate a token rotation
        time.sleep(0.01)
        _write_auth(auth, {"access_token": "v2"})

        # Third invocation: file changed → should persist
        mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
        await adapter.invoke(prompt="c", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.05)
        assert len(persist_calls) == 2, "Mutated file should trigger third persist"


# ---------------------------------------------------------------------------
# CodexAdapter.create_worker propagates credential_store and butler_name
# ---------------------------------------------------------------------------


def test_create_worker_propagates_credential_store_and_butler_name() -> None:
    store = _mock_store()
    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="chronicler",
    )
    worker = adapter.create_worker()
    assert isinstance(worker, CodexAdapter)
    assert worker._credential_store is store
    assert worker._butler_name == "chronicler"
    assert worker._codex_binary == "/usr/bin/codex"


# ---------------------------------------------------------------------------
# Per-test cache cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_auth_sync_cache():
    """Reset _AUTH_SYNC_CACHE before and after each test."""
    _AUTH_SYNC_CACHE.clear()
    yield
    _AUTH_SYNC_CACHE.clear()

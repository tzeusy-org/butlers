"""Tests for Codex auth.json rotation detection and credential-store sync.

Covers:
- _compute_file_fingerprint: returns (mtime_ns, sha256_hex) or None
- record_auth_baseline: records stable baseline after restore_tokens
- _has_rotated: detects mtime/content changes; no-file → False; no-baseline → True
- reconciliation: uses the authoritative shared Codex credential, writes safely,
  and never overwrites a newer dashboard refresh with a stale local rotation
- check_and_persist_rotation: conditionally persists a CLI rotation from the
  authority snapshot captured before its subprocess launched
- finalize_codex_auth_rotation: operation-bound persistence and conflict recovery
- CodexAdapter.invoke: revalidates at spawn and finalizes once after all attempts
- CodexAdapter.create_worker: propagates credential_store and butler_name
- _looks_like_auth_refresh_failure: positive + negative matcher tests
- CredentialStore.record_test_result: updates last_test_ok/last_verified/last_test_message
- CodexAdapter._schedule_record_test_result: fire-and-forget; no-op without store
- CodexAdapter.invoke: writes last_test_ok=false on refresh-reuse error (regression)
- CodexAdapter.invoke: writes last_test_ok=true on success (clear-on-success)
- CodexAdapter.invoke: does NOT write last_test_ok for unrelated exit-1 errors
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.runtimes._codex_auth_sync import (
    _AUTH_AUTHORITY_CACHE,
    _AUTH_SYNC_CACHE,
    _AUTH_SYNC_LOCKS,
    CodexAuthSyncResult,
    _compute_file_fingerprint,
    _has_rotated,
    check_and_persist_rotation,
    finalize_codex_auth_rotation,
    reconcile_codex_auth,
    record_auth_baseline,
)
from butlers.core.runtimes.codex import (
    _CODEX_REFRESH_TOKEN_REUSED_MARKER,
    CodexAdapter,
    _looks_like_auth_refresh_failure,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"


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


def _mock_store(*, shared: bool = False) -> MagicMock:
    """Return a minimal mock CredentialStore."""
    store = MagicMock()
    store.shared_pool = MagicMock() if shared else None
    store.store = AsyncMock(return_value=None)
    store.store_shared = AsyncMock(return_value=True)
    store.store_shared_if_unchanged = AsyncMock(return_value=True)
    store.load = AsyncMock(return_value=None)
    store.load_shared = AsyncMock(return_value=None)
    store.record_test_result = AsyncMock(return_value=None)
    store.record_test_result_if_unchanged = AsyncMock(return_value=True)
    return store


@pytest.fixture(autouse=True)
def _clear_auth_sync_state() -> None:
    """Keep process-global auth reconciliation state isolated between tests."""
    _AUTH_SYNC_CACHE.clear()
    _AUTH_AUTHORITY_CACHE.clear()
    _AUTH_SYNC_LOCKS.clear()
    yield
    _AUTH_SYNC_CACHE.clear()
    _AUTH_AUTHORITY_CACHE.clear()
    _AUTH_SYNC_LOCKS.clear()


# ---------------------------------------------------------------------------
# _compute_file_fingerprint
# ---------------------------------------------------------------------------


def test_compute_fingerprint_tuple_or_none(tmp_path: Path) -> None:
    """Existing file → (mtime_ns, sha256 hex) tuple; missing file → None."""
    assert _compute_file_fingerprint(tmp_path / "no_such_file.json") is None

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


def test_compute_fingerprint_detects_tail_change_with_preserved_mtime(tmp_path: Path) -> None:
    """Fingerprinting covers the full document, not only an auth-file prefix."""
    auth = tmp_path / "auth.json"
    first = b"{" + b'"padding":"' + (b"a" * 5000) + b'", "token":"old"}'
    second = first.replace(b'"token":"old"', b'"token":"new"')
    auth.write_bytes(first)
    before = auth.stat().st_mtime_ns
    fp1 = _compute_file_fingerprint(auth)

    auth.write_bytes(second)
    os.utime(auth, ns=(before, before))

    assert _compute_file_fingerprint(auth) != fp1


def test_atomic_writer_failure_preserves_target_and_cleans_temp_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed replace cannot leave a partial auth file or a reusable temp credential."""
    from butlers.cli_auth.persistence import _write_token_file_atomically

    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "old-token"})

    def _raise_replace(*_args, **_kwargs) -> None:
        raise OSError("replace failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            "butlers.cli_auth.persistence.os.replace",
            _raise_replace,
        )
        with pytest.raises(OSError, match="replace failed"):
            _write_token_file_atomically(auth, json.dumps({"access_token": "new-token"}))

    assert json.loads(auth.read_text(encoding="utf-8")) == {"access_token": "old-token"}
    assert not list(auth.parent.glob(".auth.json.*"))


# ---------------------------------------------------------------------------
# record_auth_baseline
# ---------------------------------------------------------------------------


def test_record_auth_baseline_populates_cache_and_skips_missing(tmp_path: Path) -> None:
    """Existing file seeds the cache with its fingerprint; missing file is a no-op."""
    auth = tmp_path / "auth.json"
    _write_auth(auth)
    key = str(auth)
    _AUTH_SYNC_CACHE.pop(key, None)
    record_auth_baseline(auth)
    assert key in _AUTH_SYNC_CACHE
    assert _AUTH_SYNC_CACHE[key] == _compute_file_fingerprint(auth)

    missing = tmp_path / "no_such_file.json"
    missing_key = str(missing)
    _AUTH_SYNC_CACHE.pop(missing_key, None)
    record_auth_baseline(missing)
    assert missing_key not in _AUTH_SYNC_CACHE


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


async def test_check_and_persist_conditionally_stores_rotation(tmp_path: Path) -> None:
    """A rotation updates its captured authority snapshot exactly once."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "old"})
    record_auth_baseline(auth)
    _write_auth(auth, {"access_token": "rotated"})

    store = _mock_store(shared=True)
    expected = json.dumps({"access_token": "old"})

    await check_and_persist_rotation(
        auth,
        store,
        expected_store_value=expected,
        butler_name="test-butler",
    )

    store.store_shared_if_unchanged.assert_awaited_once_with(
        "cli-auth/codex",
        json.dumps({"access_token": "rotated"}),
        expected_value=expected,
        category="cli-auth",
        description="CLI auth token for Codex (OpenAI)",
        is_sensitive=True,
    )
    # Cache should now reflect the current fingerprint
    assert _AUTH_SYNC_CACHE.get(str(auth)) == _compute_file_fingerprint(auth)


async def test_check_and_persist_skips_when_unchanged(tmp_path: Path) -> None:
    """When file fingerprint matches the cache, no conditional write occurs."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "stable"})
    record_auth_baseline(auth)

    store = _mock_store(shared=True)
    expected = json.dumps({"access_token": "stable"})

    # Call twice; neither should trigger a persistence because the file is unchanged.
    await check_and_persist_rotation(auth, store, expected_store_value=expected, butler_name="qa")
    await check_and_persist_rotation(auth, store, expected_store_value=expected, butler_name="qa")

    store.store_shared_if_unchanged.assert_not_awaited()


async def test_check_and_persist_skips_when_file_absent(tmp_path: Path) -> None:
    """When the token file doesn't exist, no conditional write occurs."""
    missing = tmp_path / ".codex" / "auth.json"
    _AUTH_SYNC_CACHE.pop(str(missing), None)

    store = _mock_store(shared=True)

    await check_and_persist_rotation(
        missing,
        store,
        expected_store_value=json.dumps({"access_token": "old"}),
        butler_name="qa",
    )

    store.store_shared_if_unchanged.assert_not_awaited()


async def test_check_and_persist_swallows_persist_exception(tmp_path: Path) -> None:
    """Exception from a conditional write must not propagate."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth)
    record_auth_baseline(auth)
    _write_auth(auth, {"access_token": "rotated"})

    store = _mock_store(shared=True)

    store.store_shared_if_unchanged.side_effect = RuntimeError("DB is down")

    # Must not raise.
    await check_and_persist_rotation(
        auth,
        store,
        expected_store_value=json.dumps({"access_token": "tok-1"}),
        butler_name="qa",
    )


async def test_check_and_persist_does_not_update_cache_when_persist_fails(
    tmp_path: Path,
) -> None:
    """When the conditional write loses its compare-and-set, cache is not updated."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "old"})
    record_auth_baseline(auth)
    _write_auth(auth, {"access_token": "rotated"})

    store = _mock_store(shared=True)
    store.store_shared_if_unchanged.return_value = False

    await check_and_persist_rotation(
        auth,
        store,
        expected_store_value=json.dumps({"access_token": "old"}),
        butler_name="qa",
    )

    assert _has_rotated(auth)


async def test_check_and_persist_does_not_overwrite_new_dashboard_value(tmp_path: Path) -> None:
    """A post-launch old rotation loses when the dashboard already refreshed DB auth."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "old"})
    record_auth_baseline(auth)
    _write_auth(auth, {"access_token": "old-session-rotation"})
    store = _mock_store(shared=True)
    store.store_shared_if_unchanged.return_value = False

    await check_and_persist_rotation(
        auth,
        store,
        expected_store_value=json.dumps({"access_token": "old"}),
        butler_name="qa",
    )

    # A failed CAS never writes the stale session value back over the dashboard row.
    store.store_shared_if_unchanged.assert_awaited_once()
    assert _has_rotated(auth)


# ---------------------------------------------------------------------------
# reconcile_codex_auth
# ---------------------------------------------------------------------------


async def test_persist_codex_token_bypasses_schema_local_store_when_shared_exists(
    tmp_path: Path,
) -> None:
    """A runtime rotation cannot create the local shadow that hides dashboard refreshes."""
    from dataclasses import replace

    from butlers.cli_auth.persistence import persist_token
    from butlers.cli_auth.registry import PROVIDERS

    token_path = tmp_path / ".codex" / "auth.json"
    _write_auth(token_path, {"access_token": "rotated-token"})
    provider = replace(PROVIDERS["codex"], token_path=token_path)
    store = _mock_store(shared=True)

    assert await persist_token(provider, store) is True

    store.store_shared.assert_awaited_once()
    store.store.assert_not_awaited()


async def test_reconcile_codex_auth_writes_changed_store_value(tmp_path: Path) -> None:
    """A dashboard-stored token replaces stale daemon auth before a launch."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "old-token"})
    store = _mock_store(shared=True)
    stored = json.dumps({"access_token": "new-token"})
    store.load_shared = AsyncMock(return_value=stored)

    result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result == CodexAuthSyncResult(expected_store_value=stored, file_changed=True)
    assert auth.read_text(encoding="utf-8") == stored
    assert stat.S_IMODE(auth.stat().st_mode) == 0o600
    assert _AUTH_SYNC_CACHE[str(auth)] == _compute_file_fingerprint(auth)
    assert store.load_shared.await_count == 2
    store.load_shared.assert_awaited_with("cli-auth/codex")
    store.load.assert_not_awaited()


async def test_reconcile_unknown_post_crash_local_state_conservatively_uses_shared_authority(
    tmp_path: Path,
) -> None:
    """Without a launch baseline, DB wins rather than guessing a local successor."""
    auth = tmp_path / ".codex" / "auth.json"
    orphaned_local = json.dumps({"access_token": "orphaned-rotation"})
    shared_authority = json.dumps({"access_token": "dashboard-authority"})
    auth.parent.mkdir(parents=True)
    auth.write_text(orphaned_local, encoding="utf-8")
    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(return_value=shared_authority)

    result = await reconcile_codex_auth(auth, store, butler_name="fresh-process")

    assert result.expected_store_value == shared_authority
    assert auth.read_text(encoding="utf-8") == shared_authority
    store.store_shared_if_unchanged.assert_not_awaited()


async def test_reconcile_codex_auth_keeps_matching_file_unchanged(tmp_path: Path) -> None:
    """Matching DB and local auth does not churn the canonical file."""
    auth = tmp_path / ".codex" / "auth.json"
    stored = json.dumps({"access_token": "stable-token"})
    auth.parent.mkdir(parents=True)
    auth.write_text(stored, encoding="utf-8")
    auth.chmod(0o600)
    before_mtime = auth.stat().st_mtime_ns
    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(return_value=stored)

    result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result == CodexAuthSyncResult(expected_store_value=stored, file_changed=False)
    assert auth.stat().st_mtime_ns == before_mtime
    assert _AUTH_SYNC_CACHE[str(auth)] == _compute_file_fingerprint(auth)


async def test_reconcile_codex_auth_store_failure_is_safe_and_nonfatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A credential-store failure preserves local auth without leaking it."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "existing-test-token"})
    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(
        side_effect=RuntimeError("credential store unavailable dashboard-secret-token")
    )

    with caplog.at_level(logging.WARNING, logger="butlers.core.runtimes._codex_auth_sync"):
        result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result == CodexAuthSyncResult(
        expected_store_value=None,
        file_changed=False,
        authority_known=False,
    )
    assert json.loads(auth.read_text(encoding="utf-8")) == {"access_token": "existing-test-token"}
    assert "existing-test-token" not in caplog.text
    assert "dashboard-secret-token" not in caplog.text


async def test_reconcile_codex_auth_store_timeout_is_safe_and_bounded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A stalled authority read preserves local auth without using session time."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "existing-test-token"})
    store = _mock_store(shared=True)
    never = asyncio.Event()

    async def _hang(_key: str) -> str:
        await never.wait()
        raise AssertionError("unreachable")

    store.load_shared = AsyncMock(side_effect=_hang)

    with (
        patch(
            "butlers.core.runtimes._codex_auth_sync._AUTH_STORE_OPERATION_TIMEOUT_S",
            0.01,
        ),
        caplog.at_level(logging.WARNING, logger="butlers.core.runtimes._codex_auth_sync"),
    ):
        result = await asyncio.wait_for(
            reconcile_codex_auth(auth, store, butler_name="qa"),
            timeout=0.2,
        )

    assert result == CodexAuthSyncResult(
        expected_store_value=None,
        file_changed=False,
        authority_known=False,
    )
    assert json.loads(auth.read_text(encoding="utf-8")) == {"access_token": "existing-test-token"}
    assert "existing-test-token" not in caplog.text


async def test_finalize_codex_auth_store_timeout_preserves_local_rotation(tmp_path: Path) -> None:
    """A stalled conditional write leaves a completed rotation local and non-fatal."""
    auth = tmp_path / ".codex" / "auth.json"
    original = json.dumps({"access_token": "old"})
    rotated = json.dumps({"access_token": "rotated"})
    auth.parent.mkdir(parents=True)
    auth.write_text(rotated, encoding="utf-8")
    store = _mock_store(shared=True)
    never = asyncio.Event()

    async def _hang(*_args, **_kwargs) -> bool:
        await never.wait()
        raise AssertionError("unreachable")

    store.store_shared_if_unchanged = AsyncMock(side_effect=_hang)

    with patch(
        "butlers.core.runtimes._codex_auth_sync._AUTH_STORE_OPERATION_TIMEOUT_S",
        0.01,
    ):
        result = await asyncio.wait_for(
            finalize_codex_auth_rotation(
                auth,
                store,
                expected_store_value=original,
                butler_name="qa",
            ),
            timeout=0.2,
        )

    assert result.authority_known is False
    assert auth.read_text(encoding="utf-8") == rotated
    store.store_shared_if_unchanged.assert_awaited_once()


async def test_adapter_auth_sync_timeout_covers_queued_reconciliation(tmp_path: Path) -> None:
    """The adapter caps auth-sync lock queueing in addition to DB operations."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth)
    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=_mock_store(shared=True),
        butler_name="qa",
    )
    never = asyncio.Event()

    async def _hang(*_args, **_kwargs) -> CodexAuthSyncResult:
        await never.wait()
        raise AssertionError("unreachable")

    with (
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            side_effect=_hang,
        ),
        patch("butlers.core.runtimes.codex._CODEX_AUTH_SYNC_RUNTIME_TIMEOUT_SECONDS", 0.01),
    ):
        result = await asyncio.wait_for(adapter._reconcile_canonical_auth(auth), timeout=0.2)

    assert result == CodexAuthSyncResult(expected_store_value=None, authority_known=False)


async def test_invoke_uses_one_total_auth_sync_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multiple pre/post sync phases share one bounded Spawner allowance.

    Without the shared budget, two reconciliations plus finalization could each
    consume the full per-operation cap and outrun the single declared Spawner
    overhead. The subprocess itself remains runnable after the allowance is
    spent, while the finalizer safely skips durable work it cannot fit.
    """
    auth = tmp_path / ".codex" / "auth.json"
    authority = json.dumps({"access_token": "authority-A"})
    _write_auth(auth, {"access_token": "authority-A"})
    monkeypatch.setenv("HOME", str(tmp_path))
    store = _mock_store(shared=True)
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")

    second_reconcile_cancelled = False
    reconcile_call_count = 0
    never = asyncio.Event()

    async def _reconcile(*_args, **_kwargs) -> CodexAuthSyncResult:
        """Let the second sync prove it receives only the shared remainder."""
        nonlocal reconcile_call_count, second_reconcile_cancelled
        reconcile_call_count += 1
        if reconcile_call_count == 1:
            return CodexAuthSyncResult(expected_store_value=authority)
        try:
            await never.wait()
        except asyncio.CancelledError:
            second_reconcile_cancelled = True
            raise
        raise AssertionError("unreachable")

    async def _slow_finalize(*_args, **_kwargs) -> CodexAuthSyncResult:
        await asyncio.sleep(0.07)
        return CodexAuthSyncResult(expected_store_value=authority)

    original_reconcile = adapter._reconcile_canonical_auth
    seen_budget = None

    async def _reconcile_with_explicit_first_phase_cost(*args, **kwargs) -> CodexAuthSyncResult:
        """Charge a deterministic first phase without wall-clock scheduling races."""
        nonlocal seen_budget
        budget = kwargs["auth_sync_budget"]
        if seen_budget is None:
            seen_budget = budget
            result = await original_reconcile(*args, **kwargs)
            budget.consume(0.08)
            return result
        assert budget is seen_budget
        return await original_reconcile(*args, **kwargs)

    with (
        patch(
            "butlers.core.runtimes.codex._CODEX_AUTH_SYNC_RUNTIME_TIMEOUT_SECONDS",
            0.1,
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            side_effect=_reconcile,
        ) as reconcile,
        patch(
            "butlers.core.runtimes._codex_auth_sync.finalize_codex_auth_rotation",
            side_effect=_slow_finalize,
        ) as finalize,
        patch.object(
            adapter,
            "_reconcile_canonical_auth",
            side_effect=_reconcile_with_explicit_first_phase_cost,
        ),
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
        patch(_EXEC, return_value=proc),
    ):
        result, _, _ = await adapter.invoke(
            prompt="hello",
            system_prompt="",
            mcp_servers={},
            env={},
        )

    assert result == "ok"
    assert reconcile.await_count == 2
    assert second_reconcile_cancelled
    finalize.assert_not_awaited()


async def test_reconcile_codex_auth_write_failure_is_safe_and_nonfatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed atomic replacement does not block the current runtime path."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "existing-test-token"})
    store = _mock_store(shared=True)
    stored = json.dumps({"access_token": "replacement-test-token"})
    store.load_shared = AsyncMock(return_value=stored)

    with (
        patch(
            "butlers.cli_auth.persistence._write_token_file_atomically",
            side_effect=OSError("synthetic filesystem failure"),
        ),
        caplog.at_level(logging.WARNING, logger="butlers.core.runtimes._codex_auth_sync"),
    ):
        result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result == CodexAuthSyncResult(
        expected_store_value=None,
        file_changed=False,
        authority_known=False,
    )
    assert json.loads(auth.read_text(encoding="utf-8")) == {"access_token": "existing-test-token"}
    assert "replacement-test-token" not in caplog.text
    assert "existing-test-token" not in caplog.text


async def test_reconcile_codex_auth_serializes_concurrent_writes(tmp_path: Path) -> None:
    """Concurrent callers leave one complete, mode-restricted credential file."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "old-token"})
    stored = json.dumps({"access_token": "new-token"})
    first_store = _mock_store(shared=True)
    first_store.load_shared = AsyncMock(return_value=stored)
    second_store = _mock_store(shared=True)
    second_store.load_shared = AsyncMock(return_value=stored)

    outcomes = await asyncio.gather(
        reconcile_codex_auth(auth, first_store, butler_name="qa"),
        reconcile_codex_auth(auth, second_store, butler_name="qa"),
    )

    assert sorted(result.file_changed for result in outcomes) == [False, True]
    assert json.loads(auth.read_text(encoding="utf-8")) == {"access_token": "new-token"}
    assert stat.S_IMODE(auth.stat().st_mode) == 0o600


async def test_reconcile_codex_auth_uses_local_store_without_shared_pool(tmp_path: Path) -> None:
    """Flat deployments retain their local credential-store authority."""
    auth = tmp_path / ".codex" / "auth.json"
    stored = json.dumps({"access_token": "flat-store-token"})
    store = _mock_store()
    store.load = AsyncMock(return_value=stored)

    result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result.expected_store_value == stored
    assert auth.read_text(encoding="utf-8") == stored
    assert store.load.await_count == 2
    store.load.assert_awaited_with("cli-auth/codex")
    store.load_shared.assert_not_awaited()


async def test_reconcile_codex_auth_corrects_matching_file_mode(tmp_path: Path) -> None:
    """Matching token bytes still receive the restrictive mode required for auth files."""
    auth = tmp_path / ".codex" / "auth.json"
    stored = json.dumps({"access_token": "stable-token"})
    auth.parent.mkdir(parents=True)
    auth.write_text(stored, encoding="utf-8")
    auth.chmod(0o644)
    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(return_value=stored)

    result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result.file_changed is True
    assert stat.S_IMODE(auth.stat().st_mode) == 0o600


async def test_reconcile_codex_auth_leaves_invalid_database_value_out_of_file(
    tmp_path: Path,
) -> None:
    """A malformed shared row cannot replace a working local auth document."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "working-token"})
    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(return_value="not-json")

    result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result.expected_store_value is None
    assert json.loads(auth.read_text(encoding="utf-8")) == {"access_token": "working-token"}


async def test_reconcile_flushes_pending_local_rotation_before_authority_read(
    tmp_path: Path,
) -> None:
    """A finished local CLI rotation is safely flushed instead of being overwritten."""
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "old-token"})
    rotated = json.dumps({"access_token": "rotated-token"})
    auth.parent.mkdir(parents=True)
    auth.write_text(old, encoding="utf-8")
    record_auth_baseline(auth)
    auth.write_text(rotated, encoding="utf-8")
    store = _mock_store(shared=True)

    result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result.expected_store_value == rotated
    assert auth.read_text(encoding="utf-8") == rotated
    store.store_shared_if_unchanged.assert_awaited_once()
    store.load_shared.assert_not_awaited()


async def test_reconcile_dashboard_refresh_wins_over_pending_local_rotation(tmp_path: Path) -> None:
    """A stale local rotation cannot beat a newer dashboard credential update."""
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "old-token"})
    rotated = json.dumps({"access_token": "rotated-token"})
    refreshed = json.dumps({"access_token": "dashboard-token"})
    auth.parent.mkdir(parents=True)
    auth.write_text(old, encoding="utf-8")
    record_auth_baseline(auth)
    auth.write_text(rotated, encoding="utf-8")
    store = _mock_store(shared=True)
    store.store_shared_if_unchanged.return_value = False
    store.load_shared = AsyncMock(return_value=refreshed)

    result = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert result.expected_store_value == refreshed
    assert auth.read_text(encoding="utf-8") == refreshed
    store.store_shared_if_unchanged.assert_awaited_once()
    store.load_shared.assert_awaited_once_with("cli-auth/codex")


async def test_reconcile_keeps_successor_rotation_detectable_after_cas_interleaving(
    tmp_path: Path,
) -> None:
    """A C2 writer racing after C1's CAS is never baselined as C1.

    The first reconcile snapshots/persists C1 while the mocked store causes a
    second process to write C2 before the CAS returns.  The cache must retain
    C1's fingerprint so the next reconcile detects and conditionally flushes
    C2 instead of silently overwriting it with C1.
    """
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "old"})
    first_rotation = json.dumps({"access_token": "C1"})
    successor_rotation = json.dumps({"access_token": "C2"})
    auth.parent.mkdir(parents=True)
    auth.write_text(old, encoding="utf-8")
    record_auth_baseline(auth)
    auth.write_text(first_rotation, encoding="utf-8")
    store = _mock_store(shared=True)
    authority = old

    async def _load_shared(_key: str) -> str | None:
        return authority

    cas_count = 0

    async def _cas(_key: str, value: str, *, expected_value: str | None, **_kwargs) -> bool:
        nonlocal authority, cas_count
        cas_count += 1
        if authority != expected_value:
            return False
        authority = value
        if cas_count == 1:
            _write_auth(auth, {"access_token": "C2"})
        return True

    store.load_shared = AsyncMock(side_effect=_load_shared)
    store.store_shared_if_unchanged = AsyncMock(side_effect=_cas)

    first = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert first.expected_store_value == first_rotation
    assert auth.read_text(encoding="utf-8") == successor_rotation
    assert _has_rotated(auth) is True

    second = await reconcile_codex_auth(auth, store, butler_name="qa")

    assert second.expected_store_value == successor_rotation
    assert authority == successor_rotation
    assert auth.read_text(encoding="utf-8") == successor_rotation
    assert cas_count == 2


async def test_reconcile_does_not_adopt_untracked_local_auth_when_authority_is_absent(
    tmp_path: Path,
) -> None:
    """An absent row cannot be recreated from a fresh process's local file."""
    auth = tmp_path / ".codex" / "auth.json"
    local_auth = json.dumps({"access_token": "untracked-local"})
    auth.parent.mkdir(parents=True)
    auth.write_text(local_auth, encoding="utf-8")

    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(return_value=None)

    result = await reconcile_codex_auth(auth, store, butler_name="fresh-process")

    assert result.expected_store_value is None
    assert result.authority_known is True
    assert auth.read_text(encoding="utf-8") == local_auth
    store.store_shared_if_unchanged.assert_not_awaited()


async def test_invoke_does_not_resurrect_a_deleted_credential_or_its_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached A deletion cannot be recreated by an unchanged A invocation."""
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "A"})
    auth.parent.mkdir(parents=True)
    auth.write_text(old, encoding="utf-8")
    record_auth_baseline(auth)
    monkeypatch.setenv("HOME", str(tmp_path))

    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(return_value=None)
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")

    with (
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
        patch(_EXEC, return_value=proc),
    ):
        await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0)

    assert auth.read_text(encoding="utf-8") == old
    store.store_shared_if_unchanged.assert_not_awaited()
    store.record_test_result_if_unchanged.assert_not_awaited()


# ---------------------------------------------------------------------------
# Operation-bound finalization and spawn linearization
# ---------------------------------------------------------------------------


async def test_finalize_old_prewarm_cannot_overwrite_newer_dashboard_refresh(
    tmp_path: Path,
) -> None:
    """A prewarm captured on A must CAS A, never a later dashboard B."""
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "old"})
    dashboard = json.dumps({"access_token": "dashboard"})
    prewarm_rotation = json.dumps({"access_token": "prewarm-rotation"})
    auth.parent.mkdir(parents=True)
    auth.write_text(prewarm_rotation, encoding="utf-8")
    store = _mock_store(shared=True)
    store.store_shared_if_unchanged.return_value = False
    store.load_shared = AsyncMock(return_value=dashboard)

    result = await finalize_codex_auth_rotation(
        auth,
        store,
        expected_store_value=old,
        butler_name="qa",
    )

    assert result.expected_store_value == dashboard
    assert auth.read_text(encoding="utf-8") == dashboard
    _, kwargs = store.store_shared_if_unchanged.await_args
    assert kwargs["expected_value"] == old


async def test_barrier_interleaving_dashboard_refresh_beats_older_prewarm(
    tmp_path: Path,
) -> None:
    """A real A→B→A' interleaving leaves both authorities at B.

    The older prewarm records A, waits while a second dashboard callback
    stores B, then writes its A' rotation.  Its finalizer must use its captured
    A (not the newer mutable cache baseline B), lose the CAS, and restore B.
    """
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "A"})
    dashboard = json.dumps({"access_token": "B"})
    auth.parent.mkdir(parents=True)
    auth.write_text(old, encoding="utf-8")
    store = _mock_store(shared=True)
    authority = old

    async def _load_shared(_key: str) -> str | None:
        return authority

    async def _cas(_key: str, value: str, *, expected_value: str | None, **_kwargs) -> bool:
        nonlocal authority
        if authority != expected_value:
            return False
        authority = value
        return True

    store.load_shared = AsyncMock(side_effect=_load_shared)
    store.store_shared_if_unchanged = AsyncMock(side_effect=_cas)
    older_ready = asyncio.Event()
    dashboard_committed = asyncio.Event()

    async def _older_prewarm() -> None:
        baseline = await reconcile_codex_auth(auth, store, butler_name="older-dashboard")
        assert baseline.expected_store_value == old
        older_ready.set()
        await dashboard_committed.wait()
        _write_auth(auth, {"access_token": "A-rotated"})
        await finalize_codex_auth_rotation(
            auth,
            store,
            expected_store_value=baseline.expected_store_value,
            butler_name="older-dashboard",
        )

    async def _newer_dashboard_refresh() -> None:
        nonlocal authority
        await older_ready.wait()
        authority = dashboard
        _write_auth(auth, {"access_token": "B"})
        dashboard_committed.set()

    await asyncio.gather(_older_prewarm(), _newer_dashboard_refresh())

    assert authority == dashboard
    assert auth.read_text(encoding="utf-8") == dashboard
    assert any(
        call.kwargs.get("expected_value") == old
        for call in store.store_shared_if_unchanged.await_args_list
    )


async def test_finalize_no_rotation_applies_dashboard_refresh(tmp_path: Path) -> None:
    """A dashboard refresh during a non-rotating operation still reaches the file."""
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "old"})
    dashboard = json.dumps({"access_token": "dashboard"})
    auth.parent.mkdir(parents=True)
    auth.write_text(old, encoding="utf-8")
    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(return_value=dashboard)

    result = await finalize_codex_auth_rotation(
        auth,
        store,
        expected_store_value=old,
        butler_name="qa",
    )

    assert result.expected_store_value == dashboard
    assert auth.read_text(encoding="utf-8") == dashboard
    store.store_shared_if_unchanged.assert_not_awaited()


async def test_invoke_reconciles_before_freshness_check_and_isolated_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dashboard credential is visible before either auth-dependent launch step."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "stale-local-token"})
    stored = json.dumps({"access_token": "dashboard-token", "expires_at": 4_102_444_800})
    monkeypatch.setenv("HOME", str(tmp_path))
    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(return_value=stored)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    mock_proc.returncode = 0

    def _assert_freshness_sees_reconciled(codex_dir: Path) -> bool:
        assert (codex_dir / "auth.json").read_text(encoding="utf-8") == stored
        return False

    async def _assert_isolated_home(*_args, **kwargs):
        isolated_auth = Path(kwargs["env"]["HOME"]) / ".codex" / "auth.json"
        assert isolated_auth.is_symlink()
        assert isolated_auth.read_text(encoding="utf-8") == stored
        return mock_proc

    with (
        patch(
            "butlers.core.runtimes.codex._token_needs_refresh",
            side_effect=_assert_freshness_sees_reconciled,
        ),
        patch(_EXEC, side_effect=_assert_isolated_home),
    ):
        await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})


async def test_invoke_revalidates_authority_immediately_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dashboard refresh after early preflight is visible at actual spawn time."""
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "old", "expires_at": 4_102_444_800})
    dashboard = json.dumps({"access_token": "dashboard", "expires_at": 4_102_444_800})
    auth.parent.mkdir(parents=True)
    auth.write_text(old, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    store = _mock_store(shared=True)
    loads = 0

    async def _load_shared(_key: str) -> str:
        nonlocal loads
        loads += 1
        return old if loads == 1 else dashboard

    store.load_shared = AsyncMock(side_effect=_load_shared)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    mock_proc.returncode = 0

    async def _assert_spawn_uses_dashboard_auth(*_args, **_kwargs):
        assert auth.read_text(encoding="utf-8") == dashboard
        return mock_proc

    with (
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
        patch(_EXEC, side_effect=_assert_spawn_uses_dashboard_auth),
    ):
        await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})

    assert loads >= 3


async def test_invoke_continues_when_live_reconciliation_cannot_read_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credential-store degradation preserves the existing best-effort runtime path."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "local-token"})
    monkeypatch.setenv("HOME", str(tmp_path))
    store = _mock_store(shared=True)
    store.load_shared = AsyncMock(side_effect=RuntimeError("store unavailable"))
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    mock_proc.returncode = 0

    async def _ignore_sync(*_args, **_kwargs) -> None:
        return None

    with (
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
        patch(_EXEC, return_value=mock_proc) as mocked_exec,
        patch(
            "butlers.core.runtimes._codex_auth_sync.check_and_persist_rotation",
            side_effect=_ignore_sync,
        ),
    ):
        await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})

    mocked_exec.assert_awaited_once()


async def test_invoke_does_not_treat_unavailable_authority_as_absent_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recovered store cannot replace a rotation after pre-spawn reads failed.

    ``None`` is a valid CAS expectation only when a read established that the
    credential row is absent.  A transient authority outage is unknown, so the
    completed invocation must leave its canonical file alone instead of using
    an insert-only CAS and then applying an old value once the DB recovers.
    """
    auth = tmp_path / ".codex" / "auth.json"
    original = json.dumps({"access_token": "old"})
    rotated = json.dumps({"access_token": "rotated"})
    auth.parent.mkdir(parents=True)
    auth.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))

    store = _mock_store(shared=True)
    load_calls = 0

    async def _load_shared(_key: str) -> str:
        nonlocal load_calls
        load_calls += 1
        if load_calls <= 2:
            raise RuntimeError("temporary authority outage")
        return original

    store.load_shared = AsyncMock(side_effect=_load_shared)
    proc = AsyncMock()
    proc.returncode = 0

    async def _communicate(_stdin: bytes) -> tuple[bytes, bytes]:
        auth.write_text(rotated, encoding="utf-8")
        return _make_ok_stdout(), b""

    proc.communicate = AsyncMock(side_effect=_communicate)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")

    with (
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
        patch(_EXEC, return_value=proc),
    ):
        await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})

    assert auth.read_text(encoding="utf-8") == rotated
    store.store_shared_if_unchanged.assert_not_awaited()
    assert load_calls == 2


async def test_invoke_continues_when_auth_store_reconciliation_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow credential pool cannot turn a locally runnable session into a timeout."""
    auth = tmp_path / ".codex" / "auth.json"
    _write_auth(auth, {"access_token": "local-token"})
    monkeypatch.setenv("HOME", str(tmp_path))
    store = _mock_store(shared=True)
    never = asyncio.Event()

    async def _hang(_key: str) -> str:
        await never.wait()
        raise AssertionError("unreachable")

    store.load_shared = AsyncMock(side_effect=_hang)
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")

    with (
        patch(
            "butlers.core.runtimes._codex_auth_sync._AUTH_STORE_OPERATION_TIMEOUT_S",
            0.01,
        ),
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
        patch(_EXEC, return_value=proc),
    ):
        result, _, _ = await asyncio.wait_for(
            adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={}),
            timeout=0.3,
        )

    assert result == "ok"
    proc.communicate.assert_awaited_once()


# ---------------------------------------------------------------------------
# Integration: two sequential invocations where auth.json mutates on second
# ---------------------------------------------------------------------------


async def test_two_invocations_only_conditionally_store_on_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First invocation establishes authority; only a later rotation writes again.

    The write path is conditional, so this regression exercises the durable
    rotation behavior without restoring the old blind ``persist_token`` path.
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
    persisted_value: str | None = json.dumps({"access_token": "v1"})

    async def _load(_key: str) -> str | None:
        return persisted_value

    async def _cas(_key: str, value: str, *, expected_value: str | None, **_kwargs) -> bool:
        nonlocal persisted_value
        if persisted_value != expected_value:
            return False
        persisted_value = value
        return True

    store.load = AsyncMock(side_effect=_load)
    store.store_shared_if_unchanged = AsyncMock(side_effect=_cas)
    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc):
        # First invocation uses the already-authoritative shared credential.
        await adapter.invoke(prompt="a", system_prompt="", mcp_servers={}, env={})
        assert store.store_shared_if_unchanged.await_count == 0

        # Second invocation: file unchanged → no additional conditional write.
        mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
        await adapter.invoke(prompt="b", system_prompt="", mcp_servers={}, env={})
        assert store.store_shared_if_unchanged.await_count == 0

        # Mutate the auth.json to simulate a token rotation
        time.sleep(0.01)
        _write_auth(auth, {"access_token": "v2"})

        # Third invocation flushes the local rotation before it can be overwritten.
        mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
        await adapter.invoke(prompt="c", system_prompt="", mcp_servers={}, env={})
        assert store.store_shared_if_unchanged.await_count == 1


async def test_retry_persists_the_final_rotation_against_the_last_spawn_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry A→A1→A2 commits A2 rather than leaving it local-only."""
    auth = tmp_path / ".codex" / "auth.json"
    first = json.dumps({"access_token": "A"})
    middle = json.dumps({"access_token": "A1"})
    final = json.dumps({"access_token": "A2"})
    auth.parent.mkdir(parents=True)
    auth.write_text(first, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    store = _mock_store()
    authority: str | None = first

    async def _load(_key: str) -> str | None:
        return authority

    async def _cas(_key: str, value: str, *, expected_value: str | None, **_kwargs) -> bool:
        nonlocal authority
        if authority != expected_value:
            return False
        authority = value
        return True

    store.load = AsyncMock(side_effect=_load)
    store.store_shared_if_unchanged = AsyncMock(side_effect=_cas)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    spawn_count = 0

    async def _spawn(*_args, **_kwargs):
        nonlocal spawn_count
        spawn_count += 1
        proc = AsyncMock()
        proc.pid = spawn_count
        proc.returncode = 1 if spawn_count == 1 else 0

        async def _communicate(_stdin: bytes):
            if spawn_count == 1:
                _write_auth(auth, {"access_token": "A1"})
                return b"", b"codex_core::compact_remote: remote compaction failed"
            _write_auth(auth, {"access_token": "A2"})
            return _make_ok_stdout(), b""

        proc.communicate = AsyncMock(side_effect=_communicate)
        return proc

    with (
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
        patch("butlers.core.runtimes.codex._TRANSIENT_CLI_RETRY_DELAYS", (0,)),
        patch(_EXEC, side_effect=_spawn),
    ):
        result, _, _ = await adapter.invoke(
            prompt="hello", system_prompt="", mcp_servers={}, env={}
        )

    assert result == "ok"
    assert authority == final
    assert auth.read_text(encoding="utf-8") == final
    cas_expectations = [
        call.kwargs["expected_value"] for call in store.store_shared_if_unchanged.await_args_list
    ]
    assert cas_expectations == [first, middle]


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
# _looks_like_auth_refresh_failure — narrowness guarantee (bias 6)
# ---------------------------------------------------------------------------


def test_looks_like_auth_refresh_failure_positive() -> None:
    """Real Codex CLI refresh-reuse message must trip the matcher."""
    # Exact phrasing from Codex CLI (see _CODEX_REFRESH_TOKEN_REUSED_MARKER)
    real_message = (
        "Your access token could not be refreshed because "
        "your refresh token was already used. "
        "Please log out and sign in again."
    )
    assert _looks_like_auth_refresh_failure(real_message) is True
    # Also matches when embedded in a longer error_detail string
    assert _looks_like_auth_refresh_failure(f"Codex CLI exited with code 1: {real_message}") is True
    # Case-insensitive
    assert _looks_like_auth_refresh_failure(real_message.upper()) is True


def test_looks_like_auth_refresh_failure_negative() -> None:
    """An unrelated exit-1 error must NOT trip the auth-refresh matcher."""
    unrelated = [
        "model is at capacity",
        "MCP tool discovery failed",
        "connection refused",
        "Error: network timeout",
        "codex_core::compact_remote",
        "",
    ]
    assert len(unrelated) == 6  # guard against vacuous pass if list shrinks
    for msg in unrelated:
        assert _looks_like_auth_refresh_failure(msg) is False, (
            f"Expected False for {msg!r} but got True"
        )


def test_refresh_token_reused_marker_is_present_in_constant() -> None:
    """Smoke-test: the named constant contains the expected trigger phrase."""
    assert "refresh token" in _CODEX_REFRESH_TOKEN_REUSED_MARKER
    assert "already used" in _CODEX_REFRESH_TOKEN_REUSED_MARKER


# ---------------------------------------------------------------------------
# CredentialStore.record_test_result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ok,message,expect_message",
    [
        # Failure with message → last_test_ok=False, message persisted.
        (False, "token already used", "token already used"),
        # Success with no message → last_test_ok=True, message cleared to None.
        (True, None, None),
    ],
    ids=["failure-with-message", "success-clears-message"],
)
async def test_record_test_result_persists_ok_and_message(ok, message, expect_message) -> None:
    """record_test_result issues an UPDATE binding ok, message, and key."""
    from unittest.mock import AsyncMock as AM
    from unittest.mock import MagicMock as MM

    conn = MM()
    conn.execute = AM(return_value="UPDATE 1")
    conn.__aenter__ = AM(return_value=conn)
    conn.__aexit__ = AM(return_value=False)

    pool = MM()
    pool.acquire = MM(return_value=conn)

    from butlers.credential_store import CredentialStore

    store = CredentialStore(pool)
    kwargs = {"ok": ok}
    if message is not None:
        kwargs["message"] = message
    await store.record_test_result("cli-auth/codex", **kwargs)

    conn.execute.assert_awaited_once()
    call_args = conn.execute.await_args
    # params are $1=ok, $2=message, $3=key
    assert call_args.args[1] is ok
    if expect_message is None:
        assert call_args.args[2] is None
    else:
        assert expect_message in call_args.args[2]
    assert call_args.args[3] == "cli-auth/codex"


async def test_record_test_result_truncates_long_message() -> None:
    """Messages longer than 512 chars are truncated before storage."""
    from unittest.mock import AsyncMock as AM
    from unittest.mock import MagicMock as MM

    conn = MM()
    conn.execute = AM(return_value="UPDATE 1")
    conn.__aenter__ = AM(return_value=conn)
    conn.__aexit__ = AM(return_value=False)

    pool = MM()
    pool.acquire = MM(return_value=conn)

    from butlers.credential_store import CredentialStore

    store = CredentialStore(pool)
    long_msg = "x" * 1000
    await store.record_test_result("cli-auth/codex", ok=False, message=long_msg)

    call_args = conn.execute.await_args
    stored_msg = call_args.args[2]
    assert stored_msg is not None and len(stored_msg) == 512


# ---------------------------------------------------------------------------
# CodexAdapter._schedule_record_test_result
# ---------------------------------------------------------------------------


async def test_schedule_record_test_result_noop_without_store() -> None:
    """Without a credential store, no task is scheduled."""
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")  # no store
    tasks_before = len(asyncio.all_tasks())
    adapter._schedule_record_test_result("cli-auth/codex", ok=False)
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) == tasks_before


async def test_schedule_record_test_result_fires_task_with_store() -> None:
    """An unfenced non-Codex result preserves the generic store writer."""
    store = _mock_store()
    store.record_test_result = AsyncMock(return_value=None)

    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )
    adapter._schedule_record_test_result("cli-auth/codex", ok=False, message="test failure")
    await asyncio.sleep(0.05)

    store.record_test_result.assert_awaited_once_with("cli-auth/codex", False, "test failure")


async def test_schedule_record_test_result_fences_codex_against_shared_authority() -> None:
    """Codex health writes use the shared authority and captured credential bytes."""
    store = _mock_store(shared=True)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    snapshot = json.dumps({"access_token": "current"})

    adapter._schedule_record_test_result(
        "cli-auth/codex",
        ok=False,
        message="old refresh failed",
        expected_store_value=snapshot,
    )
    await asyncio.sleep(0.05)

    store.record_test_result_if_unchanged.assert_awaited_once_with(
        "cli-auth/codex",
        False,
        "old refresh failed",
        expected_value=snapshot,
        use_shared_authority=True,
    )
    store.record_test_result.assert_not_awaited()


async def test_schedule_record_test_result_swallows_store_exception() -> None:
    """Exceptions from record_test_result are logged and not re-raised."""
    store = _mock_store()
    store.record_test_result = AsyncMock(side_effect=RuntimeError("DB failure"))

    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )
    adapter._schedule_record_test_result("cli-auth/codex", ok=False)
    # Must not raise
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Regression: refresh-reuse error drives last_test_ok → banner goes red
#
# This test verifies the fix: before this change, a Codex spawn failure with
# a refresh-reuse error would be silently logged but never persisted to
# credential state, leaving the banner green/healthy during a real auth outage.
# ---------------------------------------------------------------------------

_RECORD_TEST = "butlers.credential_store.CredentialStore.record_test_result"


async def test_invoke_writes_test_result_false_on_refresh_reuse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: refresh-token-reuse error must set last_test_ok=false on the
    cli-auth/codex credential row so the secrets passport banner turns red.

    Before the fix: the error was logged + raised but credential state was never
    updated → banner stayed green during a real auth outage (silent failure).
    """
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    _write_auth(codex_dir / "auth.json")
    monkeypatch.setenv("HOME", str(tmp_path))

    store = _mock_store()
    stored = (codex_dir / "auth.json").read_text(encoding="utf-8")
    store.load = AsyncMock(return_value=stored)

    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )

    # Simulate a Codex CLI exit-1 with the exact refresh-reuse phrase
    refresh_error_stderr = (
        "Your access token could not be refreshed because "
        "your refresh token was already used. "
        "Please log out and sign in again."
    )
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", refresh_error_stderr.encode()))
    mock_proc.returncode = 1

    with (
        patch(_EXEC, return_value=mock_proc),
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
    ):
        with pytest.raises(RuntimeError, match="Codex CLI exited with code 1"):
            await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.1)

    # The fix: credential state must be updated to failing
    store.record_test_result_if_unchanged.assert_awaited_once()
    call_args = store.record_test_result_if_unchanged.await_args
    assert call_args.args[0] == "cli-auth/codex"
    assert call_args.args[1] is False  # ok=False → state 'failing'
    assert call_args.kwargs["expected_value"] == stored
    assert call_args.kwargs["use_shared_authority"] is True


async def test_stale_refresh_failure_cannot_mark_dashboard_replacement_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing A session's failure never creates a red banner on B."""
    auth = tmp_path / ".codex" / "auth.json"
    old = json.dumps({"access_token": "A"})
    dashboard = json.dumps({"access_token": "B"})
    auth.parent.mkdir(parents=True)
    auth.write_text(old, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    store = _mock_store(shared=True)
    authority = old

    async def _load_shared(_key: str) -> str:
        return authority

    async def _cas(_key: str, _value: str, *, expected_value: str | None, **_kwargs) -> bool:
        return authority == expected_value

    store.load_shared = AsyncMock(side_effect=_load_shared)
    store.store_shared_if_unchanged = AsyncMock(side_effect=_cas)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    refresh_error = b"your refresh token was already used"
    proc = AsyncMock()
    proc.returncode = 1

    async def _communicate(_stdin: bytes) -> tuple[bytes, bytes]:
        nonlocal authority
        authority = dashboard
        auth.write_text(dashboard, encoding="utf-8")
        return b"", refresh_error

    proc.communicate = AsyncMock(side_effect=_communicate)

    with (
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
        patch(_EXEC, return_value=proc),
    ):
        with pytest.raises(RuntimeError, match="refresh token"):
            await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.05)

    assert authority == dashboard
    assert auth.read_text(encoding="utf-8") == dashboard
    store.record_test_result_if_unchanged.assert_not_awaited()


async def test_invoke_does_not_write_test_result_for_unrelated_exit1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrelated exit-1 (e.g. model capacity) must NOT set last_test_ok=false.

    Only the refresh-token-reuse error triggers the credential state update.
    """
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    _write_auth(codex_dir / "auth.json")
    monkeypatch.setenv("HOME", str(tmp_path))

    store = _mock_store()
    stored = (codex_dir / "auth.json").read_text(encoding="utf-8")
    store.load = AsyncMock(return_value=stored)

    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )

    unrelated_stderr = b"Error: model is at capacity. Please try again later."
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", unrelated_stderr))
    mock_proc.returncode = 1

    with (
        patch(_EXEC, return_value=mock_proc),
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
    ):
        with pytest.raises(RuntimeError):
            await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.1)

    # record_test_result must NOT have been called at all: neither the
    # auth-refresh path (wrong error) nor the success path (exit-1) fires here.
    store.record_test_result.assert_not_called()
    store.record_test_result_if_unchanged.assert_not_called()


async def test_invoke_writes_test_result_true_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful spawn must clear any prior auth-failure state (ok=True).

    This is decoupled from token rotation — a non-rotating success must still
    clear last_test_ok=false so the banner self-heals after re-authentication.
    """
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    _write_auth(codex_dir / "auth.json")
    monkeypatch.setenv("HOME", str(tmp_path))

    store = _mock_store()
    stored = (codex_dir / "auth.json").read_text(encoding="utf-8")
    store.load = AsyncMock(return_value=stored)

    adapter = CodexAdapter(
        codex_binary="/usr/bin/codex",
        credential_store=store,
        butler_name="qa",
    )

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_stdout(), b""))
    mock_proc.returncode = 0

    with (
        patch(_EXEC, return_value=mock_proc),
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
    ):
        await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        await asyncio.sleep(0.1)

    # ok=True must have been recorded (clear-on-success)
    success_calls = [
        c for c in store.record_test_result_if_unchanged.await_args_list if c.args[1] is True
    ]
    assert len(success_calls) >= 1, (
        "Expected at least one record_test_result(ok=True) call on successful spawn"
    )


# ---------------------------------------------------------------------------
# Per-test cache cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_auth_sync_cache():
    """Reset _AUTH_SYNC_CACHE before and after each test."""
    _AUTH_SYNC_CACHE.clear()
    yield
    _AUTH_SYNC_CACHE.clear()

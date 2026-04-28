"""Tests for the Codex cross-process refresh-token serialisation mechanism.

Covers:
- _read_codex_token_expires_at: parses expires_at from auth.json
- _token_needs_refresh: fast / slow path detection
- _codex_refresh_lock: POSIX flock with contention warning and timeout
- run_codex_pre_warm: calls `codex login status` under the lock
- CodexAdapter.invoke(): slow-path takes lock; fast-path skips lock
- CodexAdapter._prewarm_done: per-process singleton, cleared between tests
- Concurrent CodexAdapter.invoke() calls: only one refresh at a time
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes.codex import (
    _CODEX_TOKEN_EXPIRY_BUFFER_SECONDS,
    CodexAdapter,
    _codex_refresh_lock,
    _read_codex_token_expires_at,
    _token_needs_refresh,
    run_codex_pre_warm,
)

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_auth_json(codex_dir: Path, *, expires_at: float | None = None) -> None:
    """Write a minimal auth.json to *codex_dir* with the given expiry."""
    codex_dir.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if expires_at is not None:
        data["expires_at"] = expires_at
    (codex_dir / "auth.json").write_text(json.dumps(data), encoding="utf-8")


def _write_jwt_auth_json(codex_dir: Path, *, exp: float) -> None:
    """Write a Codex-style auth.json with expiry inside tokens.access_token."""
    codex_dir.mkdir(parents=True, exist_ok=True)
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode("utf-8"))
        .rstrip(b"=")
        .decode()
    )
    access_token = f"{header}.{payload}.sig"
    data = {"tokens": {"access_token": access_token}}
    (codex_dir / "auth.json").write_text(json.dumps(data), encoding="utf-8")


def _fresh_auth_json(codex_dir: Path) -> None:
    """Write an auth.json whose token expires 1 hour from now."""
    _write_auth_json(codex_dir, expires_at=time.time() + 3600)


def _stale_auth_json(codex_dir: Path) -> None:
    """Write an auth.json whose token is already expired."""
    _write_auth_json(codex_dir, expires_at=time.time() - 10)


def _near_expiry_auth_json(codex_dir: Path) -> None:
    """Write an auth.json whose token expires just inside the buffer window."""
    _write_auth_json(codex_dir, expires_at=time.time() + _CODEX_TOKEN_EXPIRY_BUFFER_SECONDS - 5)


def _make_ok_proc_bytes() -> bytes:
    """Minimal JSON-line stdout for a successful Codex invocation."""
    return json.dumps({"type": "result", "result": "ok"}).encode()


# ---------------------------------------------------------------------------
# _read_codex_token_expires_at
# ---------------------------------------------------------------------------


def test_read_expires_at_returns_none_when_no_file(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    assert _read_codex_token_expires_at(codex_dir) is None


def test_read_expires_at_returns_none_when_field_missing(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    _write_auth_json(codex_dir)  # no expires_at field
    assert _read_codex_token_expires_at(codex_dir) is None


def test_read_expires_at_parses_numeric_value(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    _write_auth_json(codex_dir, expires_at=9999999999.0)
    result = _read_codex_token_expires_at(codex_dir)
    assert result == pytest.approx(9999999999.0)


def test_read_expires_at_parses_codex_access_token_jwt(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    _write_jwt_auth_json(codex_dir, exp=9999999999.0)
    result = _read_codex_token_expires_at(codex_dir)
    assert result == pytest.approx(9999999999.0)


def test_read_expires_at_returns_none_for_invalid_json(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text("not-json", encoding="utf-8")
    assert _read_codex_token_expires_at(codex_dir) is None


def test_read_expires_at_returns_none_for_unparseable_access_token(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "header.not-base64.eyJzaWciOiAidmFsdWUifQ"}}),
        encoding="utf-8",
    )
    assert _read_codex_token_expires_at(codex_dir) is None


# ---------------------------------------------------------------------------
# _token_needs_refresh
# ---------------------------------------------------------------------------


def test_token_needs_refresh_when_no_file(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    assert _token_needs_refresh(codex_dir) is True


def test_token_needs_refresh_when_token_expired(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    assert _token_needs_refresh(codex_dir) is True


def test_token_needs_refresh_when_token_near_expiry(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    _near_expiry_auth_json(codex_dir)
    assert _token_needs_refresh(codex_dir) is True


def test_token_does_not_need_refresh_when_fresh(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    _fresh_auth_json(codex_dir)
    assert _token_needs_refresh(codex_dir) is False


def test_token_does_not_need_refresh_when_access_token_jwt_is_fresh(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    _write_jwt_auth_json(codex_dir, exp=time.time() + 3600)
    assert _token_needs_refresh(codex_dir) is False


def test_token_needs_refresh_when_access_token_jwt_is_expired(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    _write_jwt_auth_json(codex_dir, exp=time.time() - 10)
    assert _token_needs_refresh(codex_dir) is True


# ---------------------------------------------------------------------------
# _codex_refresh_lock: basic acquire/release
# ---------------------------------------------------------------------------


async def test_refresh_lock_acquires_and_releases(tmp_path: Path) -> None:
    """Lock manager completes normally and releases the flock."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()

    lock_path = codex_dir / "butlers.refresh.lock"
    assert not lock_path.exists()

    async with _codex_refresh_lock(codex_dir):
        assert lock_path.exists()

    # File stays around (lock released but not deleted — intentional)
    assert lock_path.exists()


async def test_refresh_lock_timeout_proceeds_unlocked(tmp_path: Path) -> None:
    """When the lock cannot be acquired within the timeout, the manager yields
    anyway (never deadlocks the caller) and logs a warning."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    lock_path = codex_dir / "butlers.refresh.lock"

    # Pre-acquire the lock in a blocking way via a raw fd
    import fcntl

    other_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    fcntl.flock(other_fd, fcntl.LOCK_EX)

    try:
        entered = False
        with (
            patch("butlers.core.runtimes.codex._CODEX_REFRESH_LOCK_TIMEOUT_SECONDS", 0.5),
            patch("butlers.core.runtimes.codex._CODEX_REFRESH_LOCK_CONTENTION_WARN_SECONDS", 0.1),
        ):
            async with _codex_refresh_lock(codex_dir):
                entered = True  # should still enter, just without the lock
        assert entered, "Lock timeout should yield (not raise)"
    finally:
        fcntl.flock(other_fd, fcntl.LOCK_UN)
        os.close(other_fd)


# ---------------------------------------------------------------------------
# run_codex_pre_warm
# ---------------------------------------------------------------------------


async def test_run_codex_pre_warm_calls_login_status(tmp_path: Path) -> None:
    """run_codex_pre_warm spawns ``codex login status`` under the lock."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        await run_codex_pre_warm(codex_dir, "/usr/bin/codex")

    assert mock_sub.call_count == 1
    called_cmd = mock_sub.call_args[0]
    assert called_cmd[:3] == ("/usr/bin/codex", "login", "status")


async def test_run_codex_pre_warm_swallows_nonzero_exit(tmp_path: Path) -> None:
    """Pre-warm does not raise when codex login status exits non-zero."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error text"))
    mock_proc.returncode = 1

    with patch(_EXEC, return_value=mock_proc):
        # Should not raise
        await run_codex_pre_warm(codex_dir, "/usr/bin/codex")


async def test_run_codex_pre_warm_swallows_timeout(tmp_path: Path) -> None:
    """Pre-warm does not raise when the subprocess times out."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with (
        patch(_EXEC, return_value=mock_proc),
        patch(
            "butlers.core.runtimes.codex.asyncio.wait_for",
            side_effect=TimeoutError,
        ),
    ):
        await run_codex_pre_warm(codex_dir, "/usr/bin/codex")  # must not raise


# ---------------------------------------------------------------------------
# CodexAdapter.invoke() — fast / slow path selection
# ---------------------------------------------------------------------------


async def test_invoke_fast_path_skips_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When token has ample lifetime, invoke() does NOT acquire the lock."""
    codex_dir = tmp_path / ".codex"
    _fresh_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))

    # Ensure prewarm done so startup pre-warm is skipped too
    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.add(prewarm_key)

    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_proc_bytes(), b""))
    mock_proc.returncode = 0

    lock_entered = []

    original_lock = _codex_refresh_lock

    @contextlib.asynccontextmanager
    async def _spy_lock(path: Path):  # type: ignore[return]
        async with original_lock(path):
            lock_entered.append(True)
            yield

    with (
        patch(_EXEC, return_value=mock_proc),
        patch("butlers.core.runtimes.codex._codex_refresh_lock", new=_spy_lock),
    ):
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    assert not lock_entered, "Fast path should not acquire the refresh lock"


async def test_invoke_slow_path_acquires_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When token is near expiry, invoke() acquires the cross-process lock."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))

    # Skip startup pre-warm by marking it done already (pre-warm is tested separately)
    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.add(prewarm_key)

    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_proc_bytes(), b""))
    mock_proc.returncode = 0

    lock_entered = []
    original_lock = _codex_refresh_lock

    @contextlib.asynccontextmanager
    async def _spy_lock(path: Path):  # type: ignore[return]
        async with original_lock(path):
            lock_entered.append(True)
            yield

    with (
        patch(_EXEC, return_value=mock_proc),
        patch("butlers.core.runtimes.codex._codex_refresh_lock", new=_spy_lock),
    ):
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    assert lock_entered, "Slow path should acquire the refresh lock"


async def test_invoke_startup_prewarm_runs_on_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First invoke() per process with a stale token triggers the startup pre-warm."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))

    prewarm_key = str(codex_dir)
    # Ensure clean state: no pre-warm done yet
    CodexAdapter._prewarm_done.discard(prewarm_key)

    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_proc_bytes(), b""))
    mock_proc.returncode = 0

    prewarm_calls = []

    async def _mock_prewarm(codex_dir_arg: Path, binary: str) -> None:
        prewarm_calls.append((str(codex_dir_arg), binary))

    with (
        patch(_EXEC, return_value=mock_proc),
        patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_mock_prewarm),
    ):
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    assert len(prewarm_calls) == 1, "Pre-warm should run exactly once on first stale invoke"
    assert prewarm_calls[0][1] == "/usr/bin/codex"


async def test_invoke_startup_prewarm_skipped_on_second_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subsequent invoke() calls do not re-run the startup pre-warm."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))

    prewarm_key = str(codex_dir)
    # Pre-mark as done (simulates state after first invoke)
    CodexAdapter._prewarm_done.add(prewarm_key)

    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(_make_ok_proc_bytes(), b""))
    mock_proc.returncode = 0

    prewarm_calls = []

    async def _mock_prewarm(codex_dir_arg: Path, binary: str) -> None:
        prewarm_calls.append((str(codex_dir_arg), binary))

    with (
        patch(_EXEC, return_value=mock_proc),
        patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_mock_prewarm),
    ):
        await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    assert not prewarm_calls, "Pre-warm should be skipped when already done for this process"


# ---------------------------------------------------------------------------
# Concurrent invoke() serialisation regression test
# ---------------------------------------------------------------------------


async def test_concurrent_invoke_serialised_on_slow_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent invoke() calls in the same process on the slow path must
    be serialised: the second call's subprocess must start only after the first
    finishes (not before).

    We assert this by recording (start, end) timestamps for each subprocess
    invocation and checking that the intervals do not overlap.
    """
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))

    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.add(prewarm_key)  # skip startup pre-warm

    adapter1 = CodexAdapter(codex_binary="/usr/bin/codex")
    adapter2 = CodexAdapter(codex_binary="/usr/bin/codex")

    # Each subprocess call takes 50ms.
    spawn_intervals: list[tuple[float, float]] = []

    async def _fake_create_subprocess(*args, **kwargs):
        start = time.monotonic()
        mock_proc = AsyncMock()

        async def _communicate(_: bytes | None = None) -> tuple[bytes, bytes]:
            await asyncio.sleep(0.05)  # 50ms "work"
            return _make_ok_proc_bytes(), b""

        mock_proc.communicate = _communicate
        mock_proc.returncode = 0
        mock_proc.pid = os.getpid()
        end = time.monotonic()
        spawn_intervals.append((start, end))
        return mock_proc

    with patch(_EXEC, side_effect=_fake_create_subprocess):
        await asyncio.gather(
            adapter1.invoke(prompt="a", system_prompt="", mcp_servers={}, env={}),
            adapter2.invoke(prompt="b", system_prompt="", mcp_servers={}, env={}),
        )

    assert len(spawn_intervals) == 2, f"Expected 2 spawns, got {len(spawn_intervals)}"

    # The subprocess calls (not just the spawns) must be serialised.
    # Because the lock is held for the entire _run_codex_subprocess call,
    # the second call's start must be >= the first call's end (or vice-versa).
    (s1, e1), (s2, e2) = sorted(spawn_intervals)
    overlap = min(e1, e2) - max(s1, s2)
    # Allow 20ms slop for scheduling jitter
    assert overlap <= 0.02, (
        f"Concurrent slow-path invocations overlapped by {overlap * 1000:.1f}ms — "
        "cross-process lock is not serialising refreshes"
    )


# ---------------------------------------------------------------------------
# Per-test cleanup: reset process-wide _prewarm_done between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_prewarm_done():
    """Clear CodexAdapter._prewarm_done before and after each test so tests are
    independent regardless of ordering."""
    CodexAdapter._prewarm_done.clear()
    yield
    CodexAdapter._prewarm_done.clear()

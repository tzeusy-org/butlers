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
import logging
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.runtimes.codex import (
    _CODEX_TOKEN_EXPIRY_BUFFER_SECONDS,
    CodexAdapter,
    _codex_refresh_lock,
    _maybe_speculative_codex_prewarm,
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
        base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode("utf-8")).rstrip(b"=").decode()
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


async def test_refresh_lock_timeout_proceeds_unlocked(tmp_path: Path, caplog) -> None:
    """When the lock cannot be acquired within the timeout, the manager yields
    anyway (never deadlocks the caller)."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    lock_path = codex_dir / "butlers.refresh.lock"

    # Pre-acquire the lock in a blocking way via a raw fd
    import fcntl

    other_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    fcntl.flock(other_fd, fcntl.LOCK_EX)

    try:
        entered = False
        caplog.set_level(logging.INFO, logger="butlers.core.runtimes.codex")
        with (
            patch("butlers.core.runtimes.codex._CODEX_REFRESH_LOCK_TIMEOUT_SECONDS", 0.5),
            patch("butlers.core.runtimes.codex._CODEX_REFRESH_LOCK_CONTENTION_WARN_SECONDS", 0.1),
        ):
            async with _codex_refresh_lock(codex_dir):
                entered = True  # should still enter, just without the lock
        assert entered, "Lock timeout should yield (not raise)"
        assert "proceeding unlocked to avoid deadlock" in caplog.text
        assert not [
            record
            for record in caplog.records
            if record.name == "butlers.core.runtimes.codex"
            and record.levelno >= logging.WARNING
            and "codex_refresh_lock" in record.getMessage()
        ]
    finally:
        fcntl.flock(other_fd, fcntl.LOCK_UN)
        os.close(other_fd)


async def test_refresh_lock_budget_does_not_queue_on_default_executor(
    tmp_path: Path,
) -> None:
    """A saturated executor cannot extend a nonblocking flock wait budget."""
    import fcntl

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    lock_path = codex_dir / "butlers.refresh.lock"
    other_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    fcntl.flock(other_fd, fcntl.LOCK_EX)

    loop = asyncio.get_running_loop()

    def _queued_executor_work(*_args: object, **_kwargs: object) -> asyncio.Future[object]:
        """Model a default executor whose work cannot start within the budget."""
        return loop.create_future()

    try:
        with patch.object(
            loop,
            "run_in_executor",
            side_effect=_queued_executor_work,
        ) as run_in_executor:

            async def _acquire() -> None:
                async with _codex_refresh_lock(codex_dir, wait_timeout_s=0.01):
                    pass

            await asyncio.wait_for(_acquire(), timeout=0.5)
    finally:
        fcntl.flock(other_fd, fcntl.LOCK_UN)
        os.close(other_fd)

    run_in_executor.assert_not_called()


async def test_refresh_lock_clamps_retry_sleep_to_operation_budget(tmp_path: Path) -> None:
    """A sub-second caller budget is not extended by the normal retry interval."""
    import fcntl

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    lock_path = codex_dir / "butlers.refresh.lock"
    other_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    fcntl.flock(other_fd, fcntl.LOCK_EX)
    sleep_delays: list[float] = []

    async def _record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    try:
        # deadline setup, first remaining-time calculation, then expiry after
        # the clamped retry. This avoids scheduler timing in the assertion.
        with (
            patch(
                "butlers.core.runtimes.codex.time.monotonic",
                side_effect=(100.0, 100.0, 100.01),
            ),
            patch("butlers.core.runtimes.codex.asyncio.sleep", side_effect=_record_sleep),
        ):
            async with _codex_refresh_lock(codex_dir, wait_timeout_s=0.01):
                pass
    finally:
        fcntl.flock(other_fd, fcntl.LOCK_UN)
        os.close(other_fd)

    assert sleep_delays == [pytest.approx(0.01)]


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
    """A timed-out live status child is killed and reaped without raising."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()

    reap_started = asyncio.Event()
    release_reaper = asyncio.Event()
    reaped = asyncio.Event()
    mock_proc = MagicMock()
    mock_proc.communicate = MagicMock(return_value=object())
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()

    async def _wait() -> None:
        reap_started.set()
        await release_reaper.wait()
        reaped.set()

    mock_proc.wait = AsyncMock(side_effect=_wait)
    await_event = asyncio.wait_for

    with (
        patch(_EXEC, return_value=mock_proc),
        patch(
            "butlers.core.runtimes.codex.asyncio.wait_for",
            side_effect=TimeoutError,
        ),
    ):
        task = asyncio.create_task(run_codex_pre_warm(codex_dir, "/usr/bin/codex"))
        await await_event(reap_started.wait(), timeout=0.2)
        assert task.done()
        await task

    release_reaper.set()
    await await_event(reaped.wait(), timeout=0.2)
    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()


async def test_run_codex_pre_warm_reaps_status_process_when_cancelled(tmp_path: Path) -> None:
    """An outer auth-budget cancellation cannot leave `login status` alive."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    entered = asyncio.Event()
    never = asyncio.Event()
    proc = MagicMock()
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    async def _communicate() -> tuple[bytes, bytes]:
        entered.set()
        await never.wait()
        raise AssertionError("unreachable")

    proc.communicate = AsyncMock(side_effect=_communicate)
    with patch(_EXEC, return_value=proc):
        task = asyncio.create_task(run_codex_pre_warm(codex_dir, "/usr/bin/codex"))
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


async def test_run_codex_pre_warm_cancellation_does_not_wait_for_hung_reap(
    tmp_path: Path,
) -> None:
    """A stuck process wait is detached rather than extending the auth budget."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    entered = asyncio.Event()
    reap_started = asyncio.Event()
    release_reaper = asyncio.Event()
    never = asyncio.Event()
    proc = MagicMock()
    proc.returncode = None
    proc.kill = MagicMock()

    async def _communicate() -> tuple[bytes, bytes]:
        entered.set()
        await never.wait()
        raise AssertionError("unreachable")

    async def _hung_wait() -> None:
        reap_started.set()
        await release_reaper.wait()

    proc.communicate = AsyncMock(side_effect=_communicate)
    proc.wait = AsyncMock(side_effect=_hung_wait)
    with patch(_EXEC, return_value=proc):
        task = asyncio.create_task(run_codex_pre_warm(codex_dir, "/usr/bin/codex"))
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        task.cancel()
        # One event-loop turn is enough for the cancellation handler to kill
        # the child, detach its hung reaper, and re-raise. An implementation
        # that awaits ``proc.wait()`` remains pending here.
        await asyncio.sleep(0)
        assert task.done()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(reap_started.wait(), timeout=0.2)
        release_reaper.set()
        await asyncio.sleep(0)

    proc.kill.assert_called_once()


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
    async def _spy_lock(path: Path, **kwargs):  # type: ignore[return]
        async with original_lock(path, **kwargs):
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
    async def _spy_lock(path: Path, **kwargs):  # type: ignore[return]
        async with original_lock(path, **kwargs):
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


async def test_invoke_bounds_on_path_prewarm_outside_provider_execution_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung status warmup cannot take time from the eventual Codex spawn."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.discard(prewarm_key)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")

    never = asyncio.Event()
    prewarm_cancelled = False

    async def _hung_prewarm(*_args, **_kwargs) -> None:
        nonlocal prewarm_cancelled
        try:
            await never.wait()
        except asyncio.CancelledError:
            prewarm_cancelled = True
            raise

    with (
        patch(
            "butlers.core.runtimes.codex._CODEX_AUTH_SYNC_RUNTIME_TIMEOUT_SECONDS",
            0.01,
        ),
        patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_hung_prewarm),
        patch.object(
            adapter,
            "_run_codex_subprocess",
            AsyncMock(return_value=("ok", [], None)),
        ) as spawn,
    ):
        result, _, _ = await asyncio.wait_for(
            adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
                timeout=1,
            ),
            timeout=0.5,
        )

    assert result == "ok"
    assert spawn.call_args.args[3] == 1
    assert prewarm_cancelled
    assert prewarm_key not in CodexAdapter._prewarm_done


async def test_invoke_bounds_refresh_lock_wait_outside_provider_execution_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow-path flock contention spends only the declared auth allowance."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.add(prewarm_key)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    wait_budgets: list[float | None] = []

    @contextlib.asynccontextmanager
    async def _capture_lock(*_args: object, wait_timeout_s: float | None = None):
        wait_budgets.append(wait_timeout_s)
        yield

    with (
        patch(
            "butlers.core.runtimes.codex._CODEX_AUTH_SYNC_RUNTIME_TIMEOUT_SECONDS",
            0.01,
        ),
        patch("butlers.core.runtimes.codex._codex_refresh_lock", _capture_lock),
        patch.object(
            adapter,
            "_run_codex_subprocess",
            AsyncMock(return_value=("ok", [], None)),
        ) as spawn,
    ):
        result, _, _ = await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
            timeout=1,
        )

    assert result == "ok"
    assert spawn.call_args.args[3] == 1
    assert len(wait_budgets) == 1
    assert wait_budgets[0] is not None
    assert 0 < wait_budgets[0] <= 0.01


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
# _maybe_speculative_codex_prewarm (bu-ep4ks.13 follow-up / bu-k9te9, slice 4)
#
# The spawner's fire-and-forget speculative prewarm (CodexAdapter.speculative_prewarm)
# delegates to this same module-level helper. It must reach the identical decision
# invoke()'s own on-path check would reach for the same on-disk state, and must be
# fully idempotent with it via the shared CodexAdapter._prewarm_done set.
# ---------------------------------------------------------------------------


async def test_speculative_prewarm_runs_when_token_stale_and_not_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale token, not yet pre-warmed this process -> runs and marks done."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.discard(prewarm_key)

    prewarm_calls = []

    async def _mock_prewarm(codex_dir_arg: Path, binary: str) -> None:
        prewarm_calls.append((str(codex_dir_arg), binary))

    with patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_mock_prewarm):
        await _maybe_speculative_codex_prewarm("/usr/bin/codex")

    assert prewarm_calls == [(prewarm_key, "/usr/bin/codex")]
    assert prewarm_key in CodexAdapter._prewarm_done


async def test_speculative_prewarm_skipped_when_already_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already pre-warmed this process -> no redundant call."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.add(prewarm_key)

    prewarm_calls = []

    async def _mock_prewarm(codex_dir_arg: Path, binary: str) -> None:
        prewarm_calls.append((str(codex_dir_arg), binary))

    with patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_mock_prewarm):
        await _maybe_speculative_codex_prewarm("/usr/bin/codex")

    assert not prewarm_calls


async def test_speculative_prewarm_skipped_when_no_auth_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unauthenticated state (no auth.json) -> nothing to refresh, no call."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Deliberately do not create tmp_path / ".codex" / "auth.json".

    prewarm_calls = []

    async def _mock_prewarm(codex_dir_arg: Path, binary: str) -> None:
        prewarm_calls.append((str(codex_dir_arg), binary))

    with patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_mock_prewarm):
        await _maybe_speculative_codex_prewarm("/usr/bin/codex")

    assert not prewarm_calls


async def test_speculative_prewarm_skipped_when_token_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh token -> no refresh needed, no call."""
    codex_dir = tmp_path / ".codex"
    _fresh_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    CodexAdapter._prewarm_done.discard(str(codex_dir))

    prewarm_calls = []

    async def _mock_prewarm(codex_dir_arg: Path, binary: str) -> None:
        prewarm_calls.append((str(codex_dir_arg), binary))

    with patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_mock_prewarm):
        await _maybe_speculative_codex_prewarm("/usr/bin/codex")

    assert not prewarm_calls


async def test_speculative_prewarm_never_raises_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure inside the speculative prewarm must never propagate to the caller.

    The spawner fires this via asyncio.create_task without awaiting or wrapping it, so an
    unswallowed exception here would only surface as an unhandled-task-exception log line at
    best -- but the contract ("a prewarm failure must never fail, delay, or alter dispatch")
    is strongest when the helper itself guarantees it never raises at all.
    """
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    CodexAdapter._prewarm_done.discard(str(codex_dir))

    with patch(
        "butlers.core.runtimes.codex.run_codex_pre_warm",
        side_effect=RuntimeError("boom"),
    ):
        await _maybe_speculative_codex_prewarm("/usr/bin/codex")  # must not raise

    # A raising pre-warm must not be recorded as done -- a later attempt (speculative or
    # on-path) should still retry rather than silently giving up forever.
    assert str(codex_dir) not in CodexAdapter._prewarm_done


async def test_speculative_prewarm_makes_invoke_skip_its_own_on_path_prewarm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The idempotency guarantee the whole optimization depends on.

    A speculative prewarm that ran to completion before invoke() must leave invoke()'s own
    on-path pre-warm check with nothing to do -- otherwise the fold would just add a second
    redundant warmup instead of eliminating the on-path one.
    """
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.discard(prewarm_key)

    prewarm_calls = []

    async def _mock_prewarm(codex_dir_arg: Path, binary: str) -> None:
        prewarm_calls.append((str(codex_dir_arg), binary))

    with patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_mock_prewarm):
        # Speculative prewarm runs first, exactly as the spawner's fire-and-forget task would.
        await _maybe_speculative_codex_prewarm("/usr/bin/codex")
        assert len(prewarm_calls) == 1

        # invoke()'s own on-path pre-warm check must now find _prewarm_done already set and
        # skip calling run_codex_pre_warm again.
        adapter = CodexAdapter(codex_binary="/usr/bin/codex")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(_make_ok_proc_bytes(), b""))
        mock_proc.returncode = 0
        with patch(_EXEC, return_value=mock_proc):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env={})

    assert len(prewarm_calls) == 1, "invoke() must not redundantly re-run the pre-warm"


async def test_adapter_speculative_prewarm_delegates_to_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CodexAdapter.speculative_prewarm() (the spawner-facing hook) uses the adapter's
    own resolved binary and reaches the same helper invoke() uses internally."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    CodexAdapter._prewarm_done.discard(str(codex_dir))

    prewarm_calls = []

    async def _mock_prewarm(codex_dir_arg: Path, binary: str) -> None:
        prewarm_calls.append((str(codex_dir_arg), binary))

    adapter = CodexAdapter(codex_binary="/opt/codex/codex")
    with patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_mock_prewarm):
        await adapter.speculative_prewarm()

    assert prewarm_calls == [(str(codex_dir), "/opt/codex/codex")]


async def test_adapter_speculative_prewarm_reconciles_before_login_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Speculative prewarm cannot run against a stale daemon-local auth file."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))
    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.discard(prewarm_key)
    stored = json.dumps({"expires_at": time.time() - 10, "access_token": "dashboard-token"})
    store = MagicMock()
    store.shared_pool = MagicMock()
    store.load_shared = AsyncMock(return_value=stored)
    store.store_shared_if_unchanged = AsyncMock(return_value=True)

    async def _assert_prewarm_input(codex_dir_arg: Path, binary: str) -> None:
        assert binary == "/opt/codex/codex"
        assert (codex_dir_arg / "auth.json").read_text(encoding="utf-8") == stored

    adapter = CodexAdapter(codex_binary="/opt/codex/codex", credential_store=store)
    with patch("butlers.core.runtimes.codex.run_codex_pre_warm", side_effect=_assert_prewarm_input):
        await adapter.speculative_prewarm()

    store.load_shared.assert_awaited()


# ---------------------------------------------------------------------------
# Concurrent invoke() serialisation regression test
# ---------------------------------------------------------------------------


async def test_concurrent_invoke_serialised_on_slow_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refresh lock stays held until the first slow-path subprocess exits."""
    codex_dir = tmp_path / ".codex"
    _stale_auth_json(codex_dir)
    monkeypatch.setenv("HOME", str(tmp_path))

    prewarm_key = str(codex_dir)
    CodexAdapter._prewarm_done.add(prewarm_key)  # skip startup pre-warm

    adapter1 = CodexAdapter(codex_binary="/usr/bin/codex")
    adapter2 = CodexAdapter(codex_binary="/usr/bin/codex")

    lock = asyncio.Lock()
    first_communicating = asyncio.Event()
    release_first = asyncio.Event()
    second_waiting_for_lock = asyncio.Event()
    second_spawned = asyncio.Event()
    spawn_count = 0

    @contextlib.asynccontextmanager
    async def _serializing_lock(*_args: object, **_kwargs: object):
        if lock.locked():
            second_waiting_for_lock.set()
        async with lock:
            yield

    async def _fake_create_subprocess(*args, **kwargs):
        nonlocal spawn_count
        spawn_count += 1
        invocation_number = spawn_count
        mock_proc = MagicMock()

        async def _communicate(*_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
            if invocation_number == 1:
                first_communicating.set()
                await release_first.wait()
            else:
                second_spawned.set()
            return _make_ok_proc_bytes(), b""

        mock_proc.communicate = _communicate
        mock_proc.returncode = 0
        mock_proc.pid = os.getpid()
        return mock_proc

    with (
        patch(_EXEC, side_effect=_fake_create_subprocess),
        patch("butlers.core.runtimes.codex._codex_refresh_lock", _serializing_lock),
    ):
        first_task = asyncio.create_task(
            adapter1.invoke(prompt="a", system_prompt="", mcp_servers={}, env={})
        )
        second_task: asyncio.Task[object] | None = None
        try:
            await asyncio.wait_for(first_communicating.wait(), timeout=0.2)
            second_task = asyncio.create_task(
                adapter2.invoke(prompt="b", system_prompt="", mcp_servers={}, env={})
            )
            await asyncio.wait_for(second_waiting_for_lock.wait(), timeout=0.2)
            assert not second_spawned.is_set()
        finally:
            release_first.set()
            await asyncio.gather(
                first_task,
                *(() if second_task is None else (second_task,)),
                return_exceptions=True,
            )

    assert second_spawned.is_set()


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

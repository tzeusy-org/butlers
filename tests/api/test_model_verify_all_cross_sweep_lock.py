"""Two verify-all sweeps never probe the same allowance account at once (bu-3rd8wy).

The manual endpoint and the hourly job run in different processes, so exclusion is a Postgres
advisory lock taken inside ``run_verify_all_models``.  A second sweep is refused (endpoint, 429)
or skipped (job) while one holds the lock; the lock is released when the sweep ends, however it
ends.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.routers import model_settings
from butlers.core.runtime_probe_control.coordinator import ProbeResult, ProbeStatus
from butlers.jobs import model_verify

pytestmark = pytest.mark.unit


class _LockServer:
    """Stands in for one Postgres server's advisory-lock table, shared by several 'sessions'."""

    def __init__(self) -> None:
        self.held: set[str] = set()
        self.unlocks = 0

    def pool(self, rows: list[dict[str, Any]]) -> AsyncMock:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows)

        async def fetchval(sql: str, key: str) -> bool:
            if "pg_try_advisory_lock" in sql:
                if key in self.held:
                    return False
                self.held.add(key)
                return True
            assert "pg_advisory_unlock" in sql
            self.unlocks += 1
            return self.held.discard(key) is None

        connection = AsyncMock()
        connection.fetchval = fetchval
        acquired = MagicMock()
        acquired.__aenter__ = AsyncMock(return_value=connection)
        acquired.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acquired)
        return pool


class _GatedClient:
    """Probes block on ``release`` so a second sweep can start while the first is mid-probe."""

    def __init__(self, release: asyncio.Event, *, explode: bool = False) -> None:
        self._release = release
        self._explode = explode
        self.in_flight = 0
        self.peak = 0
        self.asked: list[uuid.UUID] = []

    async def probe(self, entry_id: uuid.UUID) -> ProbeResult:
        self.asked.append(entry_id)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await self._release.wait()
            if self._explode:
                raise RuntimeError("probe blew up")
            return ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=1)
        finally:
            self.in_flight -= 1


def _rows(count: int = 2) -> list[dict[str, Any]]:
    return [
        {"id": uuid.uuid4(), "runtime_type": "codex", "allowance_account": "acct-a"}
        for _ in range(count)
    ]


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(model_settings.audit, "append", AsyncMock(return_value=1))


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_second_sweep_is_refused_while_the_first_holds_the_lock(monkeypatch) -> None:
    server, release = _LockServer(), asyncio.Event()
    client = _GatedClient(release)
    monkeypatch.setattr(model_settings, "_probe_client", lambda caller: client)
    rows = _rows()

    first = asyncio.create_task(model_settings.run_verify_all_models(server.pool(rows)))
    await _settle()
    with pytest.raises(model_settings.VerifyAllAlreadyRunning):
        await model_settings.run_verify_all_models(server.pool(rows), audit_actor="model_verify")
    release.set()
    result = await first

    assert result.total == 2 and result.ok == 2
    assert client.peak == 1  # same account, one probe at a time, and only the first sweep asked
    assert len(client.asked) == 2


async def test_lock_is_released_after_a_clean_sweep_so_the_next_one_runs(monkeypatch) -> None:
    server, release = _LockServer(), asyncio.Event()
    release.set()
    monkeypatch.setattr(model_settings, "_probe_client", lambda caller: _GatedClient(release))

    for _ in range(2):
        result = await model_settings.run_verify_all_models(server.pool(_rows(1)))
        assert result.total == 1

    assert server.held == set() and server.unlocks == 2


async def test_lock_is_released_when_the_sweep_raises(monkeypatch) -> None:
    server, release = _LockServer(), asyncio.Event()
    release.set()
    monkeypatch.setattr(
        model_settings, "_probe_client", lambda caller: _GatedClient(release, explode=True)
    )

    with pytest.raises(RuntimeError, match="probe blew up"):
        await model_settings.run_verify_all_models(server.pool(_rows(1)))

    assert server.held == set() and server.unlocks == 1


async def test_a_refused_sweep_probes_and_audits_nothing(monkeypatch) -> None:
    server, release = _LockServer(), asyncio.Event()
    server.held.add(model_settings._VERIFY_ALL_LOCK_KEY)
    client = _GatedClient(release)
    monkeypatch.setattr(model_settings, "_probe_client", lambda caller: client)
    pool = server.pool(_rows())

    with pytest.raises(model_settings.VerifyAllAlreadyRunning):
        await model_settings.run_verify_all_models(pool)

    pool.fetch.assert_not_awaited()
    model_settings.audit.append.assert_not_awaited()
    assert client.asked == [] and server.unlocks == 0  # never unlocks a lock it did not take


async def test_endpoint_answers_429_when_another_sweep_holds_the_lock(monkeypatch) -> None:
    monkeypatch.setattr(model_settings, "_verify_all_in_flight", False)
    monkeypatch.setattr(model_settings, "_verify_all_last_run", 0.0)
    monkeypatch.setattr(
        model_settings,
        "run_verify_all_models",
        AsyncMock(side_effect=model_settings.VerifyAllAlreadyRunning("held")),
    )
    monkeypatch.setattr(model_settings, "_shared_pool", lambda db: object())
    app = FastAPI()
    app.include_router(model_settings.catalog_router)
    app.dependency_overrides[model_settings.require_dashboard_owner_control] = lambda: "owner"
    app.dependency_overrides[model_settings._get_db_manager] = lambda: MagicMock()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/settings/models/verify-all")

    assert response.status_code == 429
    assert "already running" in response.json()["detail"]
    # A refused sweep must not start the one-minute rate-limit window.
    assert model_settings._verify_all_last_run == 0.0
    assert model_settings._verify_all_in_flight is False


async def test_hourly_job_skips_the_tick_when_another_sweep_holds_the_lock(monkeypatch) -> None:
    db = MagicMock()
    db.credential_shared_pool.return_value = object()
    monkeypatch.setattr(
        model_settings,
        "run_verify_all_models",
        AsyncMock(side_effect=model_settings.VerifyAllAlreadyRunning("held")),
    )

    assert await model_verify.run_model_verify_sweep(db) is None


docker_available = shutil.which("docker") is not None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
async def test_real_postgres_second_session_cannot_start_a_sweep_while_one_runs(
    provisioned_postgres_pool, monkeypatch
) -> None:
    release = asyncio.Event()
    client = _GatedClient(release)
    monkeypatch.setattr(model_settings, "_probe_client", lambda caller: client)

    async with provisioned_postgres_pool(max_pool_size=5) as pool:
        await pool.execute(
            "CREATE TABLE IF NOT EXISTS public.model_catalog ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), runtime_type text NOT NULL, "
            "allowance_account text, enabled boolean NOT NULL DEFAULT true)"
        )
        await pool.execute(
            "INSERT INTO public.model_catalog (runtime_type, allowance_account) "
            "VALUES ('codex', 'acct-a'), ('codex', 'acct-a')"
        )

        first = asyncio.create_task(model_settings.run_verify_all_models(pool))
        while not client.asked:
            await asyncio.sleep(0.01)
        with pytest.raises(model_settings.VerifyAllAlreadyRunning):
            await model_settings.run_verify_all_models(pool, audit_actor="model_verify_sweep")
        assert len(client.asked) == 1 and client.peak == 1

        release.set()
        assert (await first).total == 2
        # Released: the next sweep runs.
        assert (await model_settings.run_verify_all_models(pool)).total == 2

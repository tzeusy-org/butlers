"""Semantic fleet readiness controller and public ``GET /ready`` (REQ-dashboard-api-063).

The controller samples fakes for PostgreSQL, the supervised-job projection and
Switchboard's internal route preflight; the mounted cases prove the public
request reads only the cached snapshot.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import ButlerConnectionInfo
from butlers.api.readiness import CHECK_KEYS, FleetReadiness, ReadinessSnapshot
from butlers.core.control_plane_identity import ShadowCycle
from butlers.core.qa.patrol_provenance import QaPatrolContract

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
_CONTRACT = QaPatrolContract(enabled_sources=("infra_state",), patrol_interval_minutes=10)
_NAMES = ("general", "health", "qa", "switchboard")


def _configs() -> list[ButlerConnectionInfo]:
    return [
        ButlerConnectionInfo(
            name=name,
            port=41100 + index,
            qa_patrol_contract=_CONTRACT if name == "qa" else None,
        )
        for index, name in enumerate(_NAMES)
    ]


def _row(name: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "name": name,
        "policy_state": "active",
        "observed_state": "healthy",
        "boot_epoch": 3,
        "observed_boot_epoch": 3,
        "route_compatible": True,
        "accepting_routes": True,
        "healthy_observed_at": _NOW - timedelta(seconds=30),
        "liveness_ttl_seconds": 300,
        "server_now": _NOW,
    }
    row.update(overrides)
    return row


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, statement: str) -> None:
        return None

    async def fetch(self, statement: str, *args: Any) -> list[dict[str, Any]]:
        return await self._pool.fetch(statement, *args)


class _Pool:
    def __init__(self, rows: list[dict[str, Any]], last_patrol: datetime | None) -> None:
        self.rows, self.last_patrol = rows, last_patrol
        self.down = False

    async def fetchval(self, statement: str, *args: Any) -> Any:
        if self.down:
            raise OSError("db down")
        if statement == "SELECT 1":
            return 1
        if "qa_patrols" in statement:
            return self.last_patrol
        return _NOW  # clock_timestamp()

    async def fetch(self, statement: str, *args: Any) -> list[dict[str, Any]]:
        if self.down:
            raise OSError("db down")
        return self.rows

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield _Conn(self)


class _Jobs:
    def __init__(self, projection: dict[str, Any]) -> None:
        self.projection = projection

    async def read(self) -> dict[str, Any]:
        return self.projection


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _preflight(response: httpx.Response | Exception):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if isinstance(response, Exception):
            raise response
        return response

    return calls, lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _readiness(
    *,
    rows: list[dict[str, Any]] | None = None,
    last_patrol: datetime | None = _NOW - timedelta(minutes=12),
    jobs: dict[str, Any] | None = None,
    preflight: httpx.Response | Exception | None = None,
    cycle_complete: bool = True,
) -> tuple[FleetReadiness, _Pool, _Clock, list[str]]:
    pool = _Pool([_row(name) for name in _NAMES] if rows is None else rows, last_patrol)
    clock = _Clock()
    calls, client_factory = _preflight(
        preflight if preflight is not None else httpx.Response(200, json={"ready": True})
    )
    readiness = FleetReadiness(
        lambda: pool,
        _configs(),
        _Jobs(  # type: ignore[arg-type]
            jobs
            if jobs is not None
            else {
                "jobs_available": True,
                "jobs": [{"name": "fleet_shadow_observer", "state": "ok"}],
            }
        ),
        clock=clock,
        client_factory=client_factory,
    )
    readiness.note_cycle(ShadowCycle(cycle_complete, 4, 4, 0, 4))
    return readiness, pool, clock, calls


def _replace(name: str, **overrides: Any) -> list[dict[str, Any]]:
    return [_row(n, **overrides) if n == name else _row(n) for n in _NAMES]


async def test_all_fresh_inputs_are_ready_and_paused_daemon_is_an_intentional_exclusion():
    readiness, _, _, calls = _readiness(
        rows=_replace(
            "health",
            policy_state="paused",
            observed_state="unavailable",
            healthy_observed_at=_NOW - timedelta(days=2),
        )
    )
    snapshot = await readiness.sample()
    assert snapshot.ready is True
    assert readiness.verdict() == (True, dict.fromkeys(CHECK_KEYS, True))
    assert calls == ["/internal/control-plane/route-preflight"]


@pytest.mark.parametrize(
    ("scenario", "kwargs", "failed"),
    [
        (
            "stale fleet",
            {"rows": _replace("general", healthy_observed_at=_NOW - timedelta(seconds=301))},
            {"fleet"},
        ),
        ("bad epoch", {"rows": _replace("general", observed_boot_epoch=2)}, {"fleet"}),
        ("not accepting", {"rows": _replace("general", accepting_routes=False)}, {"fleet"}),
        ("quarantined", {"rows": _replace("general", policy_state="quarantined")}, {"fleet"}),
        (
            "review required",
            {"rows": _replace("general", policy_state="review_required")},
            {"fleet"},
        ),
        (
            "absent roster record",
            {"rows": [_row(n) for n in _NAMES if n != "general"]},
            {"roster", "fleet", "observer"},
        ),
        ("incomplete observer cycle", {"cycle_complete": False}, {"observer"}),
        ("no qualifying patrol", {"last_patrol": None}, {"qa_patrol"}),
        (
            "overdue patrol",
            {"last_patrol": _NOW - timedelta(minutes=21)},
            {"qa_patrol"},
        ),
        (
            "supervisor projection unavailable",
            {"jobs": {"jobs_available": False, "jobs": []}},
            {"supervisors"},
        ),
        (
            "observer loop stale",
            {
                "jobs": {
                    "jobs_available": True,
                    "jobs": [{"name": "fleet_shadow_observer", "state": "stale"}],
                }
            },
            {"supervisors"},
        ),
        (
            "preflight denied",
            {"preflight": httpx.Response(200, json={"ready": False, "failure_category": "x"})},
            {"route_canary"},
        ),
        ("preflight unavailable", {"preflight": httpx.Response(503)}, {"route_canary"}),
        ("preflight timeout", {"preflight": httpx.ReadTimeout("t")}, {"route_canary"}),
    ],
)
async def test_each_failed_input_fails_only_its_own_check(scenario, kwargs, failed):
    readiness, _, _, _ = _readiness(**kwargs)
    snapshot = await readiness.sample()
    assert snapshot.ready is False, scenario
    assert {key for key, ok in snapshot.checks.items() if not ok} == failed


async def test_database_outage_and_stale_snapshot_fail_closed():
    readiness, pool, clock, _ = _readiness()
    assert readiness.verdict() == (False, dict.fromkeys(CHECK_KEYS, False))  # never sampled
    assert (await readiness.sample()).ready is True
    clock.now += readiness.sample_interval_s * 3 + 1
    assert readiness.verdict()[0] is False  # sampler stopped: snapshot expired

    pool.down = True
    snapshot = await readiness.sample()
    assert not any(snapshot.checks[key] for key in ("postgres", "roster", "fleet", "qa_patrol"))


def _mounted(readiness: Any) -> Any:
    app = create_app(api_key="owner-key")
    app.state.ready = True
    app.state.readiness = readiness
    return app


async def test_exact_get_ready_is_public_cached_and_independent_of_process_health():
    pool_calls: list[str] = []

    def forbidden_pool() -> Any:
        pool_calls.append("pool")
        raise AssertionError("public /ready must not touch the database")

    clock = _Clock()
    readiness = FleetReadiness(forbidden_pool, _configs(), None, clock=clock)
    app = _mounted(readiness)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get("/ready")
        assert missing.status_code == 503
        assert missing.json() == {"ready": False, "checks": dict.fromkeys(CHECK_KEYS, False)}
        assert (await client.get("/health")).status_code == 200

        readiness._snapshot = ReadinessSnapshot(dict.fromkeys(CHECK_KEYS, True), clock.now)
        ok = await client.get("/ready")
        assert (ok.status_code, ok.json()) == (200, {"ready": True})
        assert ok.headers["cache-control"] == "no-store"

        failing = dict.fromkeys(CHECK_KEYS, True) | {"route_canary": False}
        readiness._snapshot = ReadinessSnapshot(failing, clock.now)
        not_ready = await client.get("/ready")
        assert not_ready.status_code == 503
        # Fixed Boolean categories only: no names, endpoints, or diagnostics.
        assert not_ready.json() == {"ready": False, "checks": failing}

        # The exception is exact: another method on /ready is refused by owner
        # auth exactly like a protected API (no auth service here: 503 closed).
        protected = await client.get("/api/system/background-jobs")
        other_method = await client.post("/ready")
        assert protected.status_code in {401, 503}
        assert (other_method.status_code, other_method.json()) == (
            protected.status_code,
            protected.json(),
        )
    assert pool_calls == []

"""Real-Postgres: a retired butler's legacy liveness episode resolves on the signal. [bu-h1b5un]"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.api.deps import DiscoveryDiagnostics
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

from .test_infra_state_condition_suppression_roundtrip import _cycle, _seed_legacy_liveness

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_ROSTER = frozenset({"health", "finance", "general"})


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=4, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE public.infra_conditions")
    yield p
    await p.close()


async def _state_and_reason(pool: asyncpg.Pool, fingerprint: str) -> tuple[str, str | None]:
    row = await pool.fetchrow(
        "SELECT state, metadata->>'resolution_reason' AS reason "
        "FROM public.infra_conditions WHERE fingerprint = $1",
        fingerprint,
    )
    return row["state"], row["reason"]


async def test_retired_butler_resolves_once_and_skipped_config_keeps_it_open(
    pool: asyncpg.Pool, monkeypatch
) -> None:
    from butlers.core.fleet_conditions import reconcile_fleet_condition

    monkeypatch.setenv("BUTLERS_FLEET_CONDITION_HANDOFF", "1")
    fingerprint = await _seed_legacy_liveness(pool, "retired")

    dirty = DiscoveryDiagnostics(
        roster_found=True, directories=frozenset(_ROSTER), failed=frozenset({"broken"})
    )
    await reconcile_fleet_condition(pool, _cycle(True, {}, expected=3), _ROSTER, dirty.is_retired)
    assert (await _state_and_reason(pool, fingerprint))[0] != "resolved"

    present = DiscoveryDiagnostics(roster_found=True, directories=frozenset({*_ROSTER, "retired"}))
    await reconcile_fleet_condition(pool, _cycle(True, {}, expected=3), _ROSTER, present.is_retired)
    assert (await _state_and_reason(pool, fingerprint))[0] != "resolved"

    clean = DiscoveryDiagnostics(roster_found=True, directories=frozenset(_ROSTER))
    await reconcile_fleet_condition(pool, _cycle(True, {}, expected=3), _ROSTER, clean.is_retired)
    assert await _state_and_reason(pool, fingerprint) == ("resolved", "butler_retired_from_roster")

    await reconcile_fleet_condition(pool, _cycle(True, {}, expected=3), _ROSTER, clean.is_retired)
    assert await _state_and_reason(pool, fingerprint) == ("resolved", "butler_retired_from_roster")

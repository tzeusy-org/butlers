"""The replay receipt against a real migrated Postgres (REQ-database-security-008).

The property this file exists for cannot be observed with a mocked pool: two
concurrent uses of one capability must produce exactly ONE receipt, exactly ONE
lookup-launch-persist path, and exactly ONE verification write --- and the loser
must be told it replayed rather than being queued behind the winner.

That is a claim about the unique constraint and about what Postgres does to a
second inserter of the same key while the first transaction is still open.  A
fake ``claim()`` that returns ``False`` on a set membership check proves the
coordinator handles the answer; only the database proves the answer is right.

The same goes for the two boundaries around it: a receipt survives a restart
(here, a brand-new pool with no shared state), and the retention trigger
refuses a DELETE before ``capability_exp + 5s`` rather than letting a cleanup
worker quietly reopen a live replay window.

REQ-core-credentials-002 supplies the capability that drives all of it, and
REQ-dashboard-model-settings-001 supplies the coordinator whose side effects
are counted.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

import butlers.core.runtime_probe_control.capability as cap
from butlers.core.runtime_probe_control.coordinator import ProbeStatus, RuntimeProbeCoordinator
from butlers.core.runtime_probe_control.keys import (
    VerifierSnapshot,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.core.runtime_probe_control.receipts import (
    RECEIPTS_TABLE,
    RuntimeProbeControlReceipts,
    nonce_digest,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    signer_document,
    synthetic_keypair,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_KID = "probe-db-current"
_SWITCHBOARD = "butler_switchboard_rw"


async def _as_switchboard(connection: asyncpg.Connection) -> None:
    """asyncpg pool ``setup`` callback: every acquired connection runs as
    Switchboard, the only role this table's core_201/core_212 RLS policy
    admits (per-connection, since a pool hands out different connections)."""
    await connection.execute(f'SET ROLE "{_SWITCHBOARD}"')


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """The migration/owner identity: fixture data setup and cleanup only.

    core_212 (bu-jcym4) FORCEs row security on the receipts table, so the
    owner is no longer an effective stand-in for Switchboard there -- see the
    ``switchboard_pool`` fixture below, which is what every receipts-table
    read, write, and coordinator run in this file actually goes through.
    """
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=2, max_size=6, init=register_jsonb_codec
    )
    # core_212 also blocks TRUNCATE on this table outright, owner included, so
    # per-test isolation has to step around that guard explicitly rather than
    # rely on TRUNCATE being unguarded -- the table owner can still disable its
    # own trigger, which is the same "not an unforgeable principal" caveat
    # REQ-database-security-007 already records for this shared-login topology.
    async with p.acquire() as connection:
        async with connection.transaction():
            await connection.execute(f"ALTER TABLE {RECEIPTS_TABLE} DISABLE TRIGGER USER")
            await connection.execute(f"TRUNCATE TABLE {RECEIPTS_TABLE}")
            await connection.execute(f"ALTER TABLE {RECEIPTS_TABLE} ENABLE TRIGGER USER")
    yield p
    await p.close()


@pytest.fixture
async def switchboard_pool(migrated_db_url: str):
    """The identity every receipts-table operation in production runs as.

    core_212 fenced the table owner out of this table's RLS policy, so the
    owner pool above can no longer stand in for Switchboard here; using it
    would either raise (INSERT/UPDATE) or silently read/delete zero rows
    (SELECT/DELETE) instead of exercising the real boundary.
    """
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=2, max_size=6, init=register_jsonb_codec, setup=_as_switchboard
    )
    yield p
    await p.close()


@pytest.fixture
def keys(tmp_path):
    """A synthetic signer and the keyring that accepts it, generated in-test."""
    now = datetime.now(UTC)
    seed, public_key = synthetic_keypair()
    signer = parse_signer_document(
        json.dumps(signer_document(seed, kid=_KID, sign_from=now - timedelta(days=1))).encode()
    )
    keyring = parse_verifier_keyring_document(
        json.dumps(
            keyring_document(current_entry(public_key, kid=_KID, sign_from=now - timedelta(days=1)))
        ).encode()
    )
    return signer, keyring


class _CountingPersistence:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        catalog_entry_id: UUID,
        ok: bool,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> bool:
        self.records.append({"catalog_entry_id": catalog_entry_id, "ok": ok})
        return bool(
            await self._pool.fetchval(
                "SELECT public.record_runtime_probe_verification($1, $2, $3, $4)",
                catalog_entry_id,
                ok,
                latency_ms,
                None if ok else error,
            )
        )


def _sign_dashboard_capability(signer, entry_id: UUID) -> str:
    """Mint one dashboard capability at the real clock, the only clock here."""
    # live-clock: RuntimeProbeCoordinator.run() verifies against its own
    # datetime.now(UTC) and the receipts retention trigger compares against
    # Postgres now(); neither takes an injected instant, so a capability signed
    # at any other instant is already expired by the time it is used.
    return cap.sign_capability(
        signer, caller="dashboard", catalog_entry_id=entry_id, now=datetime.now(UTC)
    )


async def _seed_entry(pool: asyncpg.Pool) -> UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.model_catalog (alias, runtime_type, model_id, extra_args, enabled)
        VALUES ($1, 'claude', 'claude-sonnet-4', '[]'::jsonb, true)
        RETURNING id
        """,
        f"probe-{uuid4().hex[:8]}",
    )


def _coordinator(switchboard_pool: asyncpg.Pool, keyring, persistence, *, launches: list[str]):
    """A coordinator whose only fake is the runtime launch itself.

    Takes the Switchboard-role pool, not the owner one: every real Switchboard
    daemon connection SETs ROLE once for the whole pool (``src/butlers/db.py``),
    so the coordinator's catalog reads, its receipts, and its persistence path
    all run as the same principal here too.
    """

    class _Adapter:
        async def invoke(self, **_kwargs: Any):
            launches.append("launch")
            # Hold the slot open long enough for a concurrent request to arrive.
            await asyncio.sleep(0.05)
            return "OK", [], None

    async def _factory(*_args: Any, **_kwargs: Any):
        return _Adapter()

    return RuntimeProbeCoordinator(
        switchboard_pool,
        verifier=lambda: VerifierSnapshot(keyring=keyring),
        receipts=RuntimeProbeControlReceipts(switchboard_pool),
        persistence=persistence,
        adapter_factory=_factory,
    )


async def _verification(pool: asyncpg.Pool, entry_id: UUID) -> asyncpg.Record:
    return await pool.fetchrow(
        """
        SELECT last_verified_at, last_verified_ok, last_verified_latency_ms, last_verified_error
        FROM public.model_catalog WHERE id = $1
        """,
        entry_id,
    )


# ---------------------------------------------------------------------------
# The race
# ---------------------------------------------------------------------------


async def test_two_uses_of_one_capability_commit_one_receipt_and_run_one_probe(
    pool, switchboard_pool, keys
):
    """Criterion 2: one receipt, one lookup-launch-persist path, one loser."""
    signer, keyring = keys
    entry_id = await _seed_entry(pool)
    persistence = _CountingPersistence(switchboard_pool)
    launches: list[str] = []
    coordinator = _coordinator(switchboard_pool, keyring, persistence, launches=launches)

    compact = _sign_dashboard_capability(signer, entry_id)

    first, second = await asyncio.gather(coordinator.run(compact), coordinator.run(compact))

    statuses = sorted(result.status.value for result in (first, second))
    assert statuses == ["completed", "replay"]
    assert launches == ["launch"], "the replayed capability reached the runtime"
    assert len(persistence.records) == 1

    receipts = await switchboard_pool.fetchval(f"SELECT count(*) FROM {RECEIPTS_TABLE}")
    assert receipts == 1

    row = await _verification(pool, entry_id)
    assert row["last_verified_ok"] is True
    assert row["last_verified_error"] is None


async def test_the_receipt_stores_a_digest_and_never_the_nonce(pool, switchboard_pool, keys):
    """A leaked receipt table must not be reconstructible into a capability."""
    signer, keyring = keys
    entry_id = await _seed_entry(pool)
    coordinator = _coordinator(
        switchboard_pool, keyring, _CountingPersistence(switchboard_pool), launches=[]
    )

    compact = _sign_dashboard_capability(signer, entry_id)
    # live-clock: read back at the same real clock the capability was signed at,
    # so this reads the nonce the coordinator will have consumed.
    verified = cap.verify_capability(compact, keyring=keyring, now=datetime.now(UTC))
    await coordinator.run(compact)

    row = await switchboard_pool.fetchrow(f"SELECT * FROM {RECEIPTS_TABLE}")
    assert row["nonce_digest"] == nonce_digest(verified.nonce)
    assert row["kid"] == _KID

    # Asserted by absence: neither the nonce nor any signature segment appears
    # anywhere in the stored row, in any column.
    stored = " ".join(repr(value) for value in dict(row).values())
    assert repr(verified.nonce) not in stored
    for segment in compact.split("."):
        assert segment not in stored


async def test_a_replay_is_still_denied_after_a_restart(
    pool, switchboard_pool, keys, migrated_db_url
):
    """Criterion 2: the receipt is durable, not process state.

    The second coordinator runs on a brand-new pool with no shared memory ---
    the same situation Switchboard is in after a restart.
    """
    signer, keyring = keys
    entry_id = await _seed_entry(pool)
    launches: list[str] = []
    first = _coordinator(
        switchboard_pool, keyring, _CountingPersistence(switchboard_pool), launches=launches
    )

    compact = _sign_dashboard_capability(signer, entry_id)
    assert (await first.run(compact)).status is ProbeStatus.COMPLETED

    restarted_pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        setup=_as_switchboard,
    )
    try:
        persistence = _CountingPersistence(restarted_pool)
        restarted = _coordinator(restarted_pool, keyring, persistence, launches=launches)
        result = await restarted.run(compact)
    finally:
        await restarted_pool.close()

    assert result.status is ProbeStatus.REPLAY
    assert launches == ["launch"]
    assert persistence.records == []


async def test_a_replay_leaves_existing_verification_history_unchanged(
    pool, switchboard_pool, keys
):
    """Criterion 7: a rejected request writes no failure and no success."""
    signer, keyring = keys
    entry_id = await _seed_entry(pool)
    coordinator = _coordinator(
        switchboard_pool, keyring, _CountingPersistence(switchboard_pool), launches=[]
    )

    compact = _sign_dashboard_capability(signer, entry_id)
    await coordinator.run(compact)
    before = dict(await _verification(pool, entry_id))

    replay = await coordinator.run(compact)

    assert replay.status is ProbeStatus.REPLAY
    assert dict(await _verification(pool, entry_id)) == before


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


async def test_cleanup_refuses_to_delete_a_receipt_inside_the_replay_window(switchboard_pool):
    """The trigger, not the caller's predicate, is what keeps the window closed."""
    receipts = RuntimeProbeControlReceipts(switchboard_pool)
    now = datetime.now(UTC)
    nonce = b"\x01" * 32
    assert await receipts.claim(nonce=nonce, kid=_KID, expires_at=now + timedelta(seconds=30))

    with pytest.raises(asyncpg.PostgresError):
        await switchboard_pool.execute(f"DELETE FROM {RECEIPTS_TABLE}")

    assert await receipts.is_consumed(nonce=nonce)


@pytest.mark.pg_clock
async def test_the_retention_bound_is_expiry_plus_five_seconds(switchboard_pool):
    """Criterion 2: retained through ``exp + 5s``, deletable only after.

    Both offsets are real-clock offsets because the trigger is: it compares
    ``OLD.capability_exp`` against the database's own ``now()``, so passing
    ``purge_expired`` a simulated clock moves the caller's predicate but not
    the bound underneath it.  Four and six seconds sit a full second either
    side of the boundary, which no plausible round-trip delay closes.
    """
    receipts = RuntimeProbeControlReceipts(switchboard_pool)
    now = datetime.now(UTC)
    inside, outside = b"\x02" * 32, b"\x05" * 32
    assert await receipts.claim(nonce=inside, kid=_KID, expires_at=now - timedelta(seconds=4))
    assert await receipts.claim(nonce=outside, kid=_KID, expires_at=now - timedelta(seconds=6))

    # The caller's own predicate takes only the receipt past the bound.
    assert await receipts.purge_expired() == 1
    assert await receipts.is_consumed(nonce=inside)
    assert not await receipts.is_consumed(nonce=outside)

    # And the trigger holds that same line against a caller who asks directly,
    # which is what stops a wrong cleanup predicate reopening a replay window.
    with pytest.raises(asyncpg.PostgresError):
        await switchboard_pool.execute(
            f"DELETE FROM {RECEIPTS_TABLE} WHERE nonce_digest = $1", nonce_digest(inside)
        )
    assert await receipts.is_consumed(nonce=inside)


@pytest.mark.pg_clock
async def test_purging_an_expired_receipt_does_not_free_a_live_one(switchboard_pool):
    receipts = RuntimeProbeControlReceipts(switchboard_pool)
    # live-clock: the retention trigger refuses a DELETE before capability_exp
    # + 5s using Postgres now(), so the claimed expiries have to straddle that
    # same real instant for the purge predicate to be under test at all.
    now = datetime.now(UTC)
    stale, live = b"\x03" * 32, b"\x04" * 32
    await receipts.claim(nonce=stale, kid=_KID, expires_at=now - timedelta(minutes=1))
    await receipts.claim(nonce=live, kid=_KID, expires_at=now + timedelta(seconds=30))

    assert await receipts.purge_expired(now=now) == 1
    assert not await receipts.is_consumed(nonce=stale)
    assert await receipts.is_consumed(nonce=live)

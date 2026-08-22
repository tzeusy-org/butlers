"""Real-Postgres coverage for at-most-once runtime-attention delivery.

REQ-runtime-attention-outbox-002; REQ-core-notify-027;
REQ-database-security-007.

Every assertion here runs against the production substrate — the real guard
triggers, the real RLS policies, and the real column grants — because the
at-most-once guarantee is a property of the *database plus* the worker, not of
either alone.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from butlers.tools.switchboard.routing.transport import (
    CONFIRMED,
    PROVIDER_REJECTED,
    RECIPIENT_UNAVAILABLE,
    TransportOutcome,
    TransportResult,
)
from butlers.tools.switchboard.runtime_attention.outbox import (
    MAX_TRANSPORT_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    OutboxEpisode,
    RuntimeAttentionOutbox,
)
from butlers.tools.switchboard.runtime_attention.worker import (
    RuntimeAttentionDeliveryWorker,
)

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


async def _as_role(pool: asyncpg.Pool, role: str, statement: str, *args: Any) -> Any:
    """Run one statement under an explicit runtime role."""
    async with pool.acquire() as connection:
        await connection.execute(f'SET ROLE "{role}"')
        try:
            return await connection.fetch(statement, *args)
        finally:
            await connection.execute("RESET ROLE")


async def _seed_pending_episode(pool: asyncpg.Pool, alias: str) -> uuid.UUID:
    """Create one pending outbox episode through the real producer.

    The producer is the only thing that may insert here — ``butler_switchboard_rw``
    has no INSERT grant — so the test seeds a genuine open-breaker edge rather
    than fabricating a row.
    """
    catalog_entry_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
        VALUES ($1, $2, 'codex', $3)
        """,
        catalog_entry_id,
        alias,
        f"{alias}-model",
    )
    ts = datetime.now(UTC)
    attempt_id: int | None = None
    for _ in range(5):
        attempt_id = await pool.fetchval(
            """
            INSERT INTO public.model_dispatch_attempts (catalog_entry_id, butler, outcome, ts)
            VALUES ($1, 'general', 'runtime_failure', $2)
            RETURNING id
            """,
            catalog_entry_id,
            ts,
        )

    async with pool.acquire() as connection:
        await connection.execute('SET ROLE "butler_general_rw"')
        try:
            episode_id = await connection.fetchval(
                "SELECT public.append_runtime_attention_model_breaker($1)", attempt_id
            )
        finally:
            await connection.execute("RESET ROLE")
    assert episode_id is not None, "producer must be enabled in the migrated fixture"
    return episode_id


async def _row(pool: asyncpg.Pool, episode_id: uuid.UUID) -> asyncpg.Record:
    """Read one outbox row on a connection the repository is not using."""
    rows = await _as_role(
        pool,
        "butler_switchboard_rw",
        """
        SELECT lifecycle_state, claim_token, claim_epoch, delivery_lease_epoch,
               claimed_by_instance, delivered_at
        FROM public.runtime_attention_outbox
        WHERE id = $1
        """,
        episode_id,
    )
    return rows[0]


class _Spy:
    """A transport that records every call and returns scripted outcomes."""

    def __init__(self, *results: TransportResult, error: BaseException | None = None) -> None:
        self._results = list(results) or [CONFIRMED]
        self._error = error
        self.calls: list[uuid.UUID] = []

    async def __call__(self, episode: OutboxEpisode) -> TransportResult:
        self.calls.append(episode.id)
        if self._error is not None:
            raise self._error
        index = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[index]


def _worker(
    repository: RuntimeAttentionOutbox,
    transport: Any,
    sleeps: list[float] | None = None,
) -> RuntimeAttentionDeliveryWorker:
    async def _sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return RuntimeAttentionDeliveryWorker(repository, transport, sleep=_sleep)


# ---------------------------------------------------------------------------
# AC 1 — lease, fenced claim, pre-commit, recheck, single transport, retry
# ---------------------------------------------------------------------------


async def test_only_one_delivery_service_lease_is_active(migrated_core_postgres_pool) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        first = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        second = RuntimeAttentionOutbox(pool, instance_id="worker-b")

        lease_a = await first.acquire_service_lease()
        assert lease_a is not None
        assert lease_a.epoch == 1

        assert await second.acquire_service_lease() is None, "two live delivery services"

        assert await first.release_service_lease(lease_a) is True
        lease_b = await second.acquire_service_lease()
        assert lease_b is not None
        # The epoch is monotonic across handovers — that is what makes it a
        # usable recovery fence rather than a mere mutex.
        assert lease_b.epoch == 2


async def test_claim_commits_sending_before_transport(migrated_core_postgres_pool) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        episode_id = await _seed_pending_episode(pool, "claim-precommit")
        repository = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        lease = await repository.acquire_service_lease()
        assert lease is not None

        episode = await repository.claim_next_pending(lease)
        assert episode is not None
        assert episode.id == episode_id

        # Read on a separate connection: if the claim were not committed, a
        # crash before transport would leave a row that looks deliverable.
        row = await _row(pool, episode_id)
        assert row["lifecycle_state"] == "sending"
        assert row["claim_token"] == episode.claim_token
        assert row["claim_epoch"] == 1
        assert row["delivery_lease_epoch"] == lease.epoch
        assert row["claimed_by_instance"] == "worker-a"

        assert await repository.claim_is_current(episode) is True


async def test_a_second_claim_finds_nothing_and_transport_runs_once(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=4, max_pool_size=8) as pool:
        episode_id = await _seed_pending_episode(pool, "single-transport")
        repo_a = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        repo_b = RuntimeAttentionOutbox(pool, instance_id="worker-b")
        transport = _Spy(CONFIRMED)

        cycles = await asyncio.gather(
            _worker(repo_a, transport).run_once(),
            _worker(repo_b, transport).run_once(),
        )

        assert transport.calls == [episode_id], "the episode must leave exactly once"
        assert sum(cycle.delivered for cycle in cycles) == 1
        row = await _row(pool, episode_id)
        assert row["lifecycle_state"] == "sent"
        assert row["delivered_at"] is not None


async def test_proven_not_attempted_retries_are_bounded(migrated_core_postgres_pool) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        episode_id = await _seed_pending_episode(pool, "bounded-retry")
        repository = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        transport = _Spy(RECIPIENT_UNAVAILABLE)
        sleeps: list[float] = []

        cycle = await _worker(repository, transport, sleeps).run_once()

        assert len(transport.calls) == MAX_TRANSPORT_ATTEMPTS == 3
        assert sleeps == list(RETRY_BACKOFF_SECONDS) == [1.0, 5.0]
        assert cycle.outcomes == (TransportOutcome.NOT_ATTEMPTED,)
        # Exhausted *proven* not-attempted is terminal-failed, not uncertain:
        # nothing ever left the process, so there is nothing ambiguous.
        assert (await _row(pool, episode_id))["lifecycle_state"] == "failed"


async def test_rejected_and_uncertain_outcomes_are_not_retried(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        rejected_id = await _seed_pending_episode(pool, "terminal-rejected")
        repository = RuntimeAttentionOutbox(pool, instance_id="worker-a")

        transport = _Spy(PROVIDER_REJECTED)
        await _worker(repository, transport).run_once()
        assert len(transport.calls) == 1, "a peer refusal is terminal"
        assert (await _row(pool, rejected_id))["lifecycle_state"] == "failed"

        uncertain_id = await _seed_pending_episode(pool, "terminal-uncertain")
        timeout_transport = _Spy(error=TimeoutError("deadline"))
        cycle = await _worker(repository, timeout_transport).run_once()
        assert len(timeout_transport.calls) == 1, "an ambiguous send must never repeat"
        assert cycle.outcomes == (TransportOutcome.UNCERTAIN,)
        assert (await _row(pool, uncertain_id))["lifecycle_state"] == "uncertain"


async def test_terminal_transition_requires_the_current_claim(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        episode_id = await _seed_pending_episode(pool, "stale-token")
        repository = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        lease = await repository.acquire_service_lease()
        assert lease is not None
        episode = await repository.claim_next_pending(lease)
        assert episode is not None

        stale = dataclasses.replace(episode, claim_token=uuid.uuid4())
        assert await repository.mark_sent(stale) is False
        assert (await _row(pool, episode_id))["lifecycle_state"] == "sending"

        assert await repository.mark_sent(episode) is True
        assert (await _row(pool, episode_id))["lifecycle_state"] == "sent"


# ---------------------------------------------------------------------------
# AC 2 — crash/restart recovery, and zero transport replay
# ---------------------------------------------------------------------------


async def test_live_lease_holder_prevents_any_recovery(migrated_core_postgres_pool) -> None:
    async with migrated_core_postgres_pool(min_pool_size=3, max_pool_size=6) as pool:
        await _seed_pending_episode(pool, "live-holder")
        repo_a = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        repo_b = RuntimeAttentionOutbox(pool, instance_id="worker-b")

        lease_a = await repo_a.acquire_service_lease()
        assert lease_a is not None
        episode = await repo_a.claim_next_pending(lease_a)
        assert episode is not None

        # A successor cannot even obtain the authority to look.
        assert await repo_b.acquire_service_lease() is None

        # Nor can the holder fence its own live claim: recovery requires an
        # epoch strictly below the current lease, and its own claim is at it.
        assert await repo_a.list_recoverable(lease_a, stale_after_seconds=0.0) == []


async def test_dead_claim_becomes_uncertain_after_sole_lease_and_fencing(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=3, max_pool_size=6) as pool:
        episode_id = await _seed_pending_episode(pool, "crash-recovery")
        repo_a = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        repo_b = RuntimeAttentionOutbox(pool, instance_id="worker-b")

        lease_a = await repo_a.acquire_service_lease()
        assert lease_a is not None
        episode = await repo_a.claim_next_pending(lease_a)
        assert episode is not None
        # Worker A "crashes" mid-send: its lease lapses, its claim does not.
        await repo_a.release_service_lease(lease_a)

        lease_b = await repo_b.acquire_service_lease()
        assert lease_b is not None
        assert lease_b.epoch > episode.delivery_lease_epoch

        claims = await repo_b.list_recoverable(lease_b, stale_after_seconds=0.0)
        assert [claim.id for claim in claims] == [episode_id]
        assert await repo_b.fence_stale_claim(claims[0], lease_b) is True

        row = await _row(pool, episode_id)
        assert row["lifecycle_state"] == "uncertain"
        # The fenced claim identity survives: it is evidence, not scratch space.
        assert row["claim_token"] == episode.claim_token
        assert row["delivery_lease_epoch"] == episode.delivery_lease_epoch

        # A recovered row is never handed back to transport.
        transport = _Spy(CONFIRMED)
        cycle = await _worker(repo_b, transport).run_once()
        assert transport.calls == [], "recovery must not replay a possibly-sent message"
        assert cycle.outcomes == ()
        assert (await _row(pool, episode_id))["lifecycle_state"] == "uncertain"


async def test_row_age_alone_never_authorizes_a_transition(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=3, max_pool_size=6) as pool:
        await _seed_pending_episode(pool, "age-is-not-authority")
        repo_a = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        repo_b = RuntimeAttentionOutbox(pool, instance_id="worker-b")

        lease_a = await repo_a.acquire_service_lease()
        assert lease_a is not None
        episode = await repo_a.claim_next_pending(lease_a)
        assert episode is not None
        await repo_a.release_service_lease(lease_a)

        lease_b = await repo_b.acquire_service_lease()
        assert lease_b is not None

        # Old enough to be *eligible*, but the epoch fence is what decides.
        stale_claims = await repo_b.list_recoverable(lease_b, stale_after_seconds=0.0)
        assert len(stale_claims) == 1

        # A claim whose recorded lease epoch does not match is refused even
        # though the row is exactly as old as the eligible one.
        forged = dataclasses.replace(stale_claims[0], claim_epoch=stale_claims[0].claim_epoch + 1)
        assert await repo_b.fence_stale_claim(forged, lease_b) is False
        assert (await _row(pool, episode.id))["lifecycle_state"] == "sending"

        # And the default window keeps a freshly claimed row out of reach.
        assert await repo_b.list_recoverable(lease_b) == []


# ---------------------------------------------------------------------------
# AC 3 — slow claimant, and the absence of manual reissue
# ---------------------------------------------------------------------------


async def test_fenced_claimant_cannot_mutate_the_recovered_row(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=3, max_pool_size=6) as pool:
        episode_id = await _seed_pending_episode(pool, "slow-claimant")
        repo_a = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        repo_b = RuntimeAttentionOutbox(pool, instance_id="worker-b")

        lease_a = await repo_a.acquire_service_lease()
        assert lease_a is not None
        episode = await repo_a.claim_next_pending(lease_a)
        assert episode is not None
        await repo_a.release_service_lease(lease_a)

        lease_b = await repo_b.acquire_service_lease()
        assert lease_b is not None
        claims = await repo_b.list_recoverable(lease_b, stale_after_seconds=0.0)
        assert await repo_b.fence_stale_claim(claims[0], lease_b) is True

        # Worker A wakes up believing it still owns the episode.
        assert await repo_a.claim_is_current(episode) is False
        assert await repo_a.mark_sent(episode) is False
        assert await repo_a.mark_failed(episode) is False
        assert (await _row(pool, episode_id))["lifecycle_state"] == "uncertain"

        # And a slow claimant that reaches the worker's own send path stops
        # before transport, because the fence is rechecked every attempt.
        transport = _Spy(CONFIRMED)
        assert await _worker(repo_a, transport)._deliver(episode) is None
        assert transport.calls == []


async def test_manual_reissue_is_structurally_unavailable(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        episode_id = await _seed_pending_episode(pool, "no-reissue")
        repository = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        lease = await repository.acquire_service_lease()
        assert lease is not None
        episode = await repository.claim_next_pending(lease)
        assert episode is not None

        # Switchboard holds no INSERT grant, so a successor row cannot be
        # manufactured while the original is still sending — reissue is absent
        # by construction, not by omission.
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await _as_role(
                pool,
                "butler_switchboard_rw",
                """
                INSERT INTO public.runtime_attention_outbox
                    (source, manual_reissue_of, source_snapshot, payload)
                VALUES ('model_breaker', $1, '{"reissue_of": "x"}'::jsonb,
                        '{"classification": "model_breaker_open"}'::jsonb)
                """,
                episode_id,
            )
        assert (await _row(pool, episode_id))["lifecycle_state"] == "sending"


# ---------------------------------------------------------------------------
# AC 5 — effective role isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["butler_general_rw", "butler_messenger_rw"])
async def test_unrelated_runtime_roles_cannot_reach_the_outbox(
    migrated_core_postgres_pool, role: str
) -> None:
    """Only Switchboard's effective role reaches the delivery surface.

    This is an *effective-role* check, not proof of an independently
    authenticated per-butler principal: the deployment shares one login and
    reaches these roles by ``SET ROLE``. What it does prove is that a butler
    running as itself cannot touch the outbox, which is the boundary this lane
    owns; forging the role is a separate, login-level concern.
    """
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        await _seed_pending_episode(pool, f"role-isolation-{role}")

        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await _as_role(pool, role, "SELECT * FROM public.runtime_attention_outbox")
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await _as_role(pool, role, "SELECT * FROM public.runtime_attention_delivery_lease")
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await _as_role(
                pool,
                role,
                "UPDATE public.runtime_attention_outbox SET lifecycle_state = 'sent'",
            )


async def test_switchboard_role_alone_can_inspect_and_claim(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        episode_id = await _seed_pending_episode(pool, "switchboard-only")
        rows = await _as_role(
            pool,
            "butler_switchboard_rw",
            "SELECT id FROM public.runtime_attention_outbox WHERE id = $1",
            episode_id,
        )
        assert [row["id"] for row in rows] == [episode_id]

        repository = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        lease = await repository.acquire_service_lease()
        assert lease is not None
        assert await repository.claim_next_pending(lease) is not None

        # Even Switchboard may not write the dormant evidence columns.
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await _as_role(
                pool,
                "butler_switchboard_rw",
                "UPDATE public.runtime_attention_outbox SET delivery_error_class = 'pre_transport'",
            )


# ---------------------------------------------------------------------------
# AC 7 — rollback preserves evidence and replays nothing
# ---------------------------------------------------------------------------


async def test_stopping_the_worker_preserves_evidence_without_replay(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=3, max_pool_size=6) as pool:
        sent_id = await _seed_pending_episode(pool, "rollback-sent")
        repository = RuntimeAttentionOutbox(pool, instance_id="worker-a")

        confirmed = _Spy(CONFIRMED)
        await _worker(repository, confirmed).run_once()
        assert (await _row(pool, sent_id))["lifecycle_state"] == "sent"

        uncertain_id = await _seed_pending_episode(pool, "rollback-uncertain")
        ambiguous = _Spy(error=ConnectionResetError("reset mid-request"))
        await _worker(repository, ambiguous).run_once()
        assert (await _row(pool, uncertain_id))["lifecycle_state"] == "uncertain"

        # "Stopping the worker" is just not running it. Restarting it must not
        # resurrect either terminal row.
        replay = _Spy(CONFIRMED)
        cycle = await _worker(repository, replay).run_once()
        assert replay.calls == []
        assert cycle.outcomes == ()
        assert (await _row(pool, sent_id))["lifecycle_state"] == "sent"
        assert (await _row(pool, uncertain_id))["lifecycle_state"] == "uncertain"


# ---------------------------------------------------------------------------
# AC 8 — safe telemetry
# ---------------------------------------------------------------------------


async def test_delivery_logs_carry_typed_outcomes_and_no_secrets(
    migrated_core_postgres_pool, caplog: pytest.LogCaptureFixture
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        await _seed_pending_episode(pool, "safe-telemetry")
        repository = RuntimeAttentionOutbox(pool, instance_id="worker-a")
        leaky = _Spy(
            error=RuntimeError(
                "provider rejected chat_id=987654321 bot_token=hunter2 body='Breaker opened'"
            )
        )

        with caplog.at_level(logging.INFO):
            cycle = await _worker(repository, leaky).run_once()

        assert cycle.outcomes == (TransportOutcome.UNCERTAIN,)
        assert "outcome=uncertain" in caplog.text
        for secret in ("hunter2", "987654321", "bot_token", "Breaker opened"):
            assert secret not in caplog.text

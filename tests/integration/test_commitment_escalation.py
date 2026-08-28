"""Real-Postgres proof of the commitment escalation job (bu-n1evl, RFC 0026 §6-§9).

REQ-commitment-lifecycle-005 and -006. tests/jobs/test_commitment_escalation.py
already pins the decisions this job makes without a database — the priority
map, the dedup-key shape, the cadence it borrows from the ledger, the
resolution route it refuses to take. This file covers what only a real ledger
can show:

- the escalation clock actually advancing a commitment nothing re-observes,
- a deadline inside the L0 grace actually collapsing that grace,
- the class filter actually excluding a non-commitment condition sharing the
  ledger,
- and cancellation actually landing ``resolution_reason`` and
  ``evidence_closed`` where a reader will look for them, which since bu-o4i4j
  is top-level in ``metadata`` on every resolution path.

The insight proposer is injected, so most tests here record calls through a
fake. One test deliberately does not: it runs the tick against the real
``propose_insight_candidate`` and a real ``insight_candidates`` table, because
a fake cannot show that the broker accepts the dedup key, the priority, and
the expiry this job builds (REQ-commitment-lifecycle-005's composition
requirement).
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.core.commitments import (
    commitment_fingerprint,
    create_commitment,
    list_active_commitments,
)
from butlers.core.owner_conditions import Observation, reconcile_snapshot
from butlers.jobs.commitment_escalation import (
    ARCHIVAL_DEDUP_SCOPE,
    GC_STALE_DAYS,
    cancel_stale_commitment,
    renew_stale_commitment,
    run_commitment_escalation,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=2, max_size=10)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def clean_ledger(pool: asyncpg.Pool):
    """Empty the shared ledger around every test.

    The per-test ``source`` fixture isolates rows, but this job deliberately
    sweeps every commitment-class row in ``public.owner_conditions`` at once —
    commitments are cross-butler — so its counters are table-wide. Without
    this, one test's leftovers show up in the next test's ``scanned``, and
    every count assertion becomes order-dependent.
    """
    await pool.execute("TRUNCATE public.owner_conditions")
    yield
    await pool.execute("TRUNCATE public.owner_conditions")


@pytest.fixture
def source() -> str:
    """A per-test source so concurrent ledger rows never cross-contaminate.

    Fingerprints are source-scoped, and the job sweeps every commitment-class
    row in the table, so without this each test would see every other test's
    commitments.
    """
    return f"relationship:commitment-{uuid.uuid4().hex[:12]}"


class RecordingProposer:
    """An in-memory stand-in for ``propose_insight_candidate``.

    Records every call and returns whatever the test asked for, so a test can
    assert on the exact candidate the job built without needing the
    Switchboard's ``insight_candidates`` table — and can make the broker fail
    without breaking anything.
    """

    def __init__(self, status: str = "accepted", raises: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status
        self.raises = raises

    async def __call__(self, pool: asyncpg.Pool, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("synthetic broker failure")
        return {"status": self.status, "reason": "synthetic"}

    def keys(self) -> list[str]:
        return [call["dedup_key"] for call in self.calls]

    def for_scope(self, fingerprint: str, scope: str) -> list[dict[str, Any]]:
        suffix = f":{fingerprint}:{scope}"
        return [call for call in self.calls if call["dedup_key"].endswith(suffix)]


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _commit(
    pool: asyncpg.Pool,
    *,
    source: str,
    action: str = "send Sam the book",
    summary: str = "Send Sam the book",
    confidence: float = 0.9,
    deadline: datetime | None = None,
    grace_seconds: float = 24 * 3600.0,
    counterparty: str | None = None,
) -> str:
    """Create one commitment through the real doorway and return its fingerprint."""
    entity_id = counterparty or str(uuid.uuid4())
    await create_commitment(
        pool,
        source=source,
        summary=summary,
        kind="promise",
        direction="owner_to_other",
        counterparty_entity_id=entity_id,
        confidence=confidence,
        evidence_opened={"source": "conversation", "excerpt": "synthetic test utterance"},
        action_description=action,
        deadline=deadline,
        initial_grace_seconds=grace_seconds,
    )
    return commitment_fingerprint(
        source=source,
        counterparty_entity_id=entity_id,
        action_description=action,
    )


async def _age(
    pool: asyncpg.Pool,
    source: str,
    fingerprint: str,
    *,
    level: str,
    silent_days: int,
    due_in: timedelta = timedelta(seconds=-1),
) -> None:
    """Backdate one commitment to a level and an age the clock cannot reach in a test.

    The ledger has no time-travel API and a commitment needs eleven real days
    to reach L3 and ninety more to go stale, so the row is moved directly.
    Everything the job reads is set explicitly rather than left at whatever
    ``create_commitment`` wrote.
    """
    await pool.execute(
        """
        UPDATE public.owner_conditions
        SET escalation_level = $3,
            state = 'aging',
            first_detected_at = now() - ($4 || ' days')::interval,
            last_confirmed_at = now() - ($4 || ' days')::interval,
            last_escalated_at = now() - ($4 || ' days')::interval,
            next_reescalate_at = now() + $5
        WHERE source = $1 AND fingerprint = $2
        """,
        source,
        fingerprint,
        level,
        str(silent_days),
        due_in,
    )


async def _row(pool: asyncpg.Pool, source: str, fingerprint: str) -> dict[str, Any]:
    record = await pool.fetchrow(
        "SELECT * FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2",
        source,
        fingerprint,
    )
    assert record is not None
    row = dict(record)
    raw = row["metadata"]
    row["metadata"] = json.loads(raw) if isinstance(raw, str) else raw
    return row


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — what the job looks at
# ---------------------------------------------------------------------------


class TestScopeOfTheSweep:
    async def test_req_commitment_lifecycle_005_ignores_non_commitment_conditions(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """A bill-overdue row sharing the ledger is never proposed (criterion 1).

        Commitment-ness is a metadata convention rather than a column, so the
        class filter is the only thing standing between this job and every
        other producer's conditions.
        """
        other_source = f"finance:bill-overdue-{uuid.uuid4().hex[:8]}"
        await reconcile_snapshot(
            pool,
            source=other_source,
            observations=[
                Observation(
                    fingerprint="bill-" + uuid.uuid4().hex,
                    summary="Electricity bill overdue",
                    metadata={"class": "bill", "confidence": 0.99},
                )
            ],
            snapshot_complete=False,
            initial_grace_seconds=0.0,
        )
        # Make it as eligible as a commitment could ever be.
        await pool.execute(
            """
            UPDATE public.owner_conditions
            SET escalation_level = 'L3', state = 'aging',
                last_confirmed_at = now() - interval '200 days'
            WHERE source = $1
            """,
            other_source,
        )

        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert proposer.calls == []
        assert result.scanned == 0

    async def test_req_commitment_lifecycle_005_does_not_surface_during_l0_grace(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """L0 is the grace period; a just-created commitment has not earned attention.

        Criterion 2: only L1+ is processed.
        """
        await _commit(pool, source=source, grace_seconds=24 * 3600.0)

        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert result.scanned == 1
        assert result.escalated == 0
        assert proposer.calls == []
        assert (await list_active_commitments(pool, source=source))[0]["escalation_level"] == "L0"

    async def test_req_commitment_lifecycle_005_advances_a_commitment_nothing_reobserves(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """A commitment has no producer, so this job is what moves its clock.

        The ledger advances escalation only when a producer re-observes a
        condition past ``next_reescalate_at``. No scheduled job can decide
        whether the owner sent Sam the book, so without this pass a commitment
        would sit at L0 forever and every "at L1+" requirement would be
        unreachable.
        """
        await _commit(pool, source=source, grace_seconds=0.0)

        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert result.escalated == 1
        assert result.surfaced == 1
        assert (await list_active_commitments(pool, source=source))[0]["escalation_level"] == "L1"

    async def test_req_commitment_lifecycle_005_escalating_does_not_reset_the_silence_clock(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """The tick must not confirm what it escalates.

        ``last_confirmed_at`` is the 90-day garbage-collection clock
        (REQ-commitment-lifecycle-006). A job that confirmed the rows it
        escalated would reset the clock it exists to read, and no commitment
        would ever be collected.
        """
        fingerprint = await _commit(pool, source=source, grace_seconds=0.0)
        before = (await _row(pool, source, fingerprint))["last_confirmed_at"]

        await run_commitment_escalation(pool, insight_proposer=RecordingProposer())

        after = await _row(pool, source, fingerprint)
        assert after["escalation_level"] == "L1"
        assert after["last_confirmed_at"] == before


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-004/-005 — the confidence band
# ---------------------------------------------------------------------------


class TestConfidenceBand:
    async def test_req_commitment_lifecycle_005_medium_confidence_is_created_but_not_surfaced(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Spec scenario: confidence 0.7 at L1 proposes nothing, stays queryable.

        Criterion 3.
        """
        fingerprint = await _commit(pool, source=source, confidence=0.7)
        await _age(pool, source, fingerprint, level="L1", silent_days=2)

        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert proposer.calls == []
        assert result.skipped_low_confidence == 1
        assert result.surfaced == 0
        # "AND the commitment is visible on the dashboard and in prep card queries"
        visible = await list_active_commitments(pool, source=source)
        assert [row["fingerprint"] for row in visible] == [fingerprint]

    async def test_req_commitment_lifecycle_005_high_confidence_is_surfaced_at_l1(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Spec scenario: confidence 0.9 at L1 proposes an insight candidate."""
        fingerprint = await _commit(pool, source=source, confidence=0.9)
        await _age(pool, source, fingerprint, level="L1", silent_days=2)

        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert result.surfaced == 1
        assert len(proposer.calls) == 1

    async def test_req_commitment_lifecycle_005_the_threshold_is_inclusive(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """RFC 0026 §8 bands are ``0.6 <= c < 0.8`` and ``c >= 0.8``, so 0.8 surfaces."""
        fingerprint = await _commit(pool, source=source, confidence=0.8)
        await _age(pool, source, fingerprint, level="L1", silent_days=2)

        result = await run_commitment_escalation(pool, insight_proposer=RecordingProposer())

        assert result.surfaced == 1
        assert result.skipped_low_confidence == 0


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — deadline-aware grace shortening
# ---------------------------------------------------------------------------


class TestDeadlineShortensGrace:
    @pytest.mark.pg_clock
    async def test_req_commitment_lifecycle_005_deadline_inside_grace_surfaces_before_it(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Spec scenario: a deadline inside L0 grace collapses the grace.

        Criterion 4. The grace collapses to now rather than to some margin
        before the deadline because this job ticks daily: any later target
        risks the next tick landing on the far side of the deadline, which is
        the one outcome the requirement forbids.
        """
        deadline = datetime.now(UTC) + timedelta(hours=2)
        fingerprint = await _commit(
            pool,
            source=source,
            deadline=deadline,
            grace_seconds=24 * 3600.0,
        )
        assert (await _row(pool, source, fingerprint))["escalation_level"] == "L0"

        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert result.grace_shortened == 1
        row = await _row(pool, source, fingerprint)
        assert row["escalation_level"] == "L1"
        assert result.surfaced == 1
        # Surfaced strictly before the deadline it was pulled forward for.
        assert datetime.now(UTC) < deadline

    async def test_req_commitment_lifecycle_005_deadline_beyond_grace_is_left_alone(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Grace shortening applies only when the deadline falls inside the window.

        A deadline a month out is not a reason to surface today; the ordinary
        escalation schedule already gets there in time.
        """
        fingerprint = await _commit(
            pool,
            source=source,
            deadline=datetime.now(UTC) + timedelta(days=30),
            grace_seconds=24 * 3600.0,
        )

        result = await run_commitment_escalation(pool, insight_proposer=RecordingProposer())

        assert result.grace_shortened == 0
        assert (await _row(pool, source, fingerprint))["escalation_level"] == "L0"

    async def test_req_commitment_lifecycle_005_grace_shortening_is_l0_only(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Past L0 the grace period no longer exists; the schedule owns the cadence."""
        fingerprint = await _commit(
            pool, source=source, deadline=datetime.now(UTC) + timedelta(hours=1)
        )
        await _age(pool, source, fingerprint, level="L2", silent_days=5, due_in=timedelta(days=3))

        result = await run_commitment_escalation(pool, insight_proposer=RecordingProposer())

        assert result.grace_shortened == 0
        assert (await _row(pool, source, fingerprint))["escalation_level"] == "L2"

    async def test_req_commitment_lifecycle_005_an_unparseable_deadline_does_not_stop_the_sweep(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """One producer's malformed timestamp must not cost every other commitment its tick."""
        broken = await _commit(pool, source=source, action="broken deadline", summary="Broken")
        await pool.execute(
            """
            UPDATE public.owner_conditions
            SET metadata = jsonb_set(metadata, '{deadline}', '"not-a-date"')
            WHERE source = $1 AND fingerprint = $2
            """,
            source,
            broken,
        )
        healthy = await _commit(pool, source=source, action="healthy one", summary="Healthy")
        await _age(pool, source, healthy, level="L1", silent_days=2)

        result = await run_commitment_escalation(pool, insight_proposer=RecordingProposer())

        assert result.scanned == 2
        assert result.surfaced == 1


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — the candidate itself
# ---------------------------------------------------------------------------


class TestCandidateContent:
    @pytest.mark.parametrize(("level", "priority"), [("L1", 40), ("L2", 65), ("L3", 85)])
    async def test_req_commitment_lifecycle_005_priority_is_mapped_from_escalation_level(
        self, pool: asyncpg.Pool, source: str, level: str, priority: int
    ) -> None:
        """Criterion 5: "the commitment's escalation level mapped to insight priority"."""
        fingerprint = await _commit(pool, source=source)
        await _age(pool, source, fingerprint, level=level, silent_days=2, due_in=timedelta(days=3))

        proposer = RecordingProposer()
        await run_commitment_escalation(pool, insight_proposer=proposer)

        assert [call["priority"] for call in proposer.for_scope(fingerprint, level)] == [priority]

    async def test_req_commitment_lifecycle_005_candidate_carries_the_commitment_summary(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Criterion 5: "a commitment-specific summary"."""
        fingerprint = await _commit(
            pool, source=source, summary="Send Sam the Le Guin", action="send sam the le guin"
        )
        await _age(pool, source, fingerprint, level="L2", silent_days=6, due_in=timedelta(days=3))

        proposer = RecordingProposer()
        await run_commitment_escalation(pool, insight_proposer=proposer)

        call = proposer.for_scope(fingerprint, "L2")[0]
        assert "Send Sam the Le Guin" in call["message"]
        assert call["category"] == "commitment"
        assert call["origin_butler"] == source.split(":", 1)[0]
        assert json.loads(call["metadata"])["fingerprint"] == fingerprint

    async def test_req_commitment_lifecycle_005_dedup_key_is_stable_across_ticks(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Criterion 6: two ticks at the same level produce the same key, so the broker dedups.

        The job does not deduplicate — the broker does, off this key. What the
        job owes is a key that is identical for the same commitment at the same
        level and different once the level moves, so a repeat tick collapses
        while a genuine escalation gets through.
        """
        fingerprint = await _commit(pool, source=source)
        await _age(pool, source, fingerprint, level="L1", silent_days=2, due_in=timedelta(days=3))

        proposer = RecordingProposer()
        await run_commitment_escalation(pool, insight_proposer=proposer)
        await run_commitment_escalation(pool, insight_proposer=proposer)

        assert proposer.keys() == [f"commitment:{fingerprint}:L1"] * 2

    async def test_req_commitment_lifecycle_005_dedup_key_changes_when_the_level_does(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """A genuine escalation must not be silently deduplicated against the level below."""
        fingerprint = await _commit(pool, source=source)
        await _age(pool, source, fingerprint, level="L1", silent_days=2)

        proposer = RecordingProposer()
        await run_commitment_escalation(pool, insight_proposer=proposer)

        assert proposer.keys() == [f"commitment:{fingerprint}:L2"]
        assert (await _row(pool, source, fingerprint))["escalation_level"] == "L2"

    async def test_req_commitment_lifecycle_005_a_broker_failure_does_not_abort_the_tick(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """One unroutable commitment must not stop the rest from escalating."""
        first = await _commit(pool, source=source, action="first", summary="First")
        second = await _commit(pool, source=source, action="second", summary="Second")
        for fingerprint in (first, second):
            await _age(pool, source, fingerprint, level="L1", silent_days=2)

        result = await run_commitment_escalation(
            pool, insight_proposer=RecordingProposer(raises=True)
        )

        assert result.proposal_errors == 2
        assert result.surfaced == 0
        assert result.escalated == 2


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — composition with the real insight engine
# ---------------------------------------------------------------------------


class TestInsightEngineComposition:
    async def test_req_commitment_lifecycle_005_the_real_broker_queues_the_candidate(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Criterion 10: the job composes with the engine, unmodified.

        A recording fake cannot show that the broker accepts what this job
        builds — its dedup-key pattern, its 1-100 priority range, and its
        expiry freshness check all reject silently, returning
        ``{"status": "error"}`` that the job logs and counts. Running the real
        ``propose_insight_candidate`` against a real ``insight_candidates``
        table is the only thing that proves a commitment actually reaches the
        delivery queue, where it competes for the same budget as every other
        candidate.
        """
        from butlers.tools.switchboard.insight.broker import (
            create_insight_tables,
            propose_insight_candidate,
        )

        await create_insight_tables(pool)
        fingerprint = await _commit(pool, source=source)
        await _age(pool, source, fingerprint, level="L2", silent_days=6, due_in=timedelta(days=3))

        result = await run_commitment_escalation(pool, insight_proposer=propose_insight_candidate)

        assert result.surfaced == 1, result
        queued = await pool.fetchrow(
            "SELECT * FROM insight_candidates WHERE dedup_key = $1",
            f"commitment:{fingerprint}:L2",
        )
        assert queued is not None
        assert queued["status"] == "pending"
        assert queued["priority"] == 65
        assert queued["category"] == "commitment"
        assert queued["origin_butler"] == source.split(":", 1)[0]
        # The fingerprint travels in the dedup key, which is what the attention
        # ledger records as a delivered candidate's reference — so a ledger row
        # for a commitment insight resolves back to the commitment without the
        # engine needing to learn what a commitment is.
        assert fingerprint in queued["dedup_key"]
        # The candidate's structured identity must come back out of JSONB as an
        # object. The broker binds `metadata` straight into `$9::jsonb` without
        # serializing, so this job hands it a JSON string; if the broker ever
        # starts serializing for itself this reads back as a string and fails,
        # rather than silently storing a double-encoded value.
        stored = queued["metadata"]
        stored = json.loads(stored) if isinstance(stored, str) else stored
        assert isinstance(stored, dict), stored
        assert stored["fingerprint"] == fingerprint
        assert stored["class"] == "commitment"


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-006 — garbage collection
# ---------------------------------------------------------------------------


class TestGarbageCollection:
    async def test_req_commitment_lifecycle_006_ninety_day_l3_proposes_archival(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Spec scenario: 90+ days at L3 without re-confirmation asks the archival question.

        Criterion 7.
        """
        fingerprint = await _commit(pool, source=source)
        await _age(
            pool,
            source,
            fingerprint,
            level="L3",
            silent_days=GC_STALE_DAYS + 7,
            due_in=timedelta(days=7),
        )

        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert result.archival_proposed == 1
        call = proposer.for_scope(fingerprint, ARCHIVAL_DEDUP_SCOPE)[0]
        assert call["message"].startswith(
            f"This commitment has been open for {GC_STALE_DAYS + 7} days with no activity."
        )
        assert "Cancel or keep?" in call["message"]
        assert json.loads(call["metadata"])["proposal"] == "archival"

    async def test_req_commitment_lifecycle_006_a_fresh_l3_is_not_collected(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Reaching L3 is not the trigger; ninety days of silence at L3 is."""
        fingerprint = await _commit(pool, source=source)
        await _age(
            pool,
            source,
            fingerprint,
            level="L3",
            silent_days=GC_STALE_DAYS - 1,
            due_in=timedelta(days=7),
        )

        result = await run_commitment_escalation(pool, insight_proposer=RecordingProposer())

        assert result.archival_proposed == 0

    async def test_req_commitment_lifecycle_006_archival_is_not_gated_on_confidence(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Collection is housekeeping, not surfacing, so the confidence band does not apply.

        REQ-commitment-lifecycle-006 states the rule unconditionally, and the
        outcome settles the reading: gating it would make the hedged guesses
        the extraction pipeline is explicitly allowed to file (RFC 0026 §8) the
        only rows that can never be collected, in the one pass whose purpose is
        to bound the ledger's growth.
        """
        fingerprint = await _commit(pool, source=source, confidence=0.7)
        await _age(
            pool,
            source,
            fingerprint,
            level="L3",
            silent_days=GC_STALE_DAYS + 30,
            due_in=timedelta(days=7),
        )

        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert result.skipped_low_confidence == 1
        assert result.surfaced == 0
        assert result.archival_proposed == 1
        assert proposer.keys() == [f"commitment:{fingerprint}:{ARCHIVAL_DEDUP_SCOPE}"]

    async def test_req_commitment_lifecycle_006_archival_is_not_deduped_against_l3_surfacing(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """The two proposals ask different questions and must both reach the owner."""
        fingerprint = await _commit(pool, source=source)
        await _age(
            pool,
            source,
            fingerprint,
            level="L3",
            silent_days=GC_STALE_DAYS + 1,
            due_in=timedelta(days=7),
        )

        proposer = RecordingProposer()
        await run_commitment_escalation(pool, insight_proposer=proposer)

        assert sorted(proposer.keys()) == sorted(
            [
                f"commitment:{fingerprint}:L3",
                f"commitment:{fingerprint}:{ARCHIVAL_DEDUP_SCOPE}",
            ]
        )


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-006 — the archival question's two answers
# ---------------------------------------------------------------------------


class TestCancellation:
    async def test_req_commitment_lifecycle_006_cancellation_resolves_with_reason_and_evidence(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Spec scenario: cancelling resolves with ``resolution_reason: "cancelled"``.

        Criterion 8. Both keys are the ledger's own
        (``condition_ledger.RESOLUTION_METADATA_KEYS``), so they must arrive as
        ``resolve_condition``'s ``resolution_metadata``, never as observation
        metadata: ``reconcile_snapshot`` refuses either key before it touches
        the pool, and the creation-wins merge would otherwise let a producer's
        value beat the closing evidence.

        Since bu-o4i4j both land top-level in ``metadata`` — that is what this
        asserts, rather than trusting either the top-level or the nested shape
        the supersede path used to write.
        """
        fingerprint = await _commit(pool, source=source)
        await _age(
            pool,
            source,
            fingerprint,
            level="L3",
            silent_days=GC_STALE_DAYS + 5,
            due_in=timedelta(days=7),
        )

        transition = await cancel_stale_commitment(
            pool, source=source, fingerprint=fingerprint, session_id="synthetic-session"
        )

        assert transition is not None
        assert transition.transition == "resolved"
        row = await _row(pool, source, fingerprint)
        assert row["state"] == "resolved"
        assert row["resolved_at"] is not None
        assert row["metadata"]["resolution_reason"] == "cancelled"
        assert row["metadata"]["evidence_closed"]["source"] == "owner_confirmed"
        assert row["metadata"]["evidence_closed"]["session_id"] == "synthetic-session"
        # Not nested inside identity_payload — a reader must not have to know
        # which path ended the episode.
        assert "resolution_reason" not in row["metadata"].get("identity_payload", {})

    async def test_req_commitment_lifecycle_006_cancellation_preserves_creation_evidence(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Closing evidence is added alongside ``evidence_opened``, never on top of it."""
        fingerprint = await _commit(pool, source=source)

        await cancel_stale_commitment(pool, source=source, fingerprint=fingerprint)

        metadata = (await _row(pool, source, fingerprint))["metadata"]
        assert metadata["evidence_opened"]["source"] == "conversation"
        assert metadata["evidence_closed"]["source"] == "owner_confirmed"
        assert metadata["class"] == "commitment"

    async def test_req_commitment_lifecycle_006_cancelling_twice_is_not_an_error(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """An owner answering a question about an already-closed commitment gets ``None``."""
        fingerprint = await _commit(pool, source=source)

        assert await cancel_stale_commitment(pool, source=source, fingerprint=fingerprint)
        assert await cancel_stale_commitment(pool, source=source, fingerprint=fingerprint) is None

    async def test_req_commitment_lifecycle_006_a_cancelled_commitment_leaves_the_sweep(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Resolved is neither open nor aging, so the next tick never sees it again."""
        fingerprint = await _commit(pool, source=source)
        await _age(pool, source, fingerprint, level="L3", silent_days=GC_STALE_DAYS + 1)
        await cancel_stale_commitment(pool, source=source, fingerprint=fingerprint)

        result = await run_commitment_escalation(pool, insight_proposer=RecordingProposer())

        assert result.scanned == 0


class TestRenewal:
    async def test_req_commitment_lifecycle_006_renewal_resets_escalation_to_l1(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """Spec: "Renewal resets escalation to L1 for another cycle" (criterion 9).

        Written directly rather than through ``reconcile_snapshot`` because the
        ledger's confirmation path only ever advances a level — an L3
        commitment re-observed is still L3 — so there is no downward
        transition to borrow.
        """
        fingerprint = await _commit(pool, source=source)
        await _age(
            pool,
            source,
            fingerprint,
            level="L3",
            silent_days=GC_STALE_DAYS + 10,
            due_in=timedelta(days=7),
        )

        assert await renew_stale_commitment(pool, source=source, fingerprint=fingerprint)

        row = await _row(pool, source, fingerprint)
        assert row["escalation_level"] == "L1"
        assert row["state"] == "aging"

    async def test_req_commitment_lifecycle_006_renewal_restarts_the_silence_clock(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """The owner has just said the commitment is live, so the 90-day clock restarts.

        Without this the very next tick would ask the archival question again,
        because ``last_confirmed_at`` would still be ninety days old.
        """
        fingerprint = await _commit(pool, source=source)
        await _age(
            pool,
            source,
            fingerprint,
            level="L3",
            silent_days=GC_STALE_DAYS + 10,
            due_in=timedelta(days=7),
        )

        await renew_stale_commitment(pool, source=source, fingerprint=fingerprint)
        proposer = RecordingProposer()
        result = await run_commitment_escalation(pool, insight_proposer=proposer)

        assert result.archival_proposed == 0
        assert proposer.for_scope(fingerprint, ARCHIVAL_DEDUP_SCOPE) == []

    async def test_req_commitment_lifecycle_006_renewal_starts_another_full_cycle(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """ "for another cycle" — the renewed commitment climbs L1 -> L2 -> L3 again."""
        fingerprint = await _commit(pool, source=source)
        await _age(pool, source, fingerprint, level="L3", silent_days=GC_STALE_DAYS + 10)
        await renew_stale_commitment(pool, source=source, fingerprint=fingerprint)

        # Make the renewed L1 immediately due, as the schedule would a day later.
        await pool.execute(
            """
            UPDATE public.owner_conditions SET next_reescalate_at = now() - interval '1 second'
            WHERE source = $1 AND fingerprint = $2
            """,
            source,
            fingerprint,
        )
        await run_commitment_escalation(pool, insight_proposer=RecordingProposer())

        assert (await _row(pool, source, fingerprint))["escalation_level"] == "L2"

    async def test_req_commitment_lifecycle_006_renewing_a_resolved_commitment_reports_false(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """A caller must be able to tell "renewed" from "that had already closed"."""
        fingerprint = await _commit(pool, source=source)
        await cancel_stale_commitment(pool, source=source, fingerprint=fingerprint)

        assert await renew_stale_commitment(pool, source=source, fingerprint=fingerprint) is False

    async def test_req_commitment_lifecycle_006_renewal_only_touches_commitments(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """The class filter guards the write path too, not just the read path."""
        other_source = f"finance:bill-{uuid.uuid4().hex[:8]}"
        fingerprint = "bill-" + uuid.uuid4().hex
        await reconcile_snapshot(
            pool,
            source=other_source,
            observations=[
                Observation(fingerprint=fingerprint, summary="Bill", metadata={"class": "bill"})
            ],
            snapshot_complete=False,
            initial_grace_seconds=0.0,
        )

        renewed = await renew_stale_commitment(pool, source=other_source, fingerprint=fingerprint)

        assert renewed is False


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — the scheduler's entry point
# ---------------------------------------------------------------------------


class TestScheduledJobHandler:
    async def test_req_commitment_lifecycle_005_the_registered_handler_runs_the_tick(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """The handler the scheduler dispatches must actually reach the ledger.

        It binds the real broker to the injected seam through two deferred
        imports and returns the tick's counters as the job record. Nothing
        else exercises that wiring: a broken import or a renamed field would
        surface only as a failing scheduled job in production.
        """
        from butlers.scheduled_jobs import get_deterministic_schedule_job_registry
        from butlers.tools.switchboard.insight.broker import create_insight_tables

        await create_insight_tables(pool)
        fingerprint = await _commit(pool, source=source)
        await _age(pool, source, fingerprint, level="L1", silent_days=2, due_in=timedelta(days=3))

        handler = get_deterministic_schedule_job_registry()["switchboard"]["commitment_escalation"]
        record = await handler(pool, None)

        assert record["scanned"] == 1
        assert record["surfaced"] == 1
        queued = await pool.fetchval(
            "SELECT count(*) FROM insight_candidates WHERE dedup_key = $1",
            f"commitment:{fingerprint}:L1",
        )
        assert queued == 1


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — delivery reaches the attention ledger
# ---------------------------------------------------------------------------


class TestAttentionLedgerRecording:
    async def test_req_commitment_lifecycle_005_delivery_records_the_commitment_in_the_ledger(
        self, pool: asyncpg.Pool, source: str
    ) -> None:
        """A delivered commitment insight is recorded, and resolves back to the commitment.

        REQ-commitment-lifecycle-005's normative sentence — "SHALL compose with
        the existing insight engine and attention ledger; commitments compete
        for the same delivery budget as other insights" — is what this proves:
        the candidate this job proposes travels the ordinary delivery path and
        lands in ``public.attention_ledger`` with no engine change.

        Its scenario asks for ``source: "commitment"`` and the fingerprint as
        the reference, and that literal shape is not reachable. ``source`` is a
        closed vocabulary on the ledger itself
        (``attention_ledger.VALID_SOURCES`` is ``notify``/``insight``/
        ``discretion``, and anything else is dropped with a warning), and the
        value is written by the delivery cycle, not by the proposer — so
        producing it would mean changing both the attention ledger's vocabulary
        and the insight engine, which the same requirement forbids in the very
        next clause ("No changes to the insight engine itself", criterion 10).
        The two halves of REQ-005 contradict each other, and this is the half
        that can hold.

        What survives is the substance: ``source`` says the delivery came
        through the insight path, and the fingerprint travels in ``dedup_key``,
        so a ledger row for a commitment resolves back to the commitment
        without the engine needing to learn what a commitment is. This test
        pins that, so the traceability cannot be lost silently.
        """
        from unittest.mock import AsyncMock

        from butlers.core.approvals_policy import (
            get_approvals_policy_quiet_hours,
            is_policy_quiet_now,
        )
        from butlers.tools.switchboard.insight.broker import (
            create_insight_tables,
            delivery_cycle,
            propose_insight_candidate,
        )

        await create_insight_tables(pool)
        await pool.execute(
            """
            INSERT INTO insight_settings (id, verbosity) VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity = 'normal'
            """
        )
        fingerprint = await _commit(pool, source=source)
        await _age(pool, source, fingerprint, level="L3", silent_days=20, due_in=timedelta(days=7))

        assert (
            await run_commitment_escalation(pool, insight_proposer=propose_insight_candidate)
        ).surfaced == 1

        # delivery_cycle consults the Owner Attention Policy, and the migration
        # seeds quiet hours 23:00-08:00 Asia/Singapore -- 15:00-24:00 UTC. Read
        # with the real clock this assertion therefore failed for nine hours of
        # every day and passed for the other fifteen. Pin both halves: the
        # policy, so a change to the seed cannot silently re-break it, and the
        # instant, so the test asserts what it means to assert.
        await pool.execute(
            """
            INSERT INTO public.approvals_policy (id, quiet_start_hour, quiet_end_hour, timezone)
            VALUES (1, 23, 8, 'UTC')
            ON CONFLICT (id) DO UPDATE
                SET quiet_start_hour = EXCLUDED.quiet_start_hour,
                    quiet_end_hour = EXCLUDED.quiet_end_hour,
                    timezone = EXCLUDED.timezone
            """
        )
        delivery_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        assert not is_policy_quiet_now(
            await get_approvals_policy_quiet_hours(pool), now=delivery_at
        )

        notify = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(pool, notify_fn=notify, now=delivery_at)
        assert result["skipped"] is False
        assert result["delivered"]

        row = await pool.fetchrow(
            "SELECT * FROM public.attention_ledger WHERE dedup_key = $1",
            f"commitment:{fingerprint}:L3",
        )
        assert row is not None
        assert row["source"] == "insight"
        assert row["outcome"] in ("delivered", "coalesced")
        assert row["origin_butler"] == source.split(":", 1)[0]
        assert fingerprint in row["dedup_key"]

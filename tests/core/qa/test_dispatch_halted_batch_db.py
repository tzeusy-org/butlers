"""Real-Postgres proof that a halted QA dispatch is one decision per patrol (bu-ejow5p.3).

A tripped breaker, a missing model or a full concurrency cap used to cost one claim, one
delete and one event per finding per patrol, and the cap path claimed a retry it never queued.
The batch dispatcher now evaluates the halting gates once, stamps every finding with the
authoritative reason, queues it for the next patrol and writes a single dispatch event.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.core.qa.dispatch import QaDispatchConfig, dispatch_novel_findings
from butlers.core.qa.findings import get_dispatch_queued_findings, insert_finding
from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import TriagedFinding

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _finding(severity: int = 1) -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=uuid.uuid4().hex * 2,
        source_type="log_scanner",
        source_butler="general",
        severity=severity,
        exception_type="ValueError",
        event_summary="Synthetic failure",
        call_site="module:1",
        occurrence_count=1,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


async def _patrol(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.qa_patrols (status) VALUES ('running') RETURNING id"
    )


async def _novel(pool: asyncpg.Pool, patrol_id: uuid.UUID, count: int) -> list[TriagedFinding]:
    triaged = []
    for _ in range(count):
        finding = _finding()
        finding_id = await insert_finding(pool, patrol_id, finding, dedup_reason=None)
        triaged.append(TriagedFinding(finding=finding, dedup_reason=None, finding_id=finding_id))
    return triaged


async def _trip(pool: asyncpg.Pool, patrol_id: uuid.UUID, count: int = 5) -> None:
    for _ in range(count):
        await pool.execute(
            """
            INSERT INTO public.healing_attempts
                (fingerprint, butler_name, status, severity, exception_type, call_site,
                 healing_session_id, qa_patrol_id)
            VALUES ($1, 'general', 'failed', 2, 'RuntimeError', 'src/foo.py:bar',
                    gen_random_uuid(), $2)
            """,
            "b" * 64,
            patrol_id,
        )


async def _dispatch(pool, findings, patrol_id, **config) -> list:
    return await dispatch_novel_findings(
        pool=pool,
        novel_findings=findings,
        patrol_id=patrol_id,
        config=QaDispatchConfig(**config),
        repo_root=Path("/nonexistent"),
        spawner=MagicMock(),
    )


@pytest.mark.pg_clock
async def test_open_breaker_claims_no_attempts_and_writes_one_event_per_patrol(db_pool) -> None:
    pool = db_pool
    patrol_id = await _patrol(pool)
    await _trip(pool, patrol_id)
    findings = await _novel(pool, patrol_id, 3)
    attempts_before = await pool.fetchval("SELECT count(*) FROM public.healing_attempts")

    with patch("butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock) as claim:
        results = await _dispatch(pool, findings, patrol_id)

    claim.assert_not_called()
    assert [r.reason for r in results] == ["circuit_breaker"] * 3
    assert not any(r.accepted for r in results)
    assert await pool.fetchval("SELECT count(*) FROM public.healing_attempts") == attempts_before
    events = await pool.fetch(
        "SELECT reason FROM public.healing_dispatch_events WHERE decision = 'circuit_breaker'"
    )
    assert len(events) == 1
    assert "3" in events[0]["reason"]
    rows = await pool.fetch(
        "SELECT dedup_reason, dispatch_queued FROM public.qa_findings WHERE patrol_id = $1",
        patrol_id,
    )
    assert [(r["dedup_reason"], r["dispatch_queued"]) for r in rows] == [
        ("circuit_breaker", True)
    ] * 3


@pytest.mark.pg_clock
async def test_open_breaker_queues_findings_and_they_resurface_after_close(db_pool) -> None:
    pool = db_pool
    patrol_id = await _patrol(pool)
    await _trip(pool, patrol_id)
    findings = await _novel(pool, patrol_id, 2)
    await _dispatch(pool, findings, patrol_id)

    # An owner reset closes the breaker; the held findings are still there to retry.
    await pool.execute("INSERT INTO public.breaker_resets (breaker) VALUES ('qa')")
    queued = await get_dispatch_queued_findings(pool)
    assert {row["fingerprint"] for row in queued} == {f.finding.fingerprint for f in findings}


@pytest.mark.pg_clock
async def test_cap_queues_every_leftover_finding(db_pool) -> None:
    pool = db_pool
    patrol_id = await _patrol(pool)
    findings = await _novel(pool, patrol_id, 5)
    accepted = MagicMock(accepted=True, reason="dispatched")
    active = iter([0, 1, 2, 2, 2])

    async def _count(*_args, **_kwargs) -> int:
        return next(active)

    with (
        patch("butlers.core.qa.dispatch.count_active_attempts", side_effect=_count),
        patch(
            "butlers.core.qa.dispatch.dispatch_qa_investigation",
            new_callable=AsyncMock,
            return_value=accepted,
        ) as launch,
    ):
        results = await _dispatch(pool, findings, patrol_id, max_concurrent=2)

    assert launch.await_count == 2
    assert [r.reason for r in results[2:]] == ["concurrency_cap"] * 3
    held = await pool.fetch(
        "SELECT dedup_reason FROM public.qa_findings WHERE patrol_id = $1 AND dispatch_queued",
        patrol_id,
    )
    assert [r["dedup_reason"] for r in held] == ["concurrency_cap"] * 3

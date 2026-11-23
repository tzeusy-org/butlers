"""Real-Postgres proof of the QA breaker's half-open probe (bu-ejow5p.3).

The backoff schedule is covered without a database in ``test_breaker_state.py``; here the
backoff is set to zero so the probe is due at once, and the proof is the exclusion: two
dispatchers with separate connections admit exactly one probe, and the other patrol's findings
are held and queued.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.core.qa.dispatch import QaDispatchConfig, QaDispatchResult, dispatch_novel_findings
from butlers.core.qa.findings import insert_finding
from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import TriagedFinding

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _finding() -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=uuid.uuid4().hex * 2,
        source_type="log_scanner",
        source_butler="general",
        severity=1,
        exception_type="ValueError",
        event_summary="Synthetic failure",
        call_site="module:1",
        occurrence_count=1,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


async def _trip(pool: asyncpg.Pool, patrol_id: uuid.UUID) -> None:
    for _ in range(5):
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


async def _patrol_with_findings(pool: asyncpg.Pool, count: int):
    patrol_id = await pool.fetchval(
        "INSERT INTO public.qa_patrols (status) VALUES ('running') RETURNING id"
    )
    findings = []
    for _ in range(count):
        finding = _finding()
        finding_id = await insert_finding(pool, patrol_id, finding, dedup_reason=None)
        findings.append(TriagedFinding(finding=finding, dedup_reason=None, finding_id=finding_id))
    return patrol_id, findings


@pytest.mark.pg_clock
async def test_half_open_admits_exactly_one_probe_under_concurrent_dispatchers(db_pool) -> None:
    pool = db_pool
    trip_patrol, _ = await _patrol_with_findings(pool, 0)
    await _trip(pool, trip_patrol)
    patrol_a, findings_a = await _patrol_with_findings(pool, 2)
    patrol_b, findings_b = await _patrol_with_findings(pool, 2)
    launched: list[uuid.UUID] = []

    async def launch(*, triaged_finding, patrol_id, **_kwargs) -> QaDispatchResult:
        # The claim: an in-flight attempt row, written while the probe lock is held.
        await pool.execute(
            """
            INSERT INTO public.healing_attempts
                (fingerprint, butler_name, status, severity, exception_type, call_site,
                 healing_session_id, qa_patrol_id)
            VALUES ($1, 'general', 'investigating', 2, 'RuntimeError', 'src/foo.py:bar',
                    gen_random_uuid(), $2)
            """,
            triaged_finding.finding.fingerprint,
            patrol_id,
        )
        launched.append(triaged_finding.finding_id)
        await asyncio.sleep(0.2)
        return QaDispatchResult(accepted=True, fingerprint="x" * 64, reason="dispatched")

    async def run(patrol_id, findings):
        return await dispatch_novel_findings(
            pool=pool,
            novel_findings=findings,
            patrol_id=patrol_id,
            config=QaDispatchConfig(max_concurrent=10),
            repo_root=Path("/nonexistent"),
            spawner=MagicMock(),
        )

    with (
        patch("butlers.core.qa.breaker_state.PROBE_BACKOFF_BASE", timedelta(0)),
        patch("butlers.core.qa.dispatch.dispatch_qa_investigation", side_effect=launch),
    ):
        results = await asyncio.gather(run(patrol_a, findings_a), run(patrol_b, findings_b))

    assert len(launched) == 1
    accepted = [r for batch in results for r in batch if r.accepted]
    assert len(accepted) == 1
    held = await pool.fetchval(
        "SELECT count(*) FROM public.qa_findings "
        "WHERE dedup_reason = 'circuit_breaker' AND dispatch_queued"
    )
    assert held == 3

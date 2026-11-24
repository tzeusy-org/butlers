"""Evidence bundle SQL against the real migrated schema (bu-ejow5p.13)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from butlers.core.qa.evidence_bundle import build_evidence_bundle, persist_bundle
from tests.core.qa.test_evidence_bundle import _finding, _no_history

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_the_bundle_reads_real_views_and_is_recorded_before_launch(db_pool) -> None:
    session_id = uuid.uuid4()
    now = datetime.now(UTC)
    await db_pool.execute(
        """
        INSERT INTO general.sessions (id, prompt, trigger_source, success, error, tool_calls,
                                      started_at, completed_at)
        VALUES ($1, 'p', 'schedule', false, 'ledger write failed', $2::jsonb, $3, $4)
        """,
        session_id,
        (
            [
                {
                    "name": "ledger_append",
                    "module": "finance",
                    "outcome": "error",
                    "error": "constraint violated",
                }
            ]
        ),
        now - timedelta(seconds=20),
        now,
    )
    finding = _finding(
        source_butler="general", structured_evidence={"session_ids": [str(session_id)]}
    )
    attempt_id = uuid.uuid4()

    bundle = await build_evidence_bundle(
        db_pool, finding, attempt_id=attempt_id, repo_root=Path("/repo"), git_history=_no_history
    )

    status = {s.name: s.status for s in bundle.sections}
    assert status["sessions"] == "available" and status["tool_failures"] == "available"
    assert status["recent_outcomes"] == "empty"
    assert "ledger write failed" in bundle.items[0].text

    await db_pool.execute(
        """
        INSERT INTO public.healing_attempts (id, fingerprint, butler_name, status, severity, exception_type, call_site, sanitized_msg)
        VALUES ($1, $2, 'general', 'investigating', 2, 'E', 'c', 's')
        """,
        attempt_id,
        finding.fingerprint,
    )
    patrol_id = await db_pool.fetchval("INSERT INTO public.qa_patrols DEFAULT VALUES RETURNING id")
    await db_pool.execute(
        """
        INSERT INTO public.qa_findings (patrol_id, fingerprint, source_type, source_butler, severity,
            exception_type, event_summary, call_site, occurrence_count, first_seen, last_seen,
            healing_attempt_id, structured_evidence)
        VALUES ($3, $1, 'session_records', 'general', 2, 'E', 's', 'c', 1, now(), now(), $2, '{}'::jsonb)
        """,
        finding.fingerprint,
        attempt_id,
        patrol_id,
    )

    await persist_bundle(db_pool, attempt_id, bundle)

    stored = await db_pool.fetchval(
        "SELECT structured_evidence FROM public.qa_findings WHERE healing_attempt_id = $1",
        attempt_id,
    )
    stored = json.loads(stored) if isinstance(stored, str) else stored
    assert stored["evidence_bundle"]["manifest"]["digest"] == bundle.digest
    assert stored["evidence_bundle"]["bodies"]["sections"][0]["items"][0]["id"] == "E1"

"""Committed QA suppression survives a lost acknowledgement and concurrent retry."""

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.infra_conditions import Observation, reconcile_snapshot
from butlers.core.qa.dispatch import QaDispatchConfig, dispatch_qa_investigation
from butlers.core.qa.findings import insert_finding
from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import TriagedFinding
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def suppression_db_url(postgres_container):
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


async def test_committed_suppression_is_visible_and_lost_ack_retry_has_one_decision(
    suppression_db_url,
):
    writer = await asyncpg.create_pool(suppression_db_url, min_size=2, max_size=3)
    reader = await asyncpg.create_pool(suppression_db_url, min_size=1, max_size=2)
    try:
        now = datetime.now(UTC)
        fingerprint = uuid4().hex * 2
        finding = QaFinding(
            fingerprint=fingerprint,
            source_type="infra_state",
            source_butler="qa-test",
            severity=1,
            exception_type="SyntheticUnavailable",
            event_summary="Synthetic unavailable",
            call_site="synthetic:1",
            occurrence_count=1,
            first_seen=now,
            last_seen=now,
            timestamp=now,
        )
        await reconcile_snapshot(
            writer,
            source="infra_state",
            observations=[Observation(fingerprint=fingerprint)],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )
        patrol = await writer.fetchval("INSERT INTO public.qa_patrols DEFAULT VALUES RETURNING id")
        finding_id = await insert_finding(writer, patrol, finding, None)
        triaged = TriagedFinding(finding=finding, dedup_reason=None, finding_id=finding_id)
        spawner = MagicMock()

        class LostAcknowledgement:
            pending = True

            def __getattr__(self, name):
                return getattr(writer, name)

            async def fetchval(self, *args):
                value = await writer.fetchval(*args)
                if self.pending:
                    self.pending = False
                    raise OSError("synthetic committed response lost")
                return value

        async def invoke(pool):
            return await dispatch_qa_investigation(
                pool,
                triaged,
                patrol,
                QaDispatchConfig(),
                Path("/unused"),
                spawner,
            )

        failed = await invoke(LostAcknowledgement())
        assert (failed.reason, failed.accepted, failed.attempt_id) == (
            "internal_error",
            False,
            None,
        )
        # A distinct pool/acquisition observes the first committed event and
        # finding reason despite the caller having received non-success.
        async with reader.acquire() as connection:
            assert (
                await connection.fetchval(
                    "SELECT dedup_reason FROM public.qa_findings WHERE id=$1",
                    finding_id,
                )
                == "infra_condition_open"
            )
            first_event = await connection.fetchrow(
                "SELECT id, reason, attempt_id FROM public.healing_dispatch_events "
                "WHERE fingerprint=$1",
                fingerprint,
            )
            assert first_event is not None
            assert first_event["attempt_id"] is None
            assert "Infrastructure condition already active" in first_event["reason"]
        retries = await asyncio.gather(invoke(writer), invoke(writer))
        assert all(
            (r.reason, r.accepted, r.attempt_id) == ("infra_condition_open", False, None)
            for r in retries
        )
        async with reader.acquire() as connection:
            events = await connection.fetch(
                "SELECT id, reason, decision, attempt_id FROM public.healing_dispatch_events "
                "WHERE fingerprint=$1",
                fingerprint,
            )
            assert len(events) == 1
            assert events[0]["id"] == first_event["id"]
            assert events[0]["reason"] == first_event["reason"]
            assert events[0]["decision"] == "infra_condition_open"
            assert events[0]["attempt_id"] is None
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM public.healing_attempts WHERE fingerprint=$1",
                    fingerprint,
                )
                == 0
            )
            assert (
                await connection.fetchval(
                    "SELECT healing_attempt_id FROM public.qa_findings WHERE id=$1",
                    finding_id,
                )
                is None
            )
        assert not spawner.mock_calls
    finally:
        await reader.close()
        await writer.close()

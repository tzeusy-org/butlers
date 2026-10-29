"""Real-Postgres regression: the dropped-known watch job (bu-q7vx1q.88.2).

Drives the REGISTERED ``dropped_known_watch`` handler (the one the scheduler dispatches) against
a fully migrated database. One owner condition tracks each ``(filter_reason, sender)`` episode of
unanswered known-contact drops; the owner is told once per opened or reopened transition; the
episode resolves when its rows are replayed or age out of the window; an unknown classification
changes nothing; and neither the condition nor the candidate carries the sender address.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from butlers.db import register_jsonb_codec
from butlers.scheduled_jobs import get_deterministic_schedule_job_registry
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_SOURCE = "switchboard:dropped-known"
_ENDPOINT = "gmail:user:alice@example.com"
_SENDER = "friend@known.example"
_OTHER_SENDER = "colleague@known.example"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
    # insight_candidates is referenced by insight_engagement, insight_feedback and the premise
    # binding table, so it truncates with CASCADE; nothing else here has a referencing table.
    await p.execute("TRUNCATE TABLE connectors.filtered_events, public.owner_conditions")
    await p.execute("TRUNCATE TABLE insight_candidates CASCADE")
    yield p
    await p.close()


def _classification(observed_at: datetime | None) -> dict | None:
    if observed_at is None:
        return None
    return {
        "version": 1,
        "state": "loaded",
        "reason": "none",
        "generation": 1,
        "last_success_at": observed_at.isoformat(),
        "observed_at": observed_at.isoformat(),
    }


async def _drop(
    pool: asyncpg.Pool,
    message_id: str,
    *,
    sender: str = _SENDER,
    received_at: datetime,
    known_classification: bool = True,
) -> None:
    """Persist one marked drop through the real buffer; ``known_classification=False`` is the
    historical-uncertainty shape (no frozen classification)."""
    await pool.execute(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", received_at
    )
    buf = FilteredEventBuffer(connector_type="gmail", endpoint_identity=_ENDPOINT)
    buf.record(
        external_message_id=message_id,
        source_channel="email",
        sender_identity=sender,
        subject_or_preview="private subject",
        filter_reason=FilteredEventBuffer.reason_policy_rule("connector_rule", "block", "sender"),
        status="filtered",
        received_at=received_at,
        full_payload=FilteredEventBuffer.full_payload(
            channel="email",
            provider="gmail",
            endpoint_identity=_ENDPOINT,
            external_event_id=message_id,
            external_thread_id=None,
            observed_at=received_at.isoformat(),
            sender_identity=sender,
            raw={},
            normalized_text="private body",
            important_dropped_basis="known_contact",
            classification=_classification(received_at if known_classification else None),
        ),
    )
    await buf.flush(pool)


async def _run(pool: asyncpg.Pool) -> dict:
    handler = get_deterministic_schedule_job_registry()["switchboard"]["dropped_known_watch"]
    return await handler(pool, None)


async def _conditions(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT episode, state, metadata, summary FROM public.owner_conditions "
        "WHERE source = $1 ORDER BY first_detected_at, episode",
        _SOURCE,
    )


async def _candidates(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch("SELECT * FROM insight_candidates WHERE category = 'dropped-known'")


async def test_dropped_known_watch_opens_updates_resolves_reopens_and_stays_quiet_when_unknown(
    pool: asyncpg.Pool,
) -> None:
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)

    # ACC1: two drops from one sender by one rule open ONE condition and propose ONE candidate.
    await _drop(pool, "m1", received_at=hour_ago)
    await _drop(pool, "m2", received_at=hour_ago)
    first = await _run(pool)
    assert (first["status"], first["episodes"], first["opened"], first["proposed"]) == (
        "ok",
        1,
        1,
        1,
    )
    (condition,) = await _conditions(pool)
    assert condition["state"] in ("open", "aging") and condition["episode"] == 1
    assert condition["metadata"]["count"] == 2
    assert len(await _candidates(pool)) == 1

    # ACC2: a third drop updates the count and re-running the same data proposes nothing new.
    await _drop(pool, "m3", received_at=hour_ago)
    second = await _run(pool)
    assert (second["opened"], second["proposed"]) == (0, 0)
    assert (await _conditions(pool))[0]["metadata"]["count"] == 3
    assert (await _run(pool))["proposed"] == 0
    assert len(await _candidates(pool)) == 1

    # ACC5: an unknown classification (a drop with no frozen classification) changes nothing,
    # even though every counted row was replayed; it never resolves the episode.
    await _drop(
        pool, "m-unknown", sender=_OTHER_SENDER, received_at=hour_ago, known_classification=False
    )
    await pool.execute(
        "UPDATE connectors.filtered_events SET status = 'replay_complete' "
        "WHERE external_message_id IN ('m1', 'm2', 'm3')"
    )
    degraded = await _run(pool)
    assert degraded == {"status": "degraded", "reason": "historical_uncertainty"}
    assert [row["state"] for row in await _conditions(pool)] == [condition["state"]]
    await pool.execute(
        "DELETE FROM connectors.filtered_events WHERE external_message_id = 'm-unknown'"
    )

    # ACC3: with the rows replayed the condition resolves.
    resolved = await _run(pool)
    assert (resolved["resolved"], resolved["proposed"]) == (1, 0)
    assert [row["state"] for row in await _conditions(pool)] == ["resolved"]

    # ACC4: a new drop from the same pair reopens it with exactly one new candidate.
    await _drop(pool, "m4", received_at=now - timedelta(minutes=5))
    reopened = await _run(pool)
    assert (reopened["reopened"], reopened["proposed"]) == (1, 1)
    assert len(await _candidates(pool)) == 2

    # ACC3 (age-out): once the only open row leaves the 7 day window the episode resolves.
    aged = now - timedelta(days=8)
    await pool.execute("SELECT connectors.connectors_filtered_events_ensure_partition($1)", aged)
    await pool.execute(
        "UPDATE connectors.filtered_events SET received_at = $1 WHERE external_message_id = 'm4'",
        aged,
    )
    assert (await _run(pool))["resolved"] == 1

    # Content-free: neither the ledger nor any candidate carries the sender, subject or body.
    stored = json.dumps(
        [dict(row) for row in await pool.fetch("SELECT * FROM public.owner_conditions")]
        + [dict(row) for row in await _candidates(pool)],
        default=str,
    )
    filter_reason = FilteredEventBuffer.reason_policy_rule("connector_rule", "block", "sender")
    for private in (_SENDER, _OTHER_SENDER, "private subject", "private body", filter_reason):
        assert private not in stored

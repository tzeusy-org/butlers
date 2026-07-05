"""Real-Postgres regression: FilteredEventBuffer.record()/flush() must not
double-encode ``connectors.filtered_events.full_payload`` (bu-dycxq — sibling
sweep to bu-cymc4/bu-x92jw/bu-bstqu/bu-c8b8e/bu-xfcpf).

``FilteredEventBuffer.record()`` used to ``json.dumps()`` the ``full_payload``
dict before appending it to the in-memory buffer for a later batch
``executemany`` INSERT. Every asyncpg pool in this codebase registers a JSONB
type codec (``register_jsonb_codec``, ``src/butlers/db.py``) whose encoder
calls ``json.dumps()`` on the bound Python object itself — so the old code
path double-encoded ``full_payload`` into a jsonb-typed STRING instead of an
OBJECT. ``drain_replay_pending`` carries an ``isinstance(raw_payload, str)``
workaround on read to tolerate the corrupted shape.

Every connector (gmail, telegram, discord, google_drive, google_calendar,
spotify, google_health, owntracks, steam, activitywatch) shares this single
``FilteredEventBuffer``/``drain_replay_pending`` implementation, so fixing this
one writer fixes every connector's filtered-event write path uniformly.

Live-data audit (read-only, butlers-dev, 2026-07-05): of 807,206 total
``connectors.filtered_events`` rows across the two populated monthly
partitions (filtered_events_202606: 550,832 rows, filtered_events_202607:
256,374 rows), 807,205 (effectively ALL — 100.0%) have
``jsonb_typeof(full_payload) = 'string'``. This table is documented as
operational visibility data, not an audit trail (loss is by-design acceptable,
see ``FilteredEventBuffer`` module docstring), and there are currently zero
``replay_pending`` rows in either partition, so a bulk repair of ~800k
historical rows is out of scope for this bead (no migrations permitted). The
``isinstance(raw_payload, str)`` read-side workaround in ``drain_replay_pending``
is therefore KEPT — it is the only thing standing between this near-total
historical corruption and a replay silently failing to parse ``full_payload``.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain — connectors.filtered_events."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE connectors.filtered_events")
    yield p
    await p.close()


def _sample_payload() -> dict:
    return FilteredEventBuffer.full_payload(
        channel="email",
        provider="gmail",
        endpoint_identity="gmail:user:alice@example.com",
        external_event_id="msg-001",
        external_thread_id="thread-001",
        observed_at="2026-03-11T10:00:00Z",
        sender_identity="sender@example.com",
        raw={"headers": [], "body": "Hello"},
        normalized_text="Hello",
        policy_tier="full",
    )


async def test_record_and_flush_round_trips_full_payload_as_object(pool: asyncpg.Pool) -> None:
    """record() + flush() persist full_payload as a jsonb OBJECT, not a
    jsonb-typed string."""
    buf = FilteredEventBuffer(
        connector_type="gmail", endpoint_identity="gmail:user:alice@example.com"
    )
    buf.record(
        external_message_id="msg-1",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason=FilteredEventBuffer.reason_label_exclude("CATEGORY_PROMOTIONS"),
        full_payload=_sample_payload(),
    )

    await buf.flush(pool)
    assert len(buf) == 0

    row = await pool.fetchrow(
        "SELECT full_payload FROM connectors.filtered_events "
        "WHERE connector_type = $1 AND external_message_id = $2",
        "gmail",
        "msg-1",
    )
    stored = row["full_payload"]
    assert isinstance(stored, dict), (
        f"Expected full_payload to be stored as a jsonb OBJECT but got "
        f"{type(stored).__name__!r}: {stored!r}"
    )
    assert stored["source"]["channel"] == "email"
    assert stored["payload"]["normalized_text"] == "Hello"


async def test_drain_replay_pending_handles_clean_object_row(pool: asyncpg.Pool) -> None:
    """drain_replay_pending submits a clean (object-shaped) full_payload row
    unchanged, and marks it replay_complete."""
    buf = FilteredEventBuffer(
        connector_type="gmail", endpoint_identity="gmail:user:alice@example.com"
    )
    buf.record(
        external_message_id="msg-clean",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason=FilteredEventBuffer.reason_submission_error(),
        status="replay_pending",
        full_payload=_sample_payload(),
    )
    await buf.flush(pool)

    submitted: list[dict] = []

    async def _submit(envelope: dict) -> None:
        submitted.append(envelope)

    await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", _submit)

    assert len(submitted) == 1
    assert submitted[0]["schema_version"] == "ingest.v1"
    assert submitted[0]["payload"]["normalized_text"] == "Hello"

    row = await pool.fetchrow(
        "SELECT status FROM connectors.filtered_events WHERE external_message_id = $1",
        "msg-clean",
    )
    assert row["status"] == "replay_complete"


async def test_drain_replay_pending_handles_legacy_string_shaped_row(pool: asyncpg.Pool) -> None:
    """A pre-existing corrupted row (full_payload stored as a jsonb-typed
    STRING, matching the ~100% corruption rate found in the live-data audit)
    is still parsed and submitted correctly by drain_replay_pending's
    ``isinstance(raw_payload, str)`` workaround."""
    payload = _sample_payload()
    reference_ts = datetime.now(UTC)
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", reference_ts
    )
    await pool.execute(
        """
        INSERT INTO connectors.filtered_events (
            received_at, connector_type, endpoint_identity, external_message_id,
            source_channel, sender_identity, subject_or_preview, filter_reason,
            status, full_payload, error_detail
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
        """,
        reference_ts,
        "gmail",
        "gmail:user:alice@example.com",
        "msg-legacy",
        "email",
        "sender@example.com",
        "Hello",
        FilteredEventBuffer.reason_submission_error(),
        "replay_pending",
        json.dumps(payload),  # pre-fix double-encoding, reproduced deliberately
        None,
    )

    row = await pool.fetchrow(
        "SELECT full_payload FROM connectors.filtered_events WHERE external_message_id = $1",
        "msg-legacy",
    )
    assert isinstance(row["full_payload"], str), (
        "Test setup sanity check: expected the hand-inserted row to reproduce "
        "the corrupted (string-typed) shape found in the live-data audit."
    )

    submitted: list[dict] = []

    async def _submit(envelope: dict) -> None:
        submitted.append(envelope)

    await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", _submit)

    assert len(submitted) == 1
    assert submitted[0]["payload"]["normalized_text"] == "Hello"

    row = await pool.fetchrow(
        "SELECT status FROM connectors.filtered_events WHERE external_message_id = $1",
        "msg-legacy",
    )
    assert row["status"] == "replay_complete"


async def test_buggy_write_path_would_have_corrupted_full_payload_into_a_string(
    pool: asyncpg.Pool,
) -> None:
    """Documents the pre-fix failure mode: json.dumps()-ing full_payload before
    binding it double-encodes the value into a jsonb-typed STRING instead of an
    OBJECT — the exact anti-pattern this bead removes from
    ``FilteredEventBuffer.record()``."""
    reference_ts = datetime.now(UTC)
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", reference_ts
    )
    buggy_json_string = json.dumps(_sample_payload())
    await pool.execute(
        """
        INSERT INTO connectors.filtered_events (
            received_at, connector_type, endpoint_identity, external_message_id,
            source_channel, sender_identity, subject_or_preview, filter_reason,
            status, full_payload, error_detail
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        reference_ts,
        "gmail",
        "gmail:user:alice@example.com",
        "msg-buggy",
        "email",
        "sender@example.com",
        "Hello",
        FilteredEventBuffer.reason_submission_error(),
        "filtered",
        buggy_json_string,  # bound as a plain str param, matching the old record() bug
        None,
    )

    row = await pool.fetchrow(
        "SELECT full_payload FROM connectors.filtered_events WHERE external_message_id = $1",
        "msg-buggy",
    )
    stored = row["full_payload"]
    assert isinstance(stored, str), (
        "Expected the buggy write path to corrupt full_payload into a jsonb "
        f"STRING but got {type(stored).__name__!r}: {stored!r}"
    )
    assert json.loads(stored)["payload"]["normalized_text"] == "Hello"

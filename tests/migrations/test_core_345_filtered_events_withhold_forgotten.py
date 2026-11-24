"""core_345: a filtered-event copy written for a forgotten fix is withheld (bu-s11n0s.7 S4c-4).

``execute_grant`` withholds the connector's ``connectors.filtered_events`` copies of the fixes
it forgets. A copy written later (a phone resend parked after its ingest attempts are spent)
would bring the coordinates back, so core_345's insert trigger withholds an OwnTracks copy whose
logical key (``'owntracks:' || sender_identity || ':' || external_message_id``) is tombstoned.
It takes core_343's forget guard first, so a copy racing the deleter waits for its commit and
then reads the tombstone. Installed once across the per-schema core chains; only the last
schema's downgrade removes it.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]
_async = pytest.mark.asyncio(loop_scope="session")

_TS = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)
_KEY = "owntracks:alice:1:location"
_WITHHELD = {
    "payload": {"raw": None, "normalized_text": "[location withheld: raw retention]"},
    "replay_unavailable": "the location was forgotten by raw retention",
}
_ENVELOPE = {
    "event": {"external_event_id": "1:location"},
    "payload": {"raw": {"lat": 1.3, "lon": 103.8}, "normalized_text": "Location: 1.3, 103.8"},
}
_COPY = """
    INSERT INTO connectors.filtered_events (
        connector_type, endpoint_identity, external_message_id, source_channel,
        sender_identity, filter_reason, status, full_payload
    ) VALUES ($1, $2, $3, $1, $2, 'retry_exhausted', 'error', $4::jsonb)
    RETURNING full_payload
"""


@pytest.fixture
def head_url(fresh_db) -> str:
    return fresh_db("core")


async def _copy(conn, sender: str = "alice", external_id: str = "1:location", channel="owntracks"):
    return await conn.fetchval(_COPY, channel, sender, external_id, _ENVELOPE)


async def _tombstone(conn, key: str = _KEY) -> None:
    """The deleter's work, left uncommitted when ``conn`` is in an open transaction."""
    grant = uuid4()
    await conn.execute("SELECT connectors.owntracks_forget_lock()")
    await conn.execute(
        "INSERT INTO connectors.owntracks_retention_batches "
        "(grant_id, item_count, deleted_count, already_forgotten_count) VALUES ($1, 1, 0, 1)",
        grant,
    )
    await conn.execute(
        "INSERT INTO connectors.owntracks_retention_tombstones "
        "(logical_source_key, raw_source_id, content_digest, grant_id) VALUES ($1, $2, $3, $4)",
        key,
        uuid4(),
        "v1:" + "a" * 64,
        grant,
    )


@pytest.fixture
async def events_db(db, head_url):
    events_db = await db.open(head_url, max_size=3)
    await events_db.execute("SELECT connectors.connectors_filtered_events_ensure_partition(now())")
    return events_db


@_async
async def test_a_copy_of_a_forgotten_fix_is_withheld(events_db) -> None:
    await _tombstone(events_db)
    assert await _copy(events_db) == {**_ENVELOPE, **_WITHHELD}


@_async
@pytest.mark.parametrize(
    ("sender", "external_id", "channel"),
    [
        ("alice", "2:location", "owntracks"),
        ("bob", "1:location", "owntracks"),
        ("alice", "1:transition:enter", "owntracks"),
        ("alice", "1:location", "telegram"),
    ],
    ids=["other-fix", "other-device", "transition", "other-channel"],
)
async def test_any_other_copy_is_kept(events_db, sender, external_id, channel) -> None:
    await _tombstone(events_db)
    assert await _copy(events_db, sender, external_id, channel) == _ENVELOPE


@_async
async def test_a_copy_racing_the_deleter_is_withheld_after_it_commits(events_db) -> None:
    deleter = await events_db.acquire()
    try:
        transaction = deleter.transaction()
        await transaction.start()
        await _tombstone(deleter)
        racing = asyncio.create_task(_copy(events_db))
        await asyncio.sleep(0.5)
        assert not racing.done()
        await transaction.commit()
        assert await asyncio.wait_for(racing, timeout=10) == {**_ENVELOPE, **_WITHHELD}
    finally:
        await events_db.release(deleter)


@_async
async def test_another_connector_does_not_wait_for_the_deleter(events_db) -> None:
    deleter = await events_db.acquire()
    try:
        async with deleter.transaction():
            await _tombstone(deleter)
            copy = await asyncio.wait_for(_copy(events_db, channel="telegram"), timeout=5)
            assert copy == _ENVELOPE
    finally:
        await events_db.release(deleter)


@_async
async def test_the_connector_role_writes_a_withheld_copy(events_db) -> None:
    await _tombstone(events_db)
    async with events_db.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE connector_writer")
        assert await _copy(conn) == {**_ENVELOPE, **_WITHHELD}


_SHARED_CATALOG = """
SELECT
    (SELECT array_agg(proname || ':' || oid || ':' || xmin ORDER BY proname) FROM pg_proc
     WHERE pronamespace = 'connectors'::regnamespace
       AND proname = 'filtered_events_withhold_forgotten'),
    (SELECT array_agg(tgname || ':' || oid || ':' || pg_get_triggerdef(oid) ORDER BY tgname)
     FROM pg_trigger WHERE tgname = 'filtered_events_withhold_forgotten' AND tgparentid = 0)
"""


def _catalog(db_url: str):
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            return tuple(connection.execute(text(_SHARED_CATALOG)).one())
    finally:
        engine.dispose()


def test_installed_once_and_the_last_downgrade_removes_it(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_344")
    command.upgrade(second, "core_344")
    assert _catalog(db_url) == (None, None)
    command.upgrade(first, "core_345")
    installed = _catalog(db_url)
    assert len(installed[0]) == 1
    (trigger,) = installed[1]
    assert "BEFORE INSERT ON connectors.filtered_events" in trigger
    command.upgrade(second, "core_345")
    assert _catalog(db_url) == installed, "a later schema's run rewrote the catalog"

    command.downgrade(second, "core_344")
    assert _catalog(db_url) == installed
    command.downgrade(first, "core_344")
    assert _catalog(db_url) == (None, None)


def test_a_later_schema_reinstalls_a_missing_trigger(postgres_container) -> None:
    """The function alone does not count as installed: the trigger is what withholds."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_345")
    command.upgrade(second, "core_344")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER filtered_events_withhold_forgotten ON connectors.filtered_events"
                )
            )
    finally:
        engine.dispose()
    command.upgrade(second, "core_345")
    (trigger,) = _catalog(db_url)[1]
    assert "BEFORE INSERT ON connectors.filtered_events" in trigger

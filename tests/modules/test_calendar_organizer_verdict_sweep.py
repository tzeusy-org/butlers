"""Real-Postgres organizer verdict stamping and the re-stamp sweep (REQ-module-calendar-040)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from butlers.modules.calendar import CalendarModule
from butlers.modules.calendar.organizer_trust import sweep_organizer_verdicts
from butlers.modules.calendar.organizer_verdict import VERDICT_VERSION

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_OWN = "me@example.com"
_SCHEMAS = {"relationship": "relationship", "switchboard": "switchboard"}


async def _pool(db):
    return await db("core", "relationship", "switchboard", schemas=_SCHEMAS)


def _module(pool) -> CalendarModule:
    module = CalendarModule()
    module._db = SimpleNamespace(pool=pool)
    module._projection_tables_available_cache = True
    return module


async def _event(pool, source_id, ref: str, organizer: str | None, *, stamped: int | None = None):
    metadata: dict = {
        "source_type": "provider_event",
        "organizer": organizer,
        "attendees": [{"email": _OWN, "self": True, "response_status": "needsAction"}],
    }
    if stamped is not None:
        metadata.update({"organizer_verdict": "known_entity", "verdict_version": stamped})
    await pool.execute(
        "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, "
        "ends_at, status, metadata, source_butler) VALUES ($1, $2, 'Invitation', 'UTC', $3, $4, "
        "'confirmed', "
        "$5, 'general')",
        source_id,
        ref,
        datetime(2026, 12, 1, 10, tzinfo=UTC),
        datetime(2026, 12, 1, 11, tzinfo=UTC),
        metadata,
    )


async def _seed(pool) -> None:
    entity = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, roles) VALUES ('Sam', '{}') RETURNING id"
    )
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, "
        "validity) VALUES ($1, 'has-email', 'sam@known.test', 'literal', 'test', 'active')",
        entity,
    )
    await pool.execute(
        "INSERT INTO switchboard.ingestion_rules (scope, rule_type, condition, action, priority, "
        "name) VALUES ('connector:google_calendar:me@example.com', 'sender_address', "
        "'{\"address\": \"spam@blocked.test\"}', 'block', 1, 'block spam')"
    )
    source = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
        "display_name, writable, metadata) VALUES ('provider:google:primary', 'provider_event', "
        "'user', 'google', 'primary', 'Primary', true, '{}') RETURNING id"
    )
    for ref, organizer in (
        ("known", "Sam@Known.test"),
        ("stranger", "who@nowhere.test"),
        ("blocked", "spam@blocked.test"),
        ("mine", _OWN),
    ):
        await _event(pool, source, ref, organizer)


async def _verdicts(pool) -> dict[str, str | None]:
    rows = await pool.fetch(
        "SELECT origin_ref, metadata ->> 'organizer_verdict' AS verdict FROM calendar_events"
    )
    return {r["origin_ref"]: r["verdict"] for r in rows}


async def test_the_sweep_stamps_pre_existing_invitations_including_a_blocked_organizer(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    assert await sweep_organizer_verdicts(pool) == 4
    assert await _verdicts(pool) == {
        "known": "known_entity",
        "stranger": "first_contact",
        "blocked": "blocked",
        "mine": "owner",
    }
    version = await pool.fetchval(
        "SELECT metadata ->> 'verdict_version' FROM calendar_events WHERE origin_ref = 'blocked'"
    )
    assert version == str(VERDICT_VERSION)


async def test_the_sweep_is_idempotent_and_bounded(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    assert await sweep_organizer_verdicts(pool, limit=3) == 3
    assert await sweep_organizer_verdicts(pool, limit=3) == 1
    assert await sweep_organizer_verdicts(pool, limit=3) == 0
    stamped_again = await pool.fetchval(
        "SELECT count(*) FROM calendar_events WHERE metadata ->> 'verdict_version' = $1",
        str(VERDICT_VERSION),
    )
    assert stamped_again == 4


async def test_an_older_version_is_restamped_and_a_current_one_is_left_alone(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    source = await pool.fetchval("SELECT id FROM calendar_sources LIMIT 1")
    await _event(pool, source, "old", "spam@blocked.test", stamped=VERDICT_VERSION - 1)
    await _event(pool, source, "current", "spam@blocked.test", stamped=VERDICT_VERSION)
    assert await sweep_organizer_verdicts(pool) == 5
    verdicts = await _verdicts(pool)
    assert verdicts["old"] == "blocked"
    assert verdicts["current"] == "known_entity"  # untouched: its stamp was already current


async def test_an_unreadable_identity_store_stamps_unknown_never_known(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await pool.execute("DROP FUNCTION public.organizer_identity_class(text)")
    await sweep_organizer_verdicts(pool)
    verdicts = await _verdicts(pool)
    assert verdicts["known"] == "unknown"
    assert verdicts["blocked"] == "unknown"
    assert verdicts["mine"] == "owner"  # the owner's own address needs no lookup


async def test_an_unreadable_policy_store_stamps_unknown_never_known(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await pool.execute("DROP FUNCTION public.organizer_policy_rules()")
    await sweep_organizer_verdicts(pool)
    verdicts = await _verdicts(pool)
    assert verdicts["known"] == "unknown"
    assert verdicts["stranger"] == "unknown"
    assert verdicts["mine"] == "owner"


async def test_projection_stamps_a_provider_event_as_it_writes_it(db) -> None:
    from butlers.modules.calendar.models import AttendeeInfo, CalendarEvent

    pool = await _pool(db)
    await _seed(pool)
    source = await pool.fetchval("SELECT id FROM calendar_sources LIMIT 1")
    module = _module(pool)
    module._butler_name = "general"
    when = datetime(2026, 12, 2, 10, tzinfo=UTC)

    def event(event_id: str, organizer: str) -> CalendarEvent:
        return CalendarEvent(
            event_id=event_id,
            title="Win a prize",
            start_at=when,
            end_at=when.replace(hour=11),
            timezone="UTC",
            organizer=organizer,
            attendees=[AttendeeInfo(email=_OWN, self_=True), AttendeeInfo(email=organizer)],
        )

    await module._project_provider_changes(
        source_id=source,
        provider_name="google",
        calendar_id="primary",
        updated_events=[event("e-spam", "spam@blocked.test"), event("e-sam", "sam@known.test")],
        cancelled_ids=[],
    )
    verdicts = await _verdicts(pool)
    assert verdicts["e-spam"] == "blocked"
    assert verdicts["e-sam"] == "known_entity"


def _migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/core/core_370_organizer_verdict_inputs.py"
    )
    spec = importlib.util.spec_from_file_location("core_370_ddl", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_a_transient_lookup_failure_is_retried_until_it_reads(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    migration = _migration()
    await pool.execute("DROP FUNCTION public.organizer_identity_class(text)")
    assert await sweep_organizer_verdicts(pool) == 4
    flags = await pool.fetch(
        "SELECT origin_ref, metadata ->> 'lookup_failed' AS failed FROM calendar_events"
    )
    assert {r["origin_ref"]: r["failed"] for r in flags} == {
        "known": "true",
        "stranger": "true",
        "blocked": "true",
        "mine": "false",  # the owner's own address needs no lookup, so nothing to retry
    }
    # The store comes back: only the failed rows are re-selected and now read correctly.
    await pool.execute(migration._IDENTITY_DDL)
    assert await sweep_organizer_verdicts(pool) == 3
    assert (await _verdicts(pool))["known"] == "known_entity"
    assert (await _verdicts(pool))["blocked"] == "blocked"
    assert await sweep_organizer_verdicts(pool) == 0


async def test_failed_rows_rotate_by_oldest_attempt_not_by_id(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await pool.execute("DROP FUNCTION public.organizer_identity_class(text)")
    await sweep_organizer_verdicts(pool)
    # All three lookups failed. Give them known attempt times, newest first by id order.
    for ref, when in (
        ("known", "2026-11-03T00:00:00+00:00"),
        ("stranger", "2026-11-02T00:00:00+00:00"),
        ("blocked", "2026-11-01T00:00:00+00:00"),
    ):
        await pool.execute(
            "UPDATE calendar_events SET metadata = jsonb_set(metadata, '{stamped_at}', "
            "to_jsonb($2::text)) WHERE origin_ref = $1",
            ref,
            when,
        )
    seen: list[str] = []
    for _ in range(3):
        before = {
            r["origin_ref"]: r["at"]
            for r in await pool.fetch(
                "SELECT origin_ref, metadata ->> 'stamped_at' AS at FROM calendar_events"
            )
        }
        assert await sweep_organizer_verdicts(pool, limit=1) == 1
        after = {
            r["origin_ref"]: r["at"]
            for r in await pool.fetch(
                "SELECT origin_ref, metadata ->> 'stamped_at' AS at FROM calendar_events"
            )
        }
        seen.append(next(ref for ref in after if after[ref] != before[ref]))
    assert seen == ["blocked", "stranger", "known"]  # oldest attempt first, each exactly once


async def test_a_non_integer_verdict_version_is_stale_not_a_crash(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await sweep_organizer_verdicts(pool)
    for ref, bad in (("known", '"abc"'), ("stranger", "1.5"), ("blocked", "null")):
        await pool.execute(
            "UPDATE calendar_events SET metadata = jsonb_set(metadata, '{verdict_version}', "
            "$2::jsonb) WHERE origin_ref = $1",
            ref,
            bad,
        )
    assert await sweep_organizer_verdicts(pool) == 3
    assert await sweep_organizer_verdicts(pool) == 0

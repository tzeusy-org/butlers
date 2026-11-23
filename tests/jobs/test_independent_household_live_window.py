import shutil

import pytest

from butlers.jobs.context_producers import run_calendar_context_producer
from tests.jobs.test_context_producers import _pool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture
def calendar_url(fresh_db):
    return fresh_db("core")


@pytest.mark.parametrize("kind", ["all_day", "recurring"])
async def test_live_household_time_is_counted_without_asserting_owner_signal(calendar_url, kind):
    pool = await _pool(calendar_url)
    try:
        await pool.execute("TRUNCATE calendar_events, calendar_sources CASCADE")
        source = await pool.fetchval(
            "INSERT INTO calendar_sources (source_key, source_kind, time_role) "
            "VALUES ('synthetic-household', 'provider_event', 'household_member') RETURNING id"
        )

        async def seed(ref, all_day=False, historical=False):
            return await pool.fetchval(
                """
                INSERT INTO calendar_events
                    (source_id, origin_ref, title, timezone, starts_at, ends_at, all_day,
                     status, source_butler, recurrence_rule)
                VALUES (
                    $1, $2, 'Opaque test event', 'UTC',
                    CASE WHEN $4 THEN now() - interval '1 day 5 minutes'
                        WHEN $3 THEN date_trunc('day', now())
                        ELSE now() - interval '5 minutes' END,
                    CASE WHEN $4 THEN now() - interval '1 day' + interval '15 minutes'
                        WHEN $3 THEN date_trunc('day', now()) + interval '1 day'
                        ELSE now() + interval '15 minutes' END,
                    $3, 'confirmed', 'general',
                    CASE WHEN $4 THEN 'RRULE:FREQ=DAILY' ELSE NULL END
                )
                RETURNING id
                """,
                source,
                ref,
                all_day,
                historical,
            )

        await seed("timed-control")
        control = await run_calendar_context_producer(pool)
        assert control["signal"] is None and control["household_events"] == 1
        event = await seed(
            "second-example", all_day=kind == "all_day", historical=kind == "recurring"
        )
        if kind == "recurring":
            await pool.execute(
                "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref, "
                "timezone, starts_at, ends_at) VALUES ($1, $2, 'synthetic-occurrence', 'UTC', "
                "now() - interval '5 minutes', now() + interval '15 minutes')",
                event,
                source,
            )
        result = await run_calendar_context_producer(pool)
        assert result["signal"] is None
        assert result["household_events"] == 2
    finally:
        await pool.close()

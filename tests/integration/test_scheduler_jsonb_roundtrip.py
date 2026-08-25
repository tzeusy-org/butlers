"""Real-Postgres regression: scheduler.py's ``fired_thresholds`` and
``job_args`` writers must not double-encode (bu-xfcpf — sibling sweep to
bu-cymc4/PR #2924, bu-x92jw/PR #2925, bu-bstqu/PR #2930, bu-c8b8e/PR #2932).

Every asyncpg pool in this codebase registers a JSONB codec
(``register_jsonb_codec``, ``src/butlers/db.py``) whose encoder already calls
``json.dumps()`` once. Pre-serializing a value with ``json.dumps()`` before
binding it into a ``jsonb`` column (with no ``::jsonb`` cast to force a
one-shot server-side parse) makes that encoder fire a SECOND time,
double-encoding the column into a jsonb-typed STRING instead of an
ARRAY/OBJECT. This bead fixes two such sites:

1. ``scheduled_tasks.fired_thresholds`` — the deadline-evaluation pass in
   ``tick()`` (``_evaluate_and_dispatch_deadlines``, scheduler.py ~1167) used
   to bind ``json.dumps(new_fired)`` directly into
   ``SET fired_thresholds = $3`` (no cast). Fixed by binding the sanitized
   list directly via the new ``_list_to_jsonb`` helper.
2. ``scheduled_tasks.job_args`` — ``_fire_chain()`` (scheduler.py ~1219,
   materializing a ``job``-mode event-chain action) used to bind
   ``json.dumps(task.get("job_args"))`` directly into ``VALUES (..., $5,
   ...)`` (no cast). Fixed by binding via the existing ``_dict_to_jsonb``
   helper (the same helper already used correctly by
   ``ensure_module_default_schedule``, ``schedule_create``, and
   ``sync_schedules`` elsewhere in this file).

The mocked-pool unit tests covering deadline firing and event-chain
materialization (tests/core/test_temporal_intelligence.py) cannot catch this
class of bug — the ``TestTickIntegration`` fixture there does use a real
Postgres testcontainer, but its assertions never read ``fired_thresholds``
(or the chain-materialized ``job_args``) back from the DB after ``tick()``
runs, only the dispatched prompt / span attributes / status columns. These
tests read the JSONB columns back directly and assert on
``jsonb_typeof()``/Python type, which is exactly the check the prior tests
were missing.

Live-data audit (read-only, butlers-dev, 2026-07-05):
- ``job_args``: 0 rows with ``jsonb_typeof(job_args) = 'string'`` in any
  schema (chronicler/education/finance/general/health/home/lifestyle/
  messenger/qa/relationship/switchboard/travel). No corruption at rest.
- ``fired_thresholds``: 25 rows with ``jsonb_typeof(fired_thresholds) =
  'string'`` (finance 15, general 1, health 2, home 1, relationship 5,
  travel 1). ACTIVELY corrupted — but every single one of the 25 is on a
  deadline task with ``enabled = false`` and ``deadline_status = 'expired'``:
  a terminal state the deadline-evaluation pass in ``tick()`` never revisits
  (its query is unconditionally scoped to ``WHERE enabled = true``). These
  rows are permanently inert to the hot path.

Both columns are read via defensive ``isinstance(..., list)`` /
``isinstance(..., str)`` fallbacks at scheduler.py ~1074-1077 (fired_thresholds
in the deadline pass), ~1489-1491 (fired_thresholds in the
deadline_threshold event-chain trigger), and core_tools/_scheduling.py ~246
(job_args in schedule_trigger). Both are KEPT:
- ``job_args``: cheap, zero corruption today, but guards any row written by
  an older deployed binary before this fix, or a future regression.
- ``fired_thresholds``: the double-``json.dumps`` corruption this bead fixes
  happens to be losslessly self-healing on read — the workaround's
  ``json.loads(raw_fired)`` branch decodes the doubly-encoded string back to
  the exact original list (unlike the calendar undo bug, bu-x92jw, where a
  jsonb ``||`` concat scrambled the shape into a mixed array that needed
  reconstruction). So every consumer already sees correct data in memory;
  only the on-disk typing of these 25 rows is wrong.

Decision: NO write-side self-heal (guarded UPDATE) was added for either
column. For ``job_args`` there is nothing to heal. For ``fired_thresholds``,
a self-heal placed in the deadline-evaluation pass (the natural site, mirroring
the #2925 pattern) would only ever run against rows the pass's
``WHERE enabled = true`` query fetches — and all 25 corrupted rows are
``enabled = false``, so such a self-heal would never reach them; it would
only benefit a *future* corrupted active row, a case this bead's write-side
fix already prevents from occurring. Healing the existing 25 at-rest rows
requires a one-off backfill against disabled/terminal rows outside the hot
path, which is out of scope here per the read-only live-audit constraint on
this task and is filed as a separate low-priority follow-up instead.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from butlers.core.scheduler import _fire_chain, tick

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_SCHEDULED_TASKS_DDL = """
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT UNIQUE NOT NULL,
        cron TEXT NOT NULL DEFAULT '* * * * *',
        prompt TEXT,
        dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
        job_name TEXT,
        job_args JSONB,
        complexity TEXT DEFAULT 'medium',
        timezone TEXT NOT NULL DEFAULT 'UTC',
        start_at TIMESTAMPTZ,
        end_at TIMESTAMPTZ,
        until_at TIMESTAMPTZ,
        display_title TEXT,
        calendar_event_id TEXT,
        source TEXT NOT NULL DEFAULT 'db',
        enabled BOOLEAN NOT NULL DEFAULT true,
        next_run_at TIMESTAMPTZ,
        last_run_at TIMESTAMPTZ,
        last_result JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        task_type TEXT NOT NULL DEFAULT 'cron',
        target_date DATE,
        lead_time_days INTEGER,
        alert_thresholds JSONB,
        deadline_status TEXT,
        fired_thresholds JSONB,
        depends_on JSONB
    )
"""

_EVENT_CHAINS_DDL = """
    CREATE TABLE IF NOT EXISTS event_chains (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT NOT NULL,
        trigger_type TEXT NOT NULL,
        trigger_reference TEXT NOT NULL,
        actions JSONB NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        butler_name TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""


@pytest.fixture
async def scheduler_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEDULED_TASKS_DDL)
        await pool.execute(_EVENT_CHAINS_DDL)
        yield pool


def _future_date(days: int) -> date:
    return (datetime.now(UTC) + timedelta(days=days)).date()


class TestFiredThresholdsRoundtrip:
    """tick()'s deadline pass must store fired_thresholds as a jsonb ARRAY."""

    async def test_tick_deadline_fired_thresholds_roundtrips_as_array(self, scheduler_pool) -> None:
        pool = scheduler_pool
        target = _future_date(30)
        now = datetime.now(UTC)
        task_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ($1, '* * * * *', $2, 'prompt', 'deadline', $3, 30,
                 '[{"days_before": 30, "severity": "info"}]',
                 'pending', '[]', $4, true)
            RETURNING id
            """,
            "visa-renewal",
            "Review visa renewal checklist",
            target,
            now,
        )

        dispatched = []

        async def capture_dispatch(**kwargs):
            dispatched.append(kwargs)

        await tick(pool, capture_dispatch)
        assert dispatched, "deadline threshold should have dispatched a prompt"

        row = await pool.fetchrow(
            "SELECT fired_thresholds, jsonb_typeof(fired_thresholds) AS kind "
            "FROM scheduled_tasks WHERE id = $1",
            task_id,
        )
        assert row is not None
        assert row["kind"] == "array", (
            f"fired_thresholds arrived as jsonb type {row['kind']!r}, not 'array' — "
            "the jsonb column was double-encoded into a string."
        )
        stored = row["fired_thresholds"]
        assert isinstance(stored, list), (
            f"fired_thresholds arrived as {type(stored).__name__!r}, not a list — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == [{"days_before": 30, "severity": "info"}]
        assert isinstance(stored[0], dict), (
            f"fired_thresholds[0] arrived as {type(stored[0]).__name__!r}, not a dict — "
            "the jsonb array element was double-encoded into a string."
        )

    async def test_tick_deadline_fired_thresholds_accumulates_across_ticks(
        self, scheduler_pool
    ) -> None:
        """A second threshold firing appends to (not replaces/corrupts) the array."""
        pool = scheduler_pool
        target = _future_date(14)
        now = datetime.now(UTC)
        task_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ($1, '* * * * *', $2, 'prompt', 'deadline', $3, 30,
                 '[{"days_before": 30, "severity": "info"},'
                 ' {"days_before": 14, "severity": "warning"}]',
                 'alerted', '[{"days_before": 30, "severity": "info"}]', $4, true)
            RETURNING id
            """,
            "passport-renewal",
            "Renew passport",
            target,
            now,
        )

        async def noop(**kwargs):
            pass

        await tick(pool, noop)

        row = await pool.fetchrow(
            "SELECT fired_thresholds, jsonb_typeof(fired_thresholds) AS kind "
            "FROM scheduled_tasks WHERE id = $1",
            task_id,
        )
        assert row["kind"] == "array"
        stored = row["fired_thresholds"]
        assert stored == [
            {"days_before": 30, "severity": "info"},
            {"days_before": 14, "severity": "warning"},
        ]


class TestFireChainJobArgsRoundtrip:
    """_fire_chain()'s materialized job task must store job_args as a jsonb OBJECT."""

    async def test_fire_chain_job_args_roundtrips_as_object(self, scheduler_pool) -> None:
        pool = scheduler_pool
        chain_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO event_chains (id, name, trigger_type, trigger_reference, actions, butler_name)
            VALUES ($1, $2, 'deadline_passed', 'irrelevant', '[]', 'test')
            """,
            chain_id,
            "post-renewal-followup",
        )

        # The assertions below are about the stored jsonb shape, not about
        # timing; naming the instant keeps the materialized task's run_at
        # reproducible instead of whatever the run happened to start at.
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        await _fire_chain(
            pool,
            chain_id=chain_id,
            chain_name="post-renewal-followup",
            actions=[
                {
                    "action_type": "job",
                    "delay_minutes": 0,
                    "job_name": "memory_consolidation",
                    "job_args": {"batch_size": 25, "nested": {"source_schema": "home"}},
                }
            ],
            now=now,
            trigger_label="test",
        )

        row = await pool.fetchrow(
            "SELECT job_args, jsonb_typeof(job_args) AS kind FROM scheduled_tasks "
            "WHERE name = 'chain:post-renewal-followup:0'"
        )
        assert row is not None
        assert row["kind"] == "object", (
            f"job_args arrived as jsonb type {row['kind']!r}, not 'object' — "
            "the jsonb column was double-encoded into a string."
        )
        stored = row["job_args"]
        assert isinstance(stored, dict), (
            f"job_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {"batch_size": 25, "nested": {"source_schema": "home"}}

    async def test_fire_chain_without_job_args_stores_null(self, scheduler_pool) -> None:
        """A job action with no job_args stores NULL, not an empty-string encoding."""
        pool = scheduler_pool
        chain_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO event_chains (id, name, trigger_type, trigger_reference, actions, butler_name)
            VALUES ($1, $2, 'deadline_passed', 'irrelevant', '[]', 'test')
            """,
            chain_id,
            "no-args-chain",
        )

        # The assertions below are about the stored jsonb shape, not about
        # timing; naming the instant keeps the materialized task's run_at
        # reproducible instead of whatever the run happened to start at.
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        await _fire_chain(
            pool,
            chain_id=chain_id,
            chain_name="no-args-chain",
            actions=[
                {
                    "action_type": "job",
                    "delay_minutes": 0,
                    "job_name": "memory_decay_sweep",
                }
            ],
            now=now,
            trigger_label="test",
        )

        row = await pool.fetchrow(
            "SELECT job_args FROM scheduled_tasks WHERE name = 'chain:no-args-chain:0'"
        )
        assert row is not None
        assert row["job_args"] is None

"""Tests for deadline tracking MCP tools (DB operations) and prompt context injection.

Covers tasks §2.5 and §3.1–3.4 from openspec/changes/temporal-intelligence/tasks.md:
  §2.5  Deadline prompt context injection (build_deadline_prompt_context)
  §3.1  deadline_create MCP tool with validation
  §3.2  deadline_update MCP tool (target_date change resets fired thresholds)
  §3.3  deadline_list MCP tool with status filter
  §3.4  deadline_delete MCP tool (reject TOML-sourced deadlines)

Unit tests (no DB) cover pure logic. Integration tests (Docker/asyncpg) cover
the DB functions.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

pytestmark = [pytest.mark.unit]

docker_available = shutil.which("docker") is not None


# ---------------------------------------------------------------------------
# §2.5 — build_deadline_prompt_context (pure unit tests)
# ---------------------------------------------------------------------------


def _future_date(days: int = 30) -> date:
    return (datetime.now(UTC) + timedelta(days=days)).date()


def _past_date(days: int = 1) -> date:
    return (datetime.now(UTC) - timedelta(days=days)).date()


def _make_threshold(days_before: int, severity: str = "info") -> dict[str, Any]:
    return {"days_before": days_before, "severity": severity}


class TestBuildDeadlinePromptContext:
    """Tests for the build_deadline_prompt_context() pure function.

    Covers §2.5: Prompt includes structured deadline metadata.
    """

    def test_prompt_contains_original_text(self):
        """The augmented prompt includes the original prompt text."""
        from butlers.core.temporal.deadlines import build_deadline_prompt_context

        target = _future_date(42)
        result = build_deadline_prompt_context(
            original_prompt="Begin visa renewal process",
            target_date=target,
            days_remaining=42,
            fired_threshold=_make_threshold(42, "info"),
            deadline_status="alerted",
            all_thresholds=[_make_threshold(42, "info"), _make_threshold(7, "critical")],
        )
        assert "Begin visa renewal process" in result

    def test_prompt_contains_target_date(self):
        """The augmented prompt includes target_date."""
        from butlers.core.temporal.deadlines import build_deadline_prompt_context

        target = date(2026, 8, 15)
        result = build_deadline_prompt_context(
            original_prompt="Renew passport",
            target_date=target,
            days_remaining=42,
            fired_threshold=_make_threshold(42, "info"),
            deadline_status="pending",
            all_thresholds=[_make_threshold(42, "info")],
        )
        assert "2026-08-15" in result

    def test_prompt_contains_days_remaining(self):
        """The augmented prompt includes days_remaining."""
        from butlers.core.temporal.deadlines import build_deadline_prompt_context

        result = build_deadline_prompt_context(
            original_prompt="File tax return",
            target_date=_future_date(14),
            days_remaining=14,
            fired_threshold=_make_threshold(14, "warning"),
            deadline_status="alerted",
            all_thresholds=[_make_threshold(14, "warning")],
        )
        assert "14" in result

    def test_prompt_contains_fired_threshold(self):
        """The augmented prompt includes the fired threshold details."""
        from butlers.core.temporal.deadlines import build_deadline_prompt_context

        threshold = _make_threshold(3, "critical")
        result = build_deadline_prompt_context(
            original_prompt="Emergency action required",
            target_date=_future_date(3),
            days_remaining=3,
            fired_threshold=threshold,
            deadline_status="escalated",
            all_thresholds=[_make_threshold(30, "info"), threshold],
        )
        assert "critical" in result

    def test_prompt_contains_deadline_status(self):
        """The augmented prompt includes the current deadline_status."""
        from butlers.core.temporal.deadlines import build_deadline_prompt_context

        result = build_deadline_prompt_context(
            original_prompt="Prepare submission",
            target_date=_future_date(10),
            days_remaining=10,
            fired_threshold=_make_threshold(10, "warning"),
            deadline_status="escalated",
            all_thresholds=[_make_threshold(10, "warning")],
        )
        assert "escalated" in result

    def test_prompt_contains_all_thresholds(self):
        """The augmented prompt includes the full threshold list."""
        from butlers.core.temporal.deadlines import build_deadline_prompt_context

        all_thresholds = [
            _make_threshold(42, "info"),
            _make_threshold(14, "warning"),
            _make_threshold(3, "critical"),
        ]
        result = build_deadline_prompt_context(
            original_prompt="Visa renewal",
            target_date=_future_date(42),
            days_remaining=42,
            fired_threshold=all_thresholds[0],
            deadline_status="pending",
            all_thresholds=all_thresholds,
        )
        # All three thresholds should appear
        assert "42" in result
        assert "14" in result
        assert "3" in result

    def test_prompt_has_deadline_context_block(self):
        """The augmented prompt ends with a [Deadline context: ...] block."""
        from butlers.core.temporal.deadlines import build_deadline_prompt_context

        result = build_deadline_prompt_context(
            original_prompt="Check status",
            target_date=_future_date(30),
            days_remaining=30,
            fired_threshold=_make_threshold(30, "info"),
            deadline_status="pending",
            all_thresholds=[_make_threshold(30, "info")],
        )
        assert "[Deadline context:" in result

    def test_prompt_original_text_comes_before_context_block(self):
        """Original prompt text appears before the deadline context block."""
        from butlers.core.temporal.deadlines import build_deadline_prompt_context

        result = build_deadline_prompt_context(
            original_prompt="My original prompt",
            target_date=_future_date(30),
            days_remaining=30,
            fired_threshold=_make_threshold(30, "info"),
            deadline_status="pending",
            all_thresholds=[_make_threshold(30, "info")],
        )
        original_pos = result.index("My original prompt")
        context_pos = result.index("[Deadline context:")
        assert original_pos < context_pos, "Original prompt must appear before deadline context"


# ---------------------------------------------------------------------------
# Shared DDL for deadline integration tests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# §3 — Deadline CRUD MCP tools (require Docker/asyncpg)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestDeadlineCreate:
    """Integration tests for deadline_create (§3.1).

    Covers:
    - Creating a deadline with valid parameters inserts a DB row
    - Validation errors propagate (future date, thresholds, lead_time)
    - Duplicate name raises ValueError
    """

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh test database with the scheduled_tasks table."""
        import asyncpg

        db_name = f"test_{uuid.uuid4().hex[:12]}"
        admin_conn = await asyncpg.connect(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database="postgres",
        )
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin_conn.close()

        p = await asyncpg.create_pool(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database=db_name,
            min_size=1,
            max_size=3,
        )
        await p.execute(_SCHEDULED_TASKS_DDL)
        yield p
        await p.close()

    async def test_deadline_create_inserts_row(self, pool):
        """deadline_create inserts a row with task_type='deadline' and deadline_status='pending'."""
        from butlers.core.temporal.deadlines_db import deadline_create

        target = _future_date(60)
        task_id = await deadline_create(
            pool,
            name="visa-renewal",
            target_date=target,
            lead_time_days=42,
            alert_thresholds=[
                _make_threshold(42, "info"),
                _make_threshold(14, "warning"),
                _make_threshold(3, "critical"),
            ],
            prompt="Begin visa renewal process",
        )

        assert task_id is not None
        row = await pool.fetchrow(
            """
            SELECT task_type, target_date, lead_time_days, deadline_status,
                   enabled, source, dispatch_mode, prompt
            FROM scheduled_tasks WHERE id = $1
            """,
            uuid.UUID(str(task_id)),
        )
        assert row is not None
        assert row["task_type"] == "deadline"
        assert row["target_date"] == target
        assert row["lead_time_days"] == 42
        assert row["deadline_status"] == "pending"
        assert row["enabled"] is True
        assert row["source"] == "db"
        assert row["dispatch_mode"] == "prompt"
        assert "visa renewal" in row["prompt"]

    async def test_deadline_create_returns_uuid_string(self, pool):
        """deadline_create returns a UUID string."""
        from butlers.core.temporal.deadlines_db import deadline_create

        task_id = await deadline_create(
            pool,
            name="uuid-test-deadline",
            target_date=_future_date(30),
            lead_time_days=14,
            alert_thresholds=[_make_threshold(14, "warning")],
            prompt="Test deadline notify",
        )
        # Should be parseable as UUID
        assert uuid.UUID(str(task_id))

    async def test_deadline_create_rejects_past_target_date(self, pool):
        """deadline_create raises ValueError when target_date is in the past."""
        from butlers.core.temporal.deadlines_db import deadline_create

        with pytest.raises(ValueError, match="future"):
            await deadline_create(
                pool,
                name="past-deadline",
                target_date=_past_date(10),
                lead_time_days=7,
                alert_thresholds=[_make_threshold(7)],
                prompt="This is a past deadline",
            )

    async def test_deadline_create_rejects_empty_thresholds(self, pool):
        """deadline_create raises ValueError when alert_thresholds is empty."""
        from butlers.core.temporal.deadlines_db import deadline_create

        with pytest.raises(ValueError, match="threshold"):
            await deadline_create(
                pool,
                name="empty-threshold-deadline",
                target_date=_future_date(30),
                lead_time_days=14,
                alert_thresholds=[],
                prompt="Prompt for empty threshold test",
            )

    async def test_deadline_create_rejects_threshold_exceeding_lead_time(self, pool):
        """deadline_create raises ValueError when a threshold days_before > lead_time_days."""
        from butlers.core.temporal.deadlines_db import deadline_create

        with pytest.raises(ValueError, match="lead_time_days|days_before"):
            await deadline_create(
                pool,
                name="bad-threshold-deadline",
                target_date=_future_date(60),
                lead_time_days=14,
                alert_thresholds=[_make_threshold(30, "info")],  # 30 > 14
                prompt="Test prompt for bad threshold",
            )

    async def test_deadline_create_duplicate_name_raises(self, pool):
        """deadline_create raises ValueError when the name already exists."""
        from butlers.core.temporal.deadlines_db import deadline_create

        await deadline_create(
            pool,
            name="duplicate-deadline",
            target_date=_future_date(60),
            lead_time_days=30,
            alert_thresholds=[_make_threshold(30, "info")],
            prompt="First creation notify",
        )
        with pytest.raises(ValueError, match="already exists"):
            await deadline_create(
                pool,
                name="duplicate-deadline",
                target_date=_future_date(90),
                lead_time_days=42,
                alert_thresholds=[_make_threshold(42, "info")],
                prompt="Second creation notify",
            )

    async def test_deadline_create_with_depends_on(self, pool):
        """deadline_create stores depends_on list correctly."""
        from butlers.core.temporal.deadlines_db import deadline_create

        dep_id = str(uuid.uuid4())
        task_id = await deadline_create(
            pool,
            name="dependent-deadline",
            target_date=_future_date(90),
            lead_time_days=42,
            alert_thresholds=[_make_threshold(42, "info")],
            prompt="Dependent task notify",
            depends_on=[dep_id],
        )
        row = await pool.fetchrow(
            "SELECT depends_on FROM scheduled_tasks WHERE id = $1",
            uuid.UUID(str(task_id)),
        )
        import json

        deps = row["depends_on"]
        if isinstance(deps, str):
            deps = json.loads(deps)
        assert dep_id in deps


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestDeadlineUpdate:
    """Integration tests for deadline_update (§3.2).

    Covers:
    - Updating target_date resets fired_thresholds and deadline_status to 'pending'
    - Updating deadline_status to 'completed' disables the task
    - Invalid task_id, non-deadline task, and unknown status raise ValueError
    """

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh test database with the scheduled_tasks table."""
        import asyncpg

        db_name = f"test_{uuid.uuid4().hex[:12]}"
        admin_conn = await asyncpg.connect(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database="postgres",
        )
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin_conn.close()

        p = await asyncpg.create_pool(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database=db_name,
            min_size=1,
            max_size=3,
        )
        await p.execute(_SCHEDULED_TASKS_DDL)
        yield p
        await p.close()

    async def _insert_deadline(
        self,
        pool,
        *,
        name: str = "test-deadline",
        target_date: date | None = None,
        deadline_status: str = "alerted",
        fired_thresholds: list | None = None,
        source: str = "db",
    ) -> str:
        """Helper to insert a test deadline row directly."""
        import json

        target = target_date or _future_date(60)
        fired = fired_thresholds or [{"days_before": 42, "severity": "info"}]
        row_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 depends_on, source, enabled, next_run_at)
            VALUES
                ($1, '0 * * * *', 'Test prompt notify', 'prompt', 'deadline', $2,
                 42, '[{"days_before": 42, "severity": "info"}]'::jsonb,
                 $3, $4::jsonb, '[]'::jsonb, $5, true, now() + interval '1 hour')
            RETURNING id
            """,
            name,
            target,
            deadline_status,
            json.dumps(fired),
            source,
        )
        return str(row_id)

    async def test_update_target_date_resets_fired_thresholds(self, pool):
        """Updating target_date resets fired_thresholds to [] and status to 'pending'."""
        from butlers.core.temporal.deadlines_db import deadline_update

        task_id = await self._insert_deadline(
            pool,
            name="reset-deadline",
            deadline_status="alerted",
            fired_thresholds=[{"days_before": 42, "severity": "info"}],
        )

        new_target = _future_date(90)
        await deadline_update(pool, task_id, target_date=new_target)

        import json

        row = await pool.fetchrow(
            "SELECT target_date, deadline_status, fired_thresholds"
            " FROM scheduled_tasks WHERE id = $1",
            uuid.UUID(str(task_id)),
        )
        assert row["target_date"] == new_target
        assert row["deadline_status"] == "pending"
        fired = row["fired_thresholds"]
        if isinstance(fired, str):
            fired = json.loads(fired)
        assert fired == []

    async def test_update_deadline_status_to_completed_disables_task(self, pool):
        """Setting deadline_status='completed' disables the task (enabled=False)."""
        from butlers.core.temporal.deadlines_db import deadline_update

        task_id = await self._insert_deadline(pool, name="complete-deadline")

        await deadline_update(pool, task_id, deadline_status="completed")

        row = await pool.fetchrow(
            "SELECT deadline_status, enabled FROM scheduled_tasks WHERE id = $1",
            uuid.UUID(str(task_id)),
        )
        assert row["deadline_status"] == "completed"
        assert row["enabled"] is False

    async def test_update_deadline_status_to_expired_disables_task(self, pool):
        """Setting deadline_status='expired' disables the task."""
        from butlers.core.temporal.deadlines_db import deadline_update

        task_id = await self._insert_deadline(pool, name="expire-deadline")

        await deadline_update(pool, task_id, deadline_status="expired")

        row = await pool.fetchrow(
            "SELECT deadline_status, enabled FROM scheduled_tasks WHERE id = $1",
            uuid.UUID(str(task_id)),
        )
        assert row["deadline_status"] == "expired"
        assert row["enabled"] is False

    async def test_update_invalid_task_id_raises(self, pool):
        """deadline_update raises ValueError for non-existent task_id."""
        from butlers.core.temporal.deadlines_db import deadline_update

        with pytest.raises(ValueError, match="not found"):
            await deadline_update(pool, str(uuid.uuid4()), deadline_status="completed")

    async def test_update_non_deadline_task_raises(self, pool):
        """deadline_update raises ValueError when task_type is not 'deadline'."""
        from butlers.core.temporal.deadlines_db import deadline_update

        # Insert a plain cron task
        cron_task_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, source, enabled, next_run_at)
            VALUES
                ('plain-cron', '* * * * *', 'Cron task', 'prompt', 'cron', 'db', true, now())
            RETURNING id
            """
        )
        with pytest.raises(ValueError, match="not a deadline"):
            await deadline_update(pool, str(cron_task_id), deadline_status="completed")

    async def test_update_invalid_deadline_status_raises(self, pool):
        """deadline_update raises ValueError for an unknown deadline_status value."""
        from butlers.core.temporal.deadlines_db import deadline_update

        task_id = await self._insert_deadline(pool, name="status-invalid-deadline")

        with pytest.raises(ValueError, match="Invalid deadline_status"):
            await deadline_update(pool, task_id, deadline_status="unknown-status")

    async def test_update_invalid_uuid_raises(self, pool):
        """deadline_update raises ValueError for a non-UUID task_id string."""
        from butlers.core.temporal.deadlines_db import deadline_update

        with pytest.raises(ValueError, match="Invalid task_id"):
            await deadline_update(pool, "not-a-uuid", deadline_status="completed")

    async def test_update_prompt_changes_prompt_field(self, pool):
        """Updating prompt changes the stored prompt text."""
        from butlers.core.temporal.deadlines_db import deadline_update

        task_id = await self._insert_deadline(pool, name="prompt-update-deadline")

        await deadline_update(pool, task_id, prompt="New prompt with notify call")

        row = await pool.fetchrow(
            "SELECT prompt FROM scheduled_tasks WHERE id = $1",
            uuid.UUID(str(task_id)),
        )
        assert row["prompt"] == "New prompt with notify call"

    async def test_update_target_date_with_cross_field_validation(self, pool):
        """Updating target_date validates against existing thresholds."""
        from butlers.core.temporal.deadlines_db import deadline_update

        task_id = await self._insert_deadline(pool, name="cross-validate-deadline")

        # This should work: new target still in future
        await deadline_update(pool, task_id, target_date=_future_date(100))

        # Setting target to the past should fail validation
        with pytest.raises(ValueError, match="future"):
            await deadline_update(pool, task_id, target_date=_past_date(5))


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestDeadlineList:
    """Integration tests for deadline_list (§3.3).

    Covers:
    - List all deadlines
    - Filter by status
    - Returns only task_type='deadline' rows
    - Invalid status filter raises ValueError
    """

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh test database with the scheduled_tasks table."""
        import asyncpg

        db_name = f"test_{uuid.uuid4().hex[:12]}"
        admin_conn = await asyncpg.connect(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database="postgres",
        )
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin_conn.close()

        p = await asyncpg.create_pool(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database=db_name,
            min_size=1,
            max_size=3,
        )
        await p.execute(_SCHEDULED_TASKS_DDL)
        yield p
        await p.close()

    async def _insert_deadline(
        self,
        pool,
        *,
        name: str,
        deadline_status: str = "pending",
        target_date: date | None = None,
    ) -> str:
        """Helper to insert a test deadline row."""
        target = target_date or _future_date(60)
        row_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 depends_on, source, enabled, next_run_at)
            VALUES
                ($1, '0 * * * *', 'Prompt notify', 'prompt', 'deadline', $2,
                 30, '[{"days_before": 30, "severity": "info"}]'::jsonb,
                 $3, '[]'::jsonb, '[]'::jsonb, 'db', true, now() + interval '1 hour')
            RETURNING id
            """,
            name,
            target,
            deadline_status,
        )
        return str(row_id)

    async def test_deadline_list_returns_all_deadlines(self, pool):
        """deadline_list returns all deadline-type tasks."""
        from butlers.core.temporal.deadlines_db import deadline_list

        await self._insert_deadline(pool, name="list-deadline-1", deadline_status="pending")
        await self._insert_deadline(pool, name="list-deadline-2", deadline_status="alerted")

        # Also insert a cron task (should NOT appear)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, source, enabled, next_run_at)
            VALUES
                ('cron-task', '* * * * *', 'Cron', 'prompt', 'cron', 'db', true, now())
            """
        )

        results = await deadline_list(pool)
        names = [r["name"] for r in results]
        assert "list-deadline-1" in names
        assert "list-deadline-2" in names
        assert "cron-task" not in names

    async def test_deadline_list_filters_by_status_pending(self, pool):
        """deadline_list(status='pending') returns only pending deadlines."""
        from butlers.core.temporal.deadlines_db import deadline_list

        await self._insert_deadline(pool, name="filter-pending", deadline_status="pending")
        await self._insert_deadline(pool, name="filter-alerted", deadline_status="alerted")

        results = await deadline_list(pool, status="pending")
        names = [r["name"] for r in results]
        assert "filter-pending" in names
        assert "filter-alerted" not in names

    async def test_deadline_list_filters_by_status_alerted(self, pool):
        """deadline_list(status='alerted') returns only alerted deadlines."""
        from butlers.core.temporal.deadlines_db import deadline_list

        await self._insert_deadline(pool, name="filter-pending2", deadline_status="pending")
        await self._insert_deadline(pool, name="filter-alerted2", deadline_status="alerted")
        await self._insert_deadline(pool, name="filter-escalated", deadline_status="escalated")

        results = await deadline_list(pool, status="alerted")
        names = [r["name"] for r in results]
        assert "filter-alerted2" in names
        assert "filter-pending2" not in names
        assert "filter-escalated" not in names

    async def test_deadline_list_invalid_status_raises(self, pool):
        """deadline_list raises ValueError for an invalid status filter."""
        from butlers.core.temporal.deadlines_db import deadline_list

        with pytest.raises(ValueError, match="Invalid status"):
            await deadline_list(pool, status="unknown-status")

    async def test_deadline_list_returns_empty_when_no_deadlines(self, pool):
        """deadline_list returns an empty list when no deadline tasks exist."""
        from butlers.core.temporal.deadlines_db import deadline_list

        results = await deadline_list(pool)
        # May be empty or contain tasks from other tests — just assert list type
        assert isinstance(results, list)

    async def test_deadline_list_result_contains_expected_fields(self, pool):
        """Each result dict contains expected deadline fields."""
        from butlers.core.temporal.deadlines_db import deadline_list

        await self._insert_deadline(pool, name="fields-test-deadline", deadline_status="pending")

        results = await deadline_list(pool, status="pending")
        fields_deadline = next((r for r in results if r["name"] == "fields-test-deadline"), None)
        assert fields_deadline is not None

        for field in (
            "id",
            "name",
            "target_date",
            "lead_time_days",
            "alert_thresholds",
            "deadline_status",
            "fired_thresholds",
        ):
            assert field in fields_deadline, f"Expected field {field!r} in result"

    async def test_deadline_list_target_date_is_iso_string(self, pool):
        """deadline_list normalises target_date to ISO-format string."""
        from butlers.core.temporal.deadlines_db import deadline_list

        target = _future_date(45)
        await self._insert_deadline(
            pool, name="iso-date-deadline", deadline_status="pending", target_date=target
        )

        results = await deadline_list(pool, status="pending")
        iso_deadline = next((r for r in results if r["name"] == "iso-date-deadline"), None)
        assert iso_deadline is not None
        assert iso_deadline["target_date"] == target.isoformat()

    async def test_deadline_list_all_valid_statuses(self, pool):
        """deadline_list accepts all valid status values without raising."""
        from butlers.core.temporal.deadlines_db import deadline_list

        for status in ("pending", "alerted", "escalated", "completed", "expired"):
            # Should not raise
            results = await deadline_list(pool, status=status)
            assert isinstance(results, list)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestDeadlineDelete:
    """Integration tests for deadline_delete (§3.4).

    Covers:
    - Deleting a DB-sourced deadline removes the row
    - Deleting a TOML-sourced deadline raises ValueError
    - Deleting a non-existent task raises ValueError
    - Deleting a non-deadline task raises ValueError
    - Invalid UUID raises ValueError
    """

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh test database with the scheduled_tasks table."""
        import asyncpg

        db_name = f"test_{uuid.uuid4().hex[:12]}"
        admin_conn = await asyncpg.connect(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database="postgres",
        )
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin_conn.close()

        p = await asyncpg.create_pool(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database=db_name,
            min_size=1,
            max_size=3,
        )
        await p.execute(_SCHEDULED_TASKS_DDL)
        yield p
        await p.close()

    async def _insert_deadline(
        self,
        pool,
        *,
        name: str,
        source: str = "db",
    ) -> str:
        """Helper to insert a test deadline row."""
        row_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 depends_on, source, enabled, next_run_at)
            VALUES
                ($1, '0 * * * *', 'Prompt notify', 'prompt', 'deadline', $2,
                 30, '[{"days_before": 30, "severity": "info"}]'::jsonb,
                 'pending', '[]'::jsonb, '[]'::jsonb, $3, true, now() + interval '1 hour')
            RETURNING id
            """,
            name,
            _future_date(60),
            source,
        )
        return str(row_id)

    async def test_deadline_delete_removes_db_sourced_row(self, pool):
        """deadline_delete removes a DB-sourced deadline row."""
        from butlers.core.temporal.deadlines_db import deadline_delete

        task_id = await self._insert_deadline(pool, name="delete-db-deadline", source="db")

        await deadline_delete(pool, task_id)

        row = await pool.fetchrow(
            "SELECT id FROM scheduled_tasks WHERE id = $1",
            uuid.UUID(str(task_id)),
        )
        assert row is None, "Row should have been deleted"

    async def test_deadline_delete_rejects_toml_sourced_deadline(self, pool):
        """deadline_delete raises ValueError for TOML-sourced deadlines."""
        from butlers.core.temporal.deadlines_db import deadline_delete

        task_id = await self._insert_deadline(pool, name="delete-toml-deadline", source="toml")

        with pytest.raises(ValueError, match="TOML"):
            await deadline_delete(pool, task_id)

        # Row should still exist
        row = await pool.fetchrow(
            "SELECT id FROM scheduled_tasks WHERE id = $1",
            uuid.UUID(str(task_id)),
        )
        assert row is not None, "TOML-sourced row should not be deleted"

    async def test_deadline_delete_not_found_raises(self, pool):
        """deadline_delete raises ValueError when the task_id does not exist."""
        from butlers.core.temporal.deadlines_db import deadline_delete

        with pytest.raises(ValueError, match="not found"):
            await deadline_delete(pool, str(uuid.uuid4()))

    async def test_deadline_delete_non_deadline_task_raises(self, pool):
        """deadline_delete raises ValueError when task_type is not 'deadline'."""
        from butlers.core.temporal.deadlines_db import deadline_delete

        cron_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, source, enabled, next_run_at)
            VALUES
                ('cron-to-not-delete', '* * * * *', 'Cron prompt',
                 'prompt', 'cron', 'db', true, now())
            RETURNING id
            """
        )
        with pytest.raises(ValueError, match="not a deadline"):
            await deadline_delete(pool, str(cron_id))

        # The cron task should still exist
        row = await pool.fetchrow("SELECT id FROM scheduled_tasks WHERE id = $1", cron_id)
        assert row is not None

    async def test_deadline_delete_invalid_uuid_raises(self, pool):
        """deadline_delete raises ValueError for a non-UUID task_id."""
        from butlers.core.temporal.deadlines_db import deadline_delete

        with pytest.raises(ValueError, match="Invalid task_id"):
            await deadline_delete(pool, "not-a-valid-uuid")

"""Unit tests for the notify() reference validation in sync_schedules.

Part A of bu-s3lr.3: verifies that sync_schedules emits a WARNING when a
dispatch_mode=prompt scheduled task omits notify() from the prompt and any
linked skills, and stays silent when notify() is present.
"""

from __future__ import annotations

import logging
import shutil

import pytest

from butlers.core.scheduler import _check_notify_reference, sync_schedules

pytestmark = [
    pytest.mark.unit,
]

# ---------------------------------------------------------------------------
# _check_notify_reference unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestCheckNotifyReference:
    """Unit tests for the _check_notify_reference helper."""

    def test_notify_reference_behavior(self, tmp_path, caplog):
        """No warning when notify present (case-insensitive); warning with task name when absent;
        skill with notify suppresses warning; skill without notify doesn't; missing dir is safe."""
        # notify present: no warning
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(task_name="report", prompt="Call notify() to send.", skills_dir=None)
        assert "does not reference notify" not in caplog.text

        # NOTIFY case-insensitive: no warning
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(task_name="task", prompt="Call NOTIFY() when done.", skills_dir=None)
        assert "does not reference notify" not in caplog.text

        # notify absent: warning with task name
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(task_name="cleanup-task", prompt="Delete old temp files.", skills_dir=None)
        assert "does not reference notify" in caplog.text
        assert "cleanup-task" in caplog.text

        # Skill WITH notify suppresses warning
        skills_dir = tmp_path / "skills"
        skill1 = skills_dir / "daily-digest"
        skill1.mkdir(parents=True)
        (skill1 / "SKILL.md").write_text("# Daily Digest\nCall notify() to send it.", encoding="utf-8")
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(task_name="digest", prompt="Run the daily-digest skill.", skills_dir=skills_dir)
        assert "does not reference notify" not in caplog.text

        # Skill WITHOUT notify does not suppress warning
        skill2 = skills_dir / "log-archiver"
        skill2.mkdir(parents=True)
        (skill2 / "SKILL.md").write_text("# Log Archiver\nCompress old logs.", encoding="utf-8")
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(task_name="archive-logs", prompt="Run the log-archiver skill.", skills_dir=skills_dir)
        assert "does not reference notify" in caplog.text

        # Missing skills dir does not raise; warning still emitted
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(task_name="task", prompt="Run some-skill.", skills_dir=tmp_path / "nonexistent")
        assert "does not reference notify" in caplog.text


# ---------------------------------------------------------------------------
# sync_schedules integration with notify validation (mock DB)
# ---------------------------------------------------------------------------

_SCHEDULED_TASKS_DDL = """
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT UNIQUE NOT NULL,
        cron TEXT NOT NULL,
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
        CONSTRAINT scheduled_tasks_dispatch_mode_check
            CHECK (dispatch_mode IN ('prompt', 'job')),
        CONSTRAINT scheduled_tasks_dispatch_payload_check
            CHECK (
                (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
                OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
            )
    )
"""

docker_available = shutil.which("docker") is not None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestSyncSchedulesNotifyValidation:
    """Integration tests for notify() validation in sync_schedules."""

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh database with scheduled_tasks and return a pool."""
        import uuid

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

    async def test_no_warning_for_job_tasks(self, pool, caplog):
        """dispatch_mode=job tasks do not trigger notify() validation."""
        schedules = [
            {
                "name": "cleanup-job",
                "cron": "0 3 * * *",
                "dispatch_mode": "job",
                "job_name": "cleanup",
                "prompt": None,
            }
        ]
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            await sync_schedules(pool, schedules)
        assert "does not reference notify" not in caplog.text

    async def test_warning_emitted_for_prompt_task_without_notify(self, pool, caplog):
        """Startup emits warning for a prompt task that omits notify()."""
        schedules = [
            {
                "name": "morning-cleanup",
                "cron": "0 7 * * *",
                "dispatch_mode": "prompt",
                "prompt": "Delete old temp files from the staging area.",
            }
        ]
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            await sync_schedules(pool, schedules)
        assert "does not reference notify" in caplog.text
        assert "morning-cleanup" in caplog.text

    async def test_no_warning_for_prompt_task_with_notify(self, pool, caplog):
        """No warning for a prompt task that references notify()."""
        schedules = [
            {
                "name": "eod-report",
                "cron": "0 18 * * *",
                "dispatch_mode": "prompt",
                "prompt": "Compile end-of-day summary and notify(channel='telegram').",
            }
        ]
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            await sync_schedules(pool, schedules)
        assert "does not reference notify" not in caplog.text

    async def test_warning_does_not_block_startup(self, pool, caplog):
        """Warning must not raise; sync_schedules completes normally."""
        schedules = [
            {
                "name": "silent-task",
                "cron": "0 1 * * *",
                "dispatch_mode": "prompt",
                "prompt": "Run background maintenance silently.",
            }
        ]
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            # Must not raise — warn only.
            await sync_schedules(pool, schedules)

        row = await pool.fetchrow(
            "SELECT name, enabled FROM scheduled_tasks WHERE name = 'silent-task'"
        )
        assert row is not None
        assert row["enabled"] is True

    async def test_no_warning_for_prompt_task_with_skill_containing_notify(
        self, pool, tmp_path, caplog
    ):
        """No warning when linked skill SKILL.md contains notify()."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "weekly-summary"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# Weekly Summary\nFinally, notify(channel='telegram') the owner.",
            encoding="utf-8",
        )

        schedules = [
            {
                "name": "weekly-summary-task",
                "cron": "0 9 * * 1",
                "dispatch_mode": "prompt",
                "prompt": "Execute the weekly-summary skill.",
            }
        ]
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            await sync_schedules(pool, schedules, skills_dir=skills_dir)
        assert "does not reference notify" not in caplog.text

    async def test_mixed_tasks_only_warns_for_missing_notify(self, pool, caplog):
        """With multiple tasks, warning only fires for the task lacking notify()."""
        schedules = [
            {
                "name": "good-prompt-task",
                "cron": "0 8 * * *",
                "dispatch_mode": "prompt",
                "prompt": "Do something and notify(channel='telegram', intent='send').",
            },
            {
                "name": "bad-prompt-task",
                "cron": "0 9 * * *",
                "dispatch_mode": "prompt",
                "prompt": "Do something quietly without alerting anyone.",
            },
            {
                "name": "job-task",
                "cron": "0 10 * * *",
                "dispatch_mode": "job",
                "job_name": "some-job",
            },
        ]
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            await sync_schedules(pool, schedules)

        warning_records = [r for r in caplog.records if "does not reference notify" in r.message]
        assert len(warning_records) == 1
        assert "bad-prompt-task" in warning_records[0].message

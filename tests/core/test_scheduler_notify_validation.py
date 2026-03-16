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

    def test_no_warning_when_prompt_contains_notify(self, caplog):
        """No warning when 'notify' appears in the prompt text."""
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(
                task_name="daily-report",
                prompt="Summarize and then call notify() to deliver it.",
                skills_dir=None,
            )
        assert "does not reference notify" not in caplog.text

    def test_no_warning_when_prompt_contains_notify_uppercase(self, caplog):
        """Case-insensitive match: NOTIFY should suppress the warning."""
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(
                task_name="task",
                prompt="Call NOTIFY() when done.",
                skills_dir=None,
            )
        assert "does not reference notify" not in caplog.text

    def test_warning_when_prompt_missing_notify(self, caplog):
        """Warning is emitted when 'notify' is absent from prompt and no skills provided."""
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(
                task_name="cleanup-task",
                prompt="Delete old temp files.",
                skills_dir=None,
            )
        assert "does not reference notify" in caplog.text
        assert "cleanup-task" in caplog.text

    def test_warning_includes_task_name(self, caplog):
        """The warning message includes the task name for easy identification."""
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(
                task_name="my-special-task",
                prompt="Run some background maintenance.",
                skills_dir=None,
            )
        assert "my-special-task" in caplog.text

    def test_no_warning_when_skill_contains_notify(self, tmp_path, caplog):
        """No warning when a skill's SKILL.md contains 'notify'."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "daily-digest"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# Daily Digest\nAfter composing, call notify() to send it.",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(
                task_name="digest",
                prompt="Run the daily-digest skill.",
                skills_dir=skills_dir,
            )
        assert "does not reference notify" not in caplog.text

    def test_warning_when_skill_does_not_contain_notify(self, tmp_path, caplog):
        """Warning emitted when skill SKILL.md exists but lacks 'notify'."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "log-archiver"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# Log Archiver\nCompress and move old log files.",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(
                task_name="archive-logs",
                prompt="Run the log-archiver skill.",
                skills_dir=skills_dir,
            )
        assert "does not reference notify" in caplog.text

    def test_warning_when_skills_dir_is_missing(self, tmp_path, caplog):
        """Missing skills dir does not raise; warning is still emitted when prompt lacks notify."""
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(
                task_name="task",
                prompt="Run some-skill to do the work.",
                skills_dir=tmp_path / "nonexistent-skills",
            )
        # Warning about missing notify should still be emitted (prompt has no 'notify')
        assert "does not reference notify" in caplog.text

    def test_no_warning_skills_dir_none_prompt_with_notify(self, caplog):
        """When skills_dir is None and prompt has notify, no warning."""
        with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
            _check_notify_reference(
                task_name="report",
                prompt="Compose summary. notify(channel='telegram', intent='send').",
                skills_dir=None,
            )
        assert "does not reference notify" not in caplog.text


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

"""DB-backed dispatch-path coverage for the ``schedule_trigger`` core tool.

Regression origin (bu-6l8vp, from PR #3052 / bu-90zzn review):
``core_tools/_scheduling.py::schedule_trigger`` had NO test coverage. When
PR #3052 reduced ``scheduler._parse_complexity_from_db_row`` to a single
``(row)`` argument, it missed this tool's call site (which still passed
``(row, name)``) — a runtime ``TypeError`` on the real manual-trigger dispatch
path that CI never caught, because nothing exercised it. The reviewer found it
by grep, not by a failing test.

These tests drive the REAL registered ``schedule_trigger`` closure end-to-end
against a migrated Postgres DB: seed a ``scheduled_tasks`` row, invoke the tool,
and assert the parsed ``Complexity`` enum reaches the dispatch call. The parse
happens OUTSIDE ``schedule_trigger``'s try/except (a signature/contract drift
there raises out of the tool rather than being swallowed into a status:error),
so any future drift on ``_parse_complexity_from_db_row``'s signature — or the
tier-normalization contract it delegates to — fails these tests instead of
silently shipping.

The retired-tier case ("high" -> ``Complexity.REASONING``) pins the #3052
convergence THROUGH this path specifically: a legacy row must dispatch at its
canonical successor tier, not collapse to workhorse.
"""

from __future__ import annotations

import shutil
import uuid
from types import SimpleNamespace

import asyncpg
import pytest

from butlers.config import ButlerType
from butlers.core.model_routing import Complexity
from butlers.core_tools._base import ToolContext
from butlers.core_tools._scheduling import register_scheduling_tools
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool with the scheduler table cleared between tests."""
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE scheduled_tasks CASCADE")
    yield p
    await p.close()


class _RecordingDaemon:
    """Fake daemon whose ``_dispatch_scheduled_task`` records its kwargs.

    Stands in for ``ButlerDaemon`` so the test can assert exactly what the
    ``schedule_trigger`` tool forwarded (trigger_source, prompt, complexity)
    without spinning up a real spawner.
    """

    def __init__(self, *, result=None, runtime_context=None):
        self.calls: list[dict] = []
        self._result = result
        self.config = SimpleNamespace(name="test-butler")
        self._runtime_context = runtime_context or SimpleNamespace(
            default_timezone="UTC",
            prompt_hooks=None,
            completion_hooks=None,
        )

    async def _dispatch_scheduled_task(self, **kwargs):
        self.calls.append(kwargs)
        return self._result

    async def _build_scheduler_runtime_context(self):
        return self._runtime_context


def _register_and_grab_schedule_trigger(pool, daemon):
    """Register the scheduling tool group and return the ``schedule_trigger`` closure.

    Mirrors the register-and-grab idiom in ``tests/core_tools/test_infra_trigger.py``:
    a fake ``_core_tool`` captures each registered handler by name. ``schedule_trigger``
    is only registered for non-STAFFER butlers, so ``butler_type`` is ``BUTLER`` here.
    """
    registered: dict[str, callable] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=None,
        butler_name="test-butler",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_scheduling_tools(ctx, SimpleNamespace(), _core_tool)
    return registered["schedule_trigger"]


# ---------------------------------------------------------------------------
# schedule_trigger — dispatch path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("reasoning", Complexity.REASONING),  # canonical tier passes through
        ("high", Complexity.REASONING),  # retired tier remaps (bu-lq7m4 convergence via THIS path)
        (None, Complexity.WORKHORSE),  # missing/null column defaults to workhorse
    ],
)
async def test_schedule_trigger_dispatches_parsed_complexity(pool, stored, expected):
    """schedule_trigger fetches the row, parses complexity via the shared
    ``_parse_complexity_from_db_row``, and hands the canonical ``Complexity`` enum
    to the daemon dispatch. A legacy ``high`` row must dispatch at ``reasoning``.

    This is the path PR #3052's signature change broke unnoticed — the parse call
    lives outside the tool's try/except, so contract drift raises here."""
    from butlers.core.scheduler import schedule_create

    daemon = _RecordingDaemon(result={"ok": True})
    schedule_trigger = _register_and_grab_schedule_trigger(pool, daemon)

    task_id = await schedule_create(pool, "trigger-task", "0 9 * * *", "run the trigger")
    # schedule_create validates complexity on write, so persist the stale/legacy
    # value (or NULL) via a direct row update — mirrors the tick() dispatch test.
    await pool.execute("UPDATE scheduled_tasks SET complexity = $2 WHERE id = $1", task_id, stored)

    result = await schedule_trigger(task_id=str(task_id))

    assert result["status"] == "triggered"
    assert result["name"] == "trigger-task"
    assert len(daemon.calls) == 1
    call = daemon.calls[0]
    assert call["prompt"] == "run the trigger"
    assert call["trigger_source"] == "schedule:trigger-task"
    assert call["complexity"] == expected

    # End-to-end DB write: the successful trigger stamps last_run_at.
    last_run_at = await pool.fetchval(
        "SELECT last_run_at FROM scheduled_tasks WHERE id = $1", task_id
    )
    assert last_run_at is not None


async def test_schedule_trigger_uses_shared_prompt_and_completion_context(pool):
    """Dashboard Run now binds and completes the same owner-local schedule tuple."""
    from butlers.core.scheduler import schedule_create

    prompt_context: dict = {}
    completion_context: dict = {}

    def prepare(*, task_name, prompt, run_at, timezone):
        prompt_context.update(
            task_name=task_name,
            prompt=prompt,
            run_at=run_at,
            timezone=timezone,
        )
        return f"{prompt}\n\nBound target."

    async def complete(*, task_name, result, run_at, timezone):
        completion_context.update(
            task_name=task_name,
            result=result,
            run_at=run_at,
            timezone=timezone,
        )

    dispatch_result = {"status": "ok"}
    daemon = _RecordingDaemon(
        result=dispatch_result,
        runtime_context=SimpleNamespace(
            default_timezone="Asia/Singapore",
            prompt_hooks={"chronicler_day_close": prepare},
            completion_hooks={"chronicler_day_close": complete},
        ),
    )
    daemon.config.name = "chronicler"
    schedule_trigger = _register_and_grab_schedule_trigger(pool, daemon)

    task_id = await schedule_create(
        pool,
        "chronicler_day_close",
        "5 1 * * *",
        "day-close prompt",
    )
    result = await schedule_trigger(task_id=str(task_id))

    assert result["status"] == "triggered"
    assert daemon.calls[0]["prompt"] == "day-close prompt\n\nBound target."
    assert prompt_context["task_name"] == "chronicler_day_close"
    assert prompt_context["timezone"] == "Asia/Singapore"
    assert completion_context["result"] is dispatch_result
    assert completion_context["run_at"] == prompt_context["run_at"]
    assert completion_context["timezone"] == prompt_context["timezone"]


async def test_schedule_trigger_job_mode_dispatches_without_complexity(pool):
    """A ``job``-mode row dispatches by job_name/job_args and forwards no complexity —
    parity with the tick() job-dispatch contract."""
    from butlers.core.scheduler import schedule_create

    daemon = _RecordingDaemon(result={"evaluated": 1})
    schedule_trigger = _register_and_grab_schedule_trigger(pool, daemon)

    task_id = await schedule_create(
        pool,
        "trigger-job",
        "0 9 * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
        job_args={"batch_size": 25},
    )

    result = await schedule_trigger(task_id=str(task_id))

    assert result["status"] == "triggered"
    assert len(daemon.calls) == 1
    call = daemon.calls[0]
    assert call["job_name"] == "eligibility_sweep"
    assert call["job_args"] == {"batch_size": 25}
    assert "complexity" not in call
    assert call["trigger_source"] == "schedule:trigger-job"


async def test_schedule_trigger_missing_row_returns_error_without_dispatch(pool):
    """A trigger for an unknown task id returns the tool's ``Schedule not found``
    error contract and never invokes dispatch."""
    daemon = _RecordingDaemon()
    schedule_trigger = _register_and_grab_schedule_trigger(pool, daemon)

    result = await schedule_trigger(task_id=str(uuid.uuid4()))

    assert result["status"] == "error"
    assert result["error"] == "Schedule not found"
    assert daemon.calls == []

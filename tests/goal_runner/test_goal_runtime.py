"""The goal runtime from admission to a settled goal (RFC 0038 M6, bu-dhloi1.206).

Real admission, broker and runner ledger on the broker suite's migrated
database; the session host is the runner suite's fake. Covers the three seams
``GoalRuntime`` joins: an admitted goal's lead starts on admission (or, when
that start never ran, on the next sweep), supervision runs on a period, and an
owner Stop fences the goal.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
from uuid import UUID, uuid4

import pytest

from butlers.core.goal_admission import GoalAdmission, SourceRef
from butlers.goal_runner.contracts import HostOutcome, Launch
from butlers.goal_runner.control_plane import GoalRuntime, GoalRuntimeSwitch, StopResult
from butlers.goal_runner.runner import RunnerConfig
from butlers.goal_runner.staging import ControlSessionHost, ModelCredentials
from tests.goal_runner.test_goal_runner import harness, until
from tests.switchboard.goal_admission_fixtures import Catalogs, Sources, limits
from tests.switchboard.goal_ledger_fixtures import configure

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_ON = GoalRuntimeSwitch(enabled=True)


async def _runtime(h, **kwargs) -> GoalRuntime:
    await configure(h.w.owner, limits())
    admission = GoalAdmission(h.w.broker.pool, Sources(), Catalogs())
    return GoalRuntime(admission, h.runner, switch=_ON, **kwargs)


def _submission() -> tuple[SourceRef, object]:
    return SourceRef("message", str(uuid4())), uuid4()


async def test_an_admitted_goal_runs_to_a_settled_outcome(broker_db_url):
    async with harness(broker_db_url) as h:
        runtime = await _runtime(h)

        async def completes(launch: Launch) -> HostOutcome:
            result = await h.call(
                launch.goal_id, launch.token, "goal__complete", {"outcome": "completed"}
            )
            assert result == {"status": "completed"}
            return HostOutcome()

        h.host.script.append(completes)
        source, attempt = _submission()

        result = await runtime.admit(source, attempt)

        assert result.result == "admitted" and result.goal_id is not None
        goal = result.goal_id
        lead = await h.launched(1)
        assert lead.goal_id == goal
        await h.runner.drain()
        await h.runner.supervise_once()  # settles the completed goal
        assert await h.goal_status(goal) == "completed"
        outcome = await h.w.owner.fetchrow(
            "SELECT final_status FROM switchboard.goal_outcomes WHERE goal_id = $1", goal
        )
        assert outcome["final_status"] == "completed"
        assert (await h.row(lead.invocation_id))["status"] == "returned"


class _Store:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def load(self, key: str) -> str | None:
        return self.values.get(key)


async def test_a_goal_runs_through_the_control_channel_with_its_staged_credential(broker_db_url):
    async with harness(broker_db_url) as h:
        runtime = await _runtime(h)
        commands: list[dict] = []
        revoked: list[UUID] = []

        async def session_host(command: dict) -> dict:
            # The session host's side: run the invocation, which completes its goal.
            if command["operation"] == "revoke":
                revoked.append(UUID(command["invocation_id"]))
                return {"status": "revoked"}
            commands.append(command)
            goal = UUID(command["goal_id"])
            await h.call(goal, command["token"], "goal__complete", {"outcome": "completed"})
            return {"status": "completed"}

        h.runner.host = ControlSessionHost(
            session_host,
            ModelCredentials(_Store({"cli-auth/openai": "sk-staged"})),
            broker_origin="https://control.test",
        )
        source, attempt = _submission()

        goal = (await runtime.admit(source, attempt)).goal_id
        await until(lambda: len(commands) == 1)
        await h.runner.drain()
        await h.runner.supervise_once()

        assert await h.goal_status(goal) == "completed"
        (command,) = commands
        assert base64.b64decode(command["credential"]) == b"sk-staged"
        assert command["endpoint"] == f"https://control.test/goals/{goal}/mcp"
        assert revoked == [UUID(command["invocation_id"])]  # its domain ended after complete


async def test_a_launch_with_no_credential_fails_its_goal_without_a_model_run(broker_db_url):
    async with harness(broker_db_url) as h:
        runtime = await _runtime(h)
        sent: list[dict] = []

        async def session_host(command: dict) -> dict:
            sent.append(command)
            return {"status": "completed"}

        h.runner.host = ControlSessionHost(
            session_host, ModelCredentials(_Store({})), broker_origin="https://control.test"
        )
        source, attempt = _submission()

        goal = (await runtime.admit(source, attempt)).goal_id
        await until(lambda: h.sink.outcomes != [])
        await h.runner.drain()

        assert sent == []
        assert h.sink.outcomes == ["suppressed"]  # never counted against the model
        assert h.sink.reasons == ["not_invoked:credential_missing"]
        assert await h.goal_status(goal) == "failed"


async def test_a_retried_admission_starts_nothing_twice(broker_db_url):
    async with harness(broker_db_url) as h:
        runtime = await _runtime(h)
        source, attempt = _submission()

        first = await runtime.admit(source, attempt)
        await h.launched(1)
        again = await runtime.admit(source, attempt)

        assert again.goal_id == first.goal_id and again.decision_id == first.decision_id
        await asyncio.sleep(0.2)
        assert len(h.host.launches) == 1
        assert (await h.budget(first.goal_id))["used_invocations"] == 1


async def test_a_goal_admitted_without_a_start_is_launched_by_the_sweep(broker_db_url):
    config = RunnerConfig(renew_every=3600, lead_max_tokens=500, resume_grace_seconds=0)
    async with harness(broker_db_url, config=config) as h:
        await configure(h.w.owner, limits())
        source, attempt = _submission()
        # Admission committed, then the process died before start_goal ran.
        admitted = await GoalAdmission(h.w.broker.pool, Sources(), Catalogs()).admit_submission(
            source, attempt
        )
        assert admitted.result == "admitted" and h.host.launches == []

        await h.runner.supervise_once()

        lead = await h.launched(1)
        assert lead.goal_id == admitted.goal_id


async def test_a_goal_admitted_just_now_is_left_to_its_own_start(broker_db_url):
    async with harness(broker_db_url) as h:  # the default 90 s grace
        await configure(h.w.owner, limits())
        source, attempt = _submission()
        await GoalAdmission(h.w.broker.pool, Sources(), Catalogs()).admit_submission(
            source, attempt
        )

        await h.runner.supervise_once()

        await asyncio.sleep(0.2)
        assert h.host.launches == []


async def test_owner_stop_fences_a_running_goal_once(broker_db_url):
    async with harness(broker_db_url) as h:
        runtime = await _runtime(h)
        source, attempt = _submission()
        goal = (await runtime.admit(source, attempt)).goal_id
        assert goal is not None
        lead = await h.launched(1)

        stopped = await runtime.stop(goal)
        again = await runtime.stop(goal)
        unknown = await runtime.stop(uuid4())

        assert stopped == StopResult("stopped", 1)
        assert again == StopResult("not_running", 0)
        assert unknown == StopResult("not_running", 0)
        assert await h.goal_status(goal) == "stopped"
        assert lead.invocation_id in h.host.terminated
        await until(lambda: h.runner._tasks == set())


async def test_supervision_runs_on_its_period_and_survives_a_failed_sweep(broker_db_url):
    async with harness(broker_db_url) as h:
        runtime = await _runtime(h, supervise_every=0.01)
        sweeps: list[int] = []
        real = h.runner.supervise_once

        async def sweep() -> None:
            sweeps.append(1)
            if len(sweeps) == 1:
                raise ConnectionError("database blip")
            await real()

        h.runner.supervise_once = sweep  # type: ignore[method-assign]
        runtime.start()
        runtime.start()  # idempotent
        try:
            await until(lambda: len(sweeps) >= 3)
        finally:
            await runtime.close()
        count = len(sweeps)
        await asyncio.sleep(0.05)
        assert len(sweeps) == count  # closed: no further sweeps


async def test_a_switched_off_runtime_admits_and_supervises_nothing(broker_db_url):
    async with harness(broker_db_url) as h:
        await configure(h.w.owner, limits())
        runtime = GoalRuntime(GoalAdmission(h.w.broker.pool, Sources(), Catalogs()), h.runner)
        source, attempt = _submission()
        with pytest.raises(RuntimeError):
            await runtime.admit(source, attempt)
        with pytest.raises(RuntimeError):
            runtime.start()
        assert h.host.launches == []

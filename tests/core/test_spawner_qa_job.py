"""bu-ejow5p.11: an executor session with a QaJobLaunch runs through invoke_in_job."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.failover_classifier import FailoverContext, classify_failover_eligibility
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import JobOutcome, QaJobLaunch
from butlers.core.spawner import Spawner
from tests.core.test_core_spawner import _FAKE_CATALOG_ID, MockAdapter, _make_config

RESOLUTION = (DEFAULT_RUNTIME_TYPE, "test-model", [], _FAKE_CATALOG_ID, 1800, "workhorse")


class JobAdapter(MockAdapter):
    def __init__(self) -> None:
        super().__init__(result_text="ok", capture=True)
        self.job_calls: list[dict[str, Any]] = []

    async def invoke_in_job(self, prompt, system_prompt, mcp_servers, launch, model=None, **kw):
        self.job_calls.append({"prompt": prompt, "launch": launch, "model": model})
        return "job-ok", [], None


class _Runner:
    async def run(self, manifest, *, timeout_s):
        return JobOutcome("succeeded", 0)


def _launch(tmp_path: Path) -> QaJobLaunch:
    return QaJobLaunch(
        attempt_id="a1",
        staging_dir=tmp_path,
        workspace_mount="/workspace",
        workspace_uid=10001,
        deadline_s=600,
        runner=_Runner(),
        build_manifest=lambda command: {},
        model_proxy_url="http://proxy.invalid",
    )


async def _trigger(tmp_path: Path, adapter: MockAdapter, trigger_source: str, launch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "CLAUDE.md").write_text("persona")
    with (
        patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as create,
        patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=RESOLUTION,
        ),
        patch("butlers.core.spawner.fetch_memory_context", new_callable=AsyncMock, return_value=""),
        patch(
            "butlers.core.spawner.fetch_situational_context_preamble",
            new_callable=AsyncMock,
            return_value="",
        ),
        patch(
            "butlers.core.spawner.fetch_system_prompt_override",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
        spawner = Spawner(
            config=_make_config(modules={}),
            config_dir=config_dir,
            pool=AsyncMock(),
            runtime=adapter,
        )
        return await spawner.trigger(
            "investigate", trigger_source, env_override={"PATH": "/usr/bin"}, qa_job_launch=launch
        )


async def test_a_qa_session_with_a_launch_runs_in_the_job_not_the_daemon(tmp_path: Path) -> None:
    adapter = JobAdapter()
    result = await _trigger(tmp_path, adapter, "qa", _launch(tmp_path))
    assert result.success and result.output == "job-ok"
    assert len(adapter.job_calls) == 1 and adapter.calls == []
    assert adapter.job_calls[0]["model"] == "test-model"


async def test_a_non_executor_trigger_with_a_launch_raises_instead_of_running_in_the_daemon(
    tmp_path: Path,
) -> None:
    adapter = JobAdapter()
    with pytest.raises(ValueError, match="executor trigger"):
        await _trigger(tmp_path, adapter, "tick", _launch(tmp_path))
    assert adapter.job_calls == [] and adapter.calls == []


async def test_an_adapter_without_job_support_refuses_and_never_runs_in_the_daemon(
    tmp_path: Path,
) -> None:
    adapter = MockAdapter(result_text="ok", capture=True)
    result = await _trigger(tmp_path, adapter, "qa", _launch(tmp_path))
    assert not result.success
    assert "launch_refused:runtime_unsupported" in (result.error or "")
    assert adapter.calls == []


def test_no_failure_of_a_job_attempt_is_failover_eligible() -> None:
    """A missing CLI binary is normally eligible; for a Job nothing is."""
    exc = FileNotFoundError("claude")
    normal = classify_failover_eligibility(FailoverContext(exception=exc))
    isolated = classify_failover_eligibility(FailoverContext(exception=exc, job_isolated=True))
    assert isolated.eligible is False and isolated.reason.startswith("job_attempt_not_retryable")
    assert normal.eligible is True


@pytest.mark.parametrize("category", ["model_route_unavailable", "forbidden"])
def test_refusal_message_is_the_category_only(category: str) -> None:
    from butlers.core.runtimes.base import JobLaunchRefused

    assert str(JobLaunchRefused(category)) == f"launch_refused:{category}"

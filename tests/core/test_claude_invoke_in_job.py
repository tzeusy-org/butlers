"""bu-ejow5p.11: the Claude adapter's Job seam stages files, keeps prompts out of the manifest,
reads outputs only after a terminal pod, and refuses before any effect when it cannot run."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from butlers.core.runtimes.base import JobLaunchRefused, JobOutcome, QaJobLaunch
from butlers.core.runtimes.claude_code import ClaudeCodeAdapter

pytestmark = pytest.mark.unit

_RESULT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "investigated",
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }
)
_SECRET_PROMPT = "PROMPT-BODY-MARKER"
_SECRET_SYSTEM = "SYSTEM-BODY-MARKER"


class FakeRunner:
    def __init__(self, staging: Path, outcome: JobOutcome, stdout: str = "", stderr: str = ""):
        self.staging, self.outcome, self.stdout, self.stderr = staging, outcome, stdout, stderr
        self.manifests: list[dict[str, Any]] = []

    async def run(self, manifest: dict[str, Any], *, timeout_s: float) -> JobOutcome:
        self.manifests.append(manifest)
        self.staged = {
            f.name: f.read_text() for f in (self.staging / ".in").iterdir() if f.is_file()
        }
        (self.staging / ".out" / "stdout.jsonl").write_text(self.stdout)
        (self.staging / ".out" / "stderr.txt").write_text(self.stderr)
        return self.outcome


def _launch(tmp_path: Path, runner, proxy: str | None = "http://proxy.invalid:3110"):
    def build(command):
        return {
            "kind": "Job",
            "spec": {"template": {"spec": {"containers": [{"command": command}]}}},
        }

    return QaJobLaunch(
        attempt_id="a1",
        staging_dir=tmp_path,
        workspace_mount="/workspace",
        workspace_uid=os.geteuid(),
        deadline_s=600,
        runner=runner,
        build_manifest=build,
        model_proxy_url=proxy,
    )


def _adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


async def test_success_stages_inputs_and_returns_the_parsed_result(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path, JobOutcome("succeeded", 0), stdout=_RESULT + "\n")
    text, _tools, usage = await _adapter().invoke_in_job(
        _SECRET_PROMPT, _SECRET_SYSTEM, {}, _launch(tmp_path, runner), model="claude-x"
    )
    assert text == "investigated" and usage and usage["output_tokens"] == 4
    assert runner.staged["prompt.txt"] == _SECRET_PROMPT
    assert runner.staged["system.txt"] == _SECRET_SYSTEM
    assert json.loads(runner.staged["mcp.json"]) == {"mcpServers": {}}


async def test_no_prompt_text_or_credential_enters_the_manifest(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path, JobOutcome("succeeded", 0), stdout=_RESULT + "\n")
    await _adapter().invoke_in_job(
        _SECRET_PROMPT, _SECRET_SYSTEM, {}, _launch(tmp_path, runner), model="claude-x"
    )
    text = json.dumps(runner.manifests)
    assert _SECRET_PROMPT not in text and _SECRET_SYSTEM not in text
    assert "ANTHROPIC_API_KEY" not in text
    command = runner.manifests[0]["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command[:2] == ["/bin/sh", "-c"]
    assert (
        "/workspace/.in/prompt.txt" in command[2] and "/workspace/.out/stdout.jsonl" in command[2]
    )
    assert "--model claude-x" in command[2]


async def test_no_model_route_refuses_before_any_effect(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path, JobOutcome("succeeded", 0))
    with pytest.raises(JobLaunchRefused) as raised:
        await _adapter().invoke_in_job("p", "s", {}, _launch(tmp_path, runner, proxy=None))
    assert raised.value.category == "model_route_unavailable"
    assert runner.manifests == [] and not (tmp_path / ".in").exists()


async def test_deadline_exceeded_is_a_pre_tool_timeout(tmp_path: Path) -> None:
    adapter = _adapter()
    runner = FakeRunner(tmp_path, JobOutcome("deadline_exceeded", None))
    with pytest.raises(TimeoutError):
        await adapter.invoke_in_job("p", "s", {}, _launch(tmp_path, runner))
    assert adapter.last_process_info["is_pre_tool_call"] is True


async def test_a_pod_not_shown_terminal_reads_nothing_from_the_exchange(tmp_path: Path) -> None:
    adapter = _adapter()
    runner = FakeRunner(
        tmp_path,
        JobOutcome("failed", None, "pod_not_terminal", pod_terminal=False),
        stdout=_RESULT,
    )
    with pytest.raises(RuntimeError, match="terminal"):
        await adapter.invoke_in_job("p", "s", {}, _launch(tmp_path, runner))
    assert "stdout" not in json.dumps(adapter.last_process_info)


async def test_nonzero_exit_without_a_result_is_a_pre_tool_runtime_error(tmp_path: Path) -> None:
    adapter = _adapter()
    runner = FakeRunner(tmp_path, JobOutcome("failed", 2), stderr="boom")
    with pytest.raises(RuntimeError, match="exited with code 2"):
        await adapter.invoke_in_job("p", "s", {}, _launch(tmp_path, runner))
    assert adapter.last_process_info["is_pre_tool_call"] is True


async def test_a_pod_planted_symlink_for_an_output_reads_as_missing(tmp_path: Path) -> None:
    secret = tmp_path.parent / "daemon-secret.txt"
    secret.write_text("DAEMON-ONLY")

    class Planting(FakeRunner):
        async def run(self, manifest, *, timeout_s):
            self.manifests.append(manifest)
            (self.staging / ".out" / "stdout.jsonl").symlink_to(secret)
            (self.staging / ".out" / "stderr.txt").symlink_to(secret)
            return self.outcome

    adapter = _adapter()
    runner = Planting(tmp_path, JobOutcome("failed", 1))
    with pytest.raises(RuntimeError) as raised:
        await adapter.invoke_in_job("p", "s", {}, _launch(tmp_path, runner))
    assert "DAEMON-ONLY" not in str(raised.value)
    assert "DAEMON-ONLY" not in json.dumps(adapter.last_process_info)


@pytest.mark.parametrize("runtime_type", ["codex", "gemini", "opencode", "api"])
async def test_every_other_adapter_refuses_before_any_effect(runtime_type, tmp_path: Path) -> None:
    from butlers.core.runtimes.base import RuntimeAdapter, get_adapter

    try:
        cls = get_adapter(runtime_type)
    except Exception:  # noqa: BLE001 - not registered in this build
        pytest.skip(f"{runtime_type} adapter not registered")
    assert cls.invoke_in_job is RuntimeAdapter.invoke_in_job
    runner = FakeRunner(tmp_path, JobOutcome("succeeded", 0))
    adapter = cls.__new__(cls)
    with pytest.raises(JobLaunchRefused) as raised:
        await adapter.invoke_in_job("p", "s", {}, _launch(tmp_path, runner))
    assert raised.value.category == "runtime_unsupported" and runner.manifests == []


async def test_a_planted_fifo_cannot_hang_the_read(tmp_path: Path) -> None:
    class Fifo(FakeRunner):
        async def run(self, manifest, *, timeout_s):
            self.manifests.append(manifest)
            os.mkfifo(self.staging / ".out" / "stdout.jsonl")
            (self.staging / ".out" / "stderr.txt").write_text("")
            return self.outcome

    adapter = _adapter()
    with pytest.raises(RuntimeError, match="exited with code 1"):
        await asyncio.wait_for(
            adapter.invoke_in_job(
                "p", "s", {}, _launch(tmp_path, Fifo(tmp_path, JobOutcome("failed", 1)))
            ),
            timeout=10,
        )


async def test_a_hard_link_to_a_daemon_file_reads_as_missing(tmp_path: Path) -> None:
    daemon_file = tmp_path.parent / "daemon-hardlink-target.txt"
    daemon_file.write_text("DAEMON-ONLY")

    class Linking(FakeRunner):
        async def run(self, manifest, *, timeout_s):
            self.manifests.append(manifest)
            os.link(daemon_file, self.staging / ".out" / "stdout.jsonl")
            (self.staging / ".out" / "stderr.txt").write_text("")
            return self.outcome

    adapter = _adapter()
    with pytest.raises(RuntimeError) as raised:
        await adapter.invoke_in_job(
            "p", "s", {}, _launch(tmp_path, Linking(tmp_path, JobOutcome("failed", 1)))
        )
    assert "DAEMON-ONLY" not in str(raised.value)
    assert "DAEMON-ONLY" not in json.dumps(adapter.last_process_info)


async def test_staged_inputs_are_removed_after_the_run_even_on_failure(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path, JobOutcome("failed", 1))
    with pytest.raises(RuntimeError):
        await _adapter().invoke_in_job("p", "s", {}, _launch(tmp_path, runner))
    assert not (tmp_path / ".in").exists()
    assert (tmp_path / ".out").is_dir()


@pytest.mark.parametrize("name", [".in", ".out"])
async def test_preexisting_non_directory_or_link_is_refused_before_writing(
    tmp_path: Path, name: str
) -> None:
    target = tmp_path.parent / "elsewhere"
    target.mkdir(exist_ok=True)
    (tmp_path / name).symlink_to(target)
    runner = FakeRunner(tmp_path, JobOutcome("succeeded", 0))
    with pytest.raises(JobLaunchRefused) as raised:
        await _adapter().invoke_in_job("p", "s", {}, _launch(tmp_path, runner))
    assert raised.value.category == "invalid"
    assert runner.manifests == [] and list(target.iterdir()) == []


async def test_the_prompt_goes_on_stdin_not_argv(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path, JobOutcome("succeeded", 0), stdout=_RESULT + "\n")
    await _adapter().invoke_in_job("x" * 500_000, "s", {}, _launch(tmp_path, runner))
    script = runner.manifests[0]["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert (
        "</workspace/.in/prompt.txt" in script and "$(cat /workspace/.in/prompt.txt" not in script
    )
    assert len(script) < 2000

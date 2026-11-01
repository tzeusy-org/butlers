"""Claude Code served-identity evidence through the real adapter.

REQ-model-catalog-008; REQ-model-catalog-010; REQ-core-spawner-018;
REQ-build-reproducibility-001.

``test_recorded_claude_served_identity_contract`` replays genuine CLI recordings (see
``tests/fixtures/claude_served_identity/README.md`` for provenance). The conformance test below
it uses synthetic, pinned-schema events for malformed, privacy and overflow controls; those are
never credited as recordings or as SQL persistence proof.
"""

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes.claude_code import ClaudeCodeAdapter
from butlers.core.runtimes.served_identity import (
    TerminalResultError,
    cost,
    expected_toolchain,
    model_id,
    stream_evidence,
    token,
)

pytestmark = pytest.mark.unit

_RECORDINGS = Path(__file__).parents[2] / "fixtures" / "claude_served_identity"


async def test_recorded_claude_served_identity_contract():
    """Genuine CLI 2.1.316 recordings: two models, error_max_turns, no modelUsage."""

    adapter_info: dict = {}

    async def replay(name: str, returncode: int, extra: bytes = b""):
        adapter = ClaudeCodeAdapter(claude_binary="/recorded/claude")
        process = AsyncMock(returncode=returncode, pid=7)
        stdout = extra + (_RECORDINGS / f"{name}.jsonl").read_bytes()
        process.communicate = AsyncMock(return_value=(stdout, b""))
        outcome: object
        with patch(
            "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec", return_value=process
        ):
            try:
                outcome = await adapter.invoke(
                    prompt="recorded", system_prompt="", mcp_servers={}, env={}, model="sonnet"
                )
            except Exception as exc:  # noqa: BLE001 -- each subcase asserts the exception type
                outcome = exc
        adapter_info.clear()
        adapter_info.update(adapter.last_process_info)
        return outcome, adapter.last_process_info["served"]

    # Subcase: two models served one invocation (Sonnet main loop, Haiku subagent).
    outcome, served = await replay("two_model_success", 0)
    assert isinstance(outcome, tuple)
    _, _, usage = outcome
    assert usage["input_tokens"] == 4 and usage["output_tokens"] == 135
    execution = served["executions"][0]
    assert served["observation_state"] == "observed"
    assert execution["cli_version_reported"] == "2.1.316"
    assert execution["configured_model_id"] == "claude-sonnet-5-5"
    assert execution["completion_state"] == "success"
    rows = {row["model_id"]: row for row in execution["model_usage"]}
    assert set(rows) == {"claude-haiku-5-5", "claude-sonnet-5-5"}
    assert (
        rows["claude-sonnet-5-5"]["input_tokens"],
        rows["claude-sonnet-5-5"]["output_tokens"],
    ) == (
        4,
        135,
    )
    assert rows["claude-haiku-5-5"]["cache_creation_input_tokens"] == 18652
    assert all(row["identity_authority"] == "cli_usage_breakdown" for row in rows.values())
    # CLI usage keys are evidence, not provider ground truth for "served".
    assert execution["served_models"] == []
    assert execution["reported_cost"]["value"] == "0.067815100"
    assert "recorded response removed" not in json.dumps(served)

    # Subcase: error_max_turns at the recorded exit code (CLI 2.1.316 exits 1). The parseable
    # result envelope keeps the attempt's usage, and its two turns prove the Bash tool ran,
    # so the failure is not pre-tool-call. The exit detail stays in the classified message.
    outcome, served = await replay("error_max_turns", 1)
    assert isinstance(outcome, TerminalResultError)
    assert outcome.usage == {
        "input_tokens": 2,
        "output_tokens": 74,
        "cache_read_input_tokens": 11616,
        "cache_creation_input_tokens": 15473,
    }
    assert str(outcome).startswith("Claude CLI exited with code 1: ")
    assert outcome.served["executions"][0]["error"]["code"] == "max_turns"
    execution = served["executions"][0]
    assert execution["error"] == {**execution["error"], "is_error": True, "code": "max_turns"}
    assert execution["completion_state"] == "error"
    assert [row["model_id"] for row in execution["model_usage"]] == ["claude-haiku-5-5"]
    assert adapter_info["is_pre_tool_call"] is False and adapter_info["exit_code"] == 1

    # Subcase: the same terminal result at exit 0 (earlier CLIs) is still a failure,
    # and it carries the invocation's usage for the attempt row.
    outcome, served = await replay("error_max_turns", 0)
    assert isinstance(outcome, TerminalResultError)
    assert outcome.usage["output_tokens"] == 74
    assert served["executions"][0]["error"]["code"] == "max_turns"

    # Subcase: no modelUsage (unknown model) leaves served identity empty, never the request.
    # One turn and no tool_use: the failure stays pre-tool-call for failover.
    outcome, served = await replay("no_model_usage", 1)
    assert isinstance(outcome, TerminalResultError)
    assert adapter_info["is_pre_tool_call"] is True
    execution = served["executions"][0]
    assert execution["model_usage"] == [] and execution["served_models"] == []
    assert execution["error"]["is_error"] is True

    # Synthetic CLI-shaped tool_use (nested under ``message``) also proves tools ran, even
    # when the recording's single turn says nothing more.
    tool_use = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
    }
    outcome, _ = await replay("no_model_usage", 1, json.dumps(tool_use).encode() + b"\n")
    assert isinstance(outcome, TerminalResultError)
    assert adapter_info["is_pre_tool_call"] is False

    # A non-zero exit with no result envelope keeps the plain pre-tool-call RuntimeError.
    adapter = ClaudeCodeAdapter(claude_binary="/recorded/claude")
    process = AsyncMock(returncode=1, pid=7)
    process.communicate = AsyncMock(return_value=(b"", b"boom"))
    with (
        patch(
            "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec", return_value=process
        ),
        pytest.raises(RuntimeError, match="exited with code 1: boom") as caught,
    ):
        await adapter.invoke(prompt="x", system_prompt="", mcp_servers={}, env={})
    assert not isinstance(caught.value, TerminalResultError)
    assert adapter.last_process_info["is_pre_tool_call"] is True


async def test_claude_served_identity_conformance(tmp_path):
    """Real adapter/controlled process; metadata positives and causal failures."""
    adapter = ClaudeCodeAdapter(claude_binary="/synthetic/claude")
    process = AsyncMock(returncode=0, pid=42)
    models = {
        "claude-opus-4-6": {
            "inputTokens": 5,
            "outputTokens": 3,
            "cacheReadInputTokens": 100,
            "cacheCreationInputTokens": 20,
            "costUSD": 0.001,
        },
        "claude-sonnet-4-6": {
            "inputTokens": 2,
            "outputTokens": 1,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
            "costUSD": 0,
        },
    }
    init = {
        "type": "system",
        "subtype": "init",
        "model": "claude-opus-4-6",
        "claude_code_version": "2.1.179",
    }
    final = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "modelUsage": models,
        "total_cost_usd": 0.001,
        "usage": {
            "input_tokens": 5,
            "output_tokens": 3,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
        },
        "result": "synthetic response",
    }

    async def invoke(events):
        process.communicate = AsyncMock(
            return_value=(
                "\n".join(json.dumps(event) for event in events).encode(),
                b"",
            )
        )
        with patch(
            "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec", return_value=process
        ):
            return await adapter.invoke(
                prompt="synthetic", system_prompt="", mcp_servers={}, env={}
            )

    _, tools, usage = await invoke([init, final])
    assert tools == [] and usage["input_tokens"] == 5
    receipt = adapter.last_process_info["served"]
    execution = receipt["executions"][0]
    assert execution["cli_version_reported"] == "2.1.179"
    assert [row["model_id"] for row in execution["model_usage"]] == sorted(models)
    assert execution["model_usage"][0]["cache_read_input_tokens"] == 100
    assert execution["model_usage"][0]["cache_creation_input_tokens"] == 20
    assert execution["served_models"] == []  # CLI keys are not ground truth.
    assert execution["reported_cost"]["value"] == "0.001000000"
    assert execution["reported_cost"]["scope"] == "unknown"

    failed = {**final, "is_error": True, "subtype": "error_max_turns"}
    with pytest.raises(TerminalResultError) as caught:
        await invoke([init, failed])
    assert caught.value.usage == usage
    assert caught.value.served["executions"][0]["error"]["code"] == "max_turns"
    assert adapter.last_process_info["served"]["executions"][0]["completion_state"] == "error"

    await invoke([{"type": "result", "result": "ordinary"}])
    cleared = adapter.last_process_info["served"]["executions"][0]
    assert cleared["model_usage"] == [] and cleared["cli_version_reported"] is None
    assert cleared["reported_cost"]["value"] is None
    # Content/denial objects cannot contribute identity or leak into the projection.
    private = "synthetic-private-sentinel@example.invalid"
    unsafe = {
        **final,
        "modelUsage": {private: models["claude-opus-4-6"]},
        "permission_denials": [{"input": private}],
        "result": private,
    }
    await invoke([{"type": "assistant", "content": json.dumps(final)}, unsafe])
    assert private not in json.dumps(adapter.last_process_info["served"])
    assert (
        adapter.last_process_info["served"]["executions"][0]["error"]["permission_denial_count"]
        == 1
    )
    duplicate = stream_evidence("claude", "\n".join(json.dumps(final) for _ in range(2)))
    assert len(duplicate["executions"]) == 1 and duplicate["finding_codes"] == []
    conflicting = stream_evidence("claude", "\n".join(map(json.dumps, [final, failed])))
    assert conflicting["observation_state"] == "partial"
    assert conflicting["executions"][0]["completion_state"] == "error"
    assert token(True) is None and token(-1) is None and token(2**63) is None
    assert token(0) == 0 and token(2**63 - 1) == 2**63 - 1
    assert cost(Decimal("NaN")) is None and cost("0.1") is None and cost(True) is None
    assert cost(0) == "0.000000000"
    assert model_id("claude-opus-4-6[1m]") == "claude-opus-4-6[1m]"
    assert model_id("https://example.invalid/model") is None
    # Canonical Codex input is uncached; an absent cache counter is not zero.
    missing = object()
    for cached, expected_input in (
        (missing, None),
        (None, None),
        (True, None),
        (-1, None),
        (2**63, None),
        ("0", None),
        (101, None),
        (0, 100),
        (95, 5),
    ):
        raw_usage = {"input_tokens": 100, "output_tokens": 3}
        if cached is not missing:
            raw_usage["cached_input_tokens"] = cached
        observed = stream_evidence(
            "codex", json.dumps({"type": "turn.completed", "usage": raw_usage})
        )
        normalized = observed["executions"][0]["aggregate_usage"]
        assert normalized["output_tokens"] == 3
        assert normalized["cache_read_input_tokens"] == token(raw_usage.get("cached_input_tokens"))
        assert normalized["input_tokens"] == expected_input
        assert normalized["cache_creation_input_tokens"] is None
    written = stream_evidence(
        "codex",
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 3,
                    "cached_input_tokens": 95,
                    "cache_write_input_tokens": 2,
                },
            }
        ),
    )["executions"][0]
    assert written["aggregate_usage"]["input_tokens"] is None
    assert written["aggregate_usage"]["cache_creation_input_tokens"] == 2
    assert written["cache_overlap_state"] == "unknown"
    # JSON permits escaped lone surrogates; diagnostic identity must not turn
    # the actual adapter's ordinary success into an encoding exception.
    for invalid_model in ("\ud800", "\udfff", "claude-opus-4-6\ud800"):
        assert model_id(invalid_model) is None
        response, actual_tools, actual_usage = await invoke(
            [
                {**init, "model": invalid_model},
                {
                    **final,
                    "modelUsage": {
                        invalid_model: models["claude-opus-4-6"],
                        "claude-sonnet-4-6": models["claude-sonnet-4-6"],
                    },
                },
            ]
        )
        assert response == "synthetic response" and actual_tools == tools
        assert actual_usage == usage
        safe_execution = adapter.last_process_info["served"]["executions"][0]
        assert [row["model_id"] for row in safe_execution["reported_models"]] == [
            "claude-sonnet-4-6"
        ]
        assert [row["model_id"] for row in safe_execution["model_usage"]] == ["claude-sonnet-4-6"]
        assert safe_execution["malformed_identity"] is True
    # Fixed-image reader refuses nonregular, duplicate and malformed manifests.
    manifest = tmp_path / "manifest"
    manifest.write_text("├── @anthropic-ai/claude-code@2.1.179\n└── @openai/codex@0.159.2\n")
    import os

    real_open = os.open
    with patch(
        "butlers.core.runtimes.served_identity.os.open",
        side_effect=lambda _path, flags: real_open(manifest, flags),
    ):
        assert expected_toolchain()["versions"]["@openai/codex"] == "0.159.2"
        manifest.write_text("@openai/codex@0.159.2\n@openai/codex@0.159.2\n")
        assert expected_toolchain()["state"] == "unknown"
    # Cancellation clears prior evidence without generating provider output.
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError)
    with (
        patch(
            "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec", return_value=process
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await adapter.invoke(prompt="synthetic", system_prompt="", mcp_servers={}, env={})
    cancelled = adapter.last_process_info["served"]["executions"][0]
    assert cancelled["completion_state"] == "cancelled" and cancelled["model_usage"] == []

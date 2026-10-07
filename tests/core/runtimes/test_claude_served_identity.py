"""Synthetic pinned-schema conformance; this file is NOT a provider recording.

REQ-model-catalog-005; REQ-model-catalog-007; REQ-core-spawner-010;
REQ-build-reproducibility-001.
The original genuine three-recording obligation is still unavailable. These
controls must never be credited as that corpus or as SQL persistence proof.
"""

import asyncio
import json
from decimal import Decimal
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

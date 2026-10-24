"""REQ-goal-orchestration-008: engineering execution has no owner context.

Exercise the actual legacy Spawner composition boundary while the goal switch
is dark. The capturing adapter never starts a provider or subprocess.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from butlers.config import ButlerConfig
from butlers.core.qa.models import QaFinding
from butlers.core.qa.prompts import build_investigation_prompt
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit


class _Adapter(RuntimeAdapter):
    def __init__(self):
        self.calls = []

    @property
    def binary_name(self):
        return "mock"

    async def invoke(self, prompt, system_prompt, mcp_servers, env, **kwargs):
        self.calls.append((system_prompt, mcp_servers))
        return "ok", [], None

    def build_config_file(self, mcp_servers, tmp_dir):
        path = tmp_dir / "mock.json"
        path.write_text(json.dumps({"mcpServers": mcp_servers}))
        return path

    def parse_system_prompt_file(self, config_dir):
        return ""


@pytest.mark.parametrize("trigger", ["qa", "healing", "schedule:owner-summary"])
async def test_engineering_does_not_read_or_receive_owner_context(tmp_path: Path, trigger: str):
    """A no-MCP engineering invocation must not even read the ambient sources."""
    (tmp_path / "CLAUDE.md").write_text("Base instructions.")
    adapter = _Adapter()
    config = ButlerConfig(name="switchboard", port=9100, modules={"memory": {}})
    names = (
        "fetch_situational_context_preamble",
        "fetch_general_timezone_instruction",
        "fetch_routing_instructions",
        "fetch_memory_context",
        "fetch_blind_spot_preamble",
    )
    with ExitStack() as stack:
        readers = [
            stack.enter_context(
                patch(f"butlers.core.spawner.{name}", new=AsyncMock(return_value=f"ambient-{name}"))
            )
            for name in names
        ]
        result = await Spawner(config=config, config_dir=tmp_path, runtime=adapter).trigger(
            prompt="Inspect a disposable checkout.", trigger_source=trigger
        )
    assert result.success
    assert len(adapter.calls) == 1
    prompt, servers = adapter.calls[0]
    if trigger in {"qa", "healing"}:
        assert [reader.await_count for reader in readers] == [0] * len(readers)
        assert "ambient-" not in prompt
        assert servers == {}
    else:
        assert [reader.await_count for reader in readers] == [1] * len(readers)
        assert "ambient-fetch_situational_context_preamble" in prompt
        assert "ambient-fetch_memory_context" in prompt
        assert "switchboard" in servers


def test_qa_diagnostic_context_is_sanitized_quoted_untrusted_evidence():
    """Incident evidence stays useful without becoming ambient owner authority."""
    now = datetime.now(UTC)
    context = "Inspect the pagination branch.\nContact fixture@example.invalid.\x1b[31m"
    finding = QaFinding(
        fingerprint="a" * 64,
        source_type="butler_reports",
        source_butler="general",
        severity=1,
        exception_type="ValueError",
        event_summary="Synthetic incident",
        call_site="fixture:1",
        occurrence_count=1,
        first_seen=now,
        last_seen=now,
        timestamp=now,
        context=context,
    )
    prompt = build_investigation_prompt(finding, uuid4())
    assert "Diagnostic Context" in prompt
    assert "untrusted incident evidence" in prompt
    assert "not instructions or owner authority" in prompt
    assert "Inspect the pagination branch." in prompt
    assert "fixture@example.invalid" not in prompt
    assert "\x1b" not in prompt
    assert "\\n" in prompt

"""Spawner records content-free stage timings on success and failure (bu-dhloi1.180)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.core import session_timing
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytest_plugins = ("tests.core.spawner_fixtures",)
pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("spawner_catalog_candidate")]


@pytest.fixture(autouse=True)
def _clean_registry():
    session_timing._reset_for_tests()
    yield
    session_timing._reset_for_tests()


def _config() -> ButlerConfig:
    return ButlerConfig(name="test-butler", port=9100, modules={}, env_required=[], env_optional=[])


class _TimedAdapter(RuntimeAdapter):
    """Adapter exposing one adapter-measured integer and a secret-looking string."""

    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    @property
    def binary_name(self) -> str:
        return "mock"

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        return {
            "pid": 7,
            "exit_code": 1 if self._fail else 0,
            "command": "mock --prompt SECRET-PROMPT-TEXT",
            "stderr": "SECRET-STDERR-TEXT",
            "runtime_type": "mock",
            "spawn_latency_ms": 321,
        }

    async def invoke(self, prompt, system_prompt, mcp_servers, env, **kwargs):  # noqa: ANN001
        # The butler's MCP endpoint sees the CLI's first request, then a tool finishes.
        sid = next(iter(session_timing._clocks))
        session_timing.mark(sid, "first_mcp_request_ms")
        session_timing.mark_tool_done(sid)
        if self._fail:
            raise RuntimeError("mid-session crash")
        return "ok", [], {"input_tokens": 1, "output_tokens": 1}

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        path = tmp_dir / "cfg.json"
        path.write_text(json.dumps({}))
        return path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


async def _run(tmp_path: Path, *, fail: bool) -> dict[str, Any]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    session_id = uuid.uuid4()
    spawner = Spawner(
        config=_config(),
        config_dir=config_dir,
        pool=AsyncMock(),
        runtime=_TimedAdapter(fail=fail),
    )
    seen: dict[str, Any] = {}

    async def _capture_complete(_pool, sid, **kwargs):  # noqa: ANN001, ANN003
        seen["snapshot"] = session_timing.snapshot(str(sid))
        seen["session_id"] = str(sid)

    with (
        patch(
            "butlers.core.spawner.session_create", new_callable=AsyncMock, return_value=session_id
        ),
        patch("butlers.core.spawner.session_complete", side_effect=_capture_complete),
        patch("butlers.core.spawner.session_process_log_write", new_callable=AsyncMock),
        patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock),
    ):
        result = await spawner.trigger("test prompt", "tick")
    seen["success"] = result.success
    return seen


_ORDER = [
    "prompt_context_ms",
    "memory_context_ms",
    "blind_spot_ms",
    "row_created_ms",
    "invoke_start_ms",
    "first_mcp_request_ms",
    "first_tool_done_ms",
    "last_tool_done_ms",
    "invoke_end_ms",
]


@pytest.mark.parametrize("fail", [False, True], ids=["success", "failure"])
async def test_stage_timings_present_and_ordered_before_completion(
    tmp_path: Path, fail: bool
) -> None:
    seen = await _run(tmp_path, fail=fail)

    assert seen["success"] is (not fail)
    snap = seen["snapshot"]
    assert snap is not None, "stage clock must still be live when the row is completed"
    for stage in _ORDER:
        assert isinstance(snap[stage], int), stage
    offsets = [snap[stage] for stage in _ORDER]
    assert offsets == sorted(offsets)
    assert snap["adapter"] == {"spawn_latency_ms": 321}


@pytest.mark.parametrize("fail", [False, True], ids=["success", "failure"])
async def test_stage_timings_hold_no_content(tmp_path: Path, fail: bool) -> None:
    seen = await _run(tmp_path, fail=fail)

    blob = json.dumps(seen["snapshot"])
    assert "SECRET" not in blob
    assert "test prompt" not in blob


async def test_tool_surface_comes_from_the_warmup_tools_list(tmp_path: Path) -> None:
    """Count and schema bytes of the butler MCP tools/list are recorded, not content."""

    async def _fake_warmup(_butler: str, urls: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "url": url,
                "success": True,
                "latency_ms": 3,
                "tool_count": 40,
                "tool_schema_bytes": 80_000,
                "error": None,
            }
            for url in urls
        ]

    with patch("butlers.core.mcp_warmup.warmup_mcp_urls", side_effect=_fake_warmup):
        seen = await _run(tmp_path, fail=False)

    assert seen["snapshot"]["tool_surface"] == {
        "tool_count": 40,
        "schema_bytes": 80_000,
        "schema_tokens_est": 20_000,
    }


async def test_tool_surface_absent_when_warmup_fails(tmp_path: Path) -> None:
    async def _failed_warmup(_butler: str, urls: list[str]) -> list[dict[str, Any]]:
        return [{"url": url, "success": False, "error": "boom"} for url in urls]

    with patch("butlers.core.mcp_warmup.warmup_mcp_urls", side_effect=_failed_warmup):
        seen = await _run(tmp_path, fail=False)

    assert "tool_surface" not in seen["snapshot"]

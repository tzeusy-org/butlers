"""An empty env mapping never makes Gemini inherit the daemon environment. [bu-9i5uwq]

Only an explicit ``None`` means "inherit". Sentinel values are synthetic and never printed.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import GeminiAdapter

pytestmark = pytest.mark.unit

_GEMINI_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"
_AMBIENT = {"DAEMON_AMBIENT_SENTINEL": "ambient-synthetic", "PATH": "/synthetic/ambient/bin"}


def _process(stdout: bytes = b"{}") -> AsyncMock:
    process = AsyncMock(returncode=0)
    process.communicate.return_value = (stdout, b"")
    return process


async def _gemini_child_env(env):
    adapter = GeminiAdapter()
    output = json.dumps({"type": "result", "result": "ok"}).encode()
    with (
        patch("butlers.core.runtimes.gemini.shutil.which", return_value="/synthetic/gemini"),
        patch.dict(os.environ, _AMBIENT, clear=True),
        patch(_GEMINI_EXEC, return_value=_process(output)) as spawn,
    ):
        await adapter.invoke(prompt="p", system_prompt="", mcp_servers={}, env=env)
    return spawn.call_args.kwargs["env"]


async def test_gemini_empty_mapping_yields_an_empty_child_env_not_inheritance() -> None:
    child_env = await _gemini_child_env({})
    assert child_env == {}


async def test_gemini_none_still_inherits() -> None:
    assert await _gemini_child_env(None) is None


async def test_gemini_non_empty_mapping_is_filtered_and_used() -> None:
    child_env = await _gemini_child_env(
        {"GOOGLE_API_KEY": "synthetic-key", "DASHBOARD_API_KEY": "synthetic-owner"}
    )
    assert child_env == {"GOOGLE_API_KEY": "synthetic-key"}

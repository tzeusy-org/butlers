"""Gemini invoke() hands the CLI the butler MCP server and nothing else. [bu-7kkyk1]

Header values are synthetic and never printed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.fact_authority import INVOCATION_HEADER
from butlers.core.runtimes import GeminiAdapter

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"
_SERVERS = {
    "my-butler": {
        "url": "http://localhost:9100/mcp",
        "headers": {INVOCATION_HEADER: "synthetic-binding"},
    }
}


async def _invoke(mcp_servers: dict, env: dict[str, str] | None = None) -> dict:
    """Run invoke() and capture what the CLI would have seen at spawn time."""
    seen: dict = {}

    async def fake_exec(*cmd, **kwargs):
        child_env = kwargs["env"] or {}
        settings = child_env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
        seen.update(
            cmd=list(cmd),
            env=child_env,
            settings_path=settings,
            settings=json.loads(await asyncio.to_thread(Path(settings).read_text))
            if settings
            else None,
        )
        process = AsyncMock(returncode=0)
        process.communicate.return_value = (
            json.dumps({"type": "result", "result": "ok"}).encode(),
            b"",
        )
        return process

    with (
        patch("butlers.core.runtimes.gemini.shutil.which", return_value="/synthetic/gemini"),
        patch(_EXEC, side_effect=fake_exec),
    ):
        await GeminiAdapter().invoke(
            prompt="p", system_prompt="", mcp_servers=mcp_servers, env=env or {"PATH": "/bin"}
        )
    return seen


async def test_invoke_writes_the_butler_server_and_points_the_cli_at_it() -> None:
    seen = await _invoke(_SERVERS)
    server = seen["settings"]["mcpServers"]["my-butler"]
    assert server["httpUrl"] == "http://localhost:9100/mcp"
    assert "url" not in server


async def test_invoke_keeps_the_invocation_header() -> None:
    seen = await _invoke(_SERVERS)
    headers = seen["settings"]["mcpServers"]["my-butler"]["headers"]
    assert headers == {INVOCATION_HEADER: "synthetic-binding"}


async def test_invoke_does_not_put_header_values_on_the_command_line() -> None:
    seen = await _invoke(_SERVERS)
    assert not any("synthetic-binding" in str(arg) for arg in seen["cmd"])


async def test_invoke_limits_the_session_to_the_configured_servers() -> None:
    seen = await _invoke(_SERVERS)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--allowed-mcp-server-names") + 1] == "my-butler"


async def test_invoke_leaves_the_user_home_alone_so_cli_auth_keeps_working() -> None:
    seen = await _invoke(_SERVERS)
    assert "GEMINI_CLI_HOME" not in seen["env"]


async def test_each_invocation_gets_its_own_config_path_and_cleans_up() -> None:
    first = await _invoke(_SERVERS)
    second = await _invoke(_SERVERS)
    assert first["settings_path"] != second["settings_path"]
    assert not await asyncio.to_thread(Path(first["settings_path"]).exists)


async def test_no_servers_adds_no_mcp_wiring() -> None:
    seen = await _invoke({})
    assert seen["settings_path"] is None
    assert "--allowed-mcp-server-names" not in seen["cmd"]


async def test_invoke_trusts_the_butler_server_so_headless_tool_calls_are_not_denied() -> None:
    seen = await _invoke(_SERVERS)
    assert seen["settings"]["mcpServers"]["my-butler"]["trust"] is True

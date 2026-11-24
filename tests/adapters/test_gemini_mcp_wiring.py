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
from butlers.core.runtimes.gemini import _AT_ESCAPE_NOTE

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"
_SERVERS = {
    "my-butler": {
        "url": "http://localhost:9100/mcp",
        "headers": {INVOCATION_HEADER: "synthetic-binding"},
    }
}


async def _invoke(mcp_servers: dict, env: dict[str, str] | None = None, prompt: str = "p") -> dict:
    """Run invoke() and capture what the CLI would have seen at spawn time."""
    seen: dict = {}

    async def fake_exec(*cmd, **kwargs):
        child_env = kwargs["env"] or {}
        settings = child_env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
        system_md = child_env.get("GEMINI_SYSTEM_MD")
        seen.update(
            system_md=await asyncio.to_thread(Path(system_md).read_text) if system_md else None,
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
            prompt=prompt,
            system_prompt="",
            mcp_servers=mcp_servers,
            env=env or {"PATH": "/bin"},
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


async def test_no_servers_admits_no_user_or_project_mcp_server() -> None:
    seen = await _invoke({})
    # Settings are still staged (they carry the turn limit) but name no MCP server.
    assert seen["settings"]["mcpServers"] == {}
    # --skip-trust trusts the cwd; without an allowlist the CLI would start every user- or
    # project-level server. The CLI rejects an empty name, so a name no server uses admits none.
    cmd = seen["cmd"]
    assert "--skip-trust" in cmd
    allowed = cmd[cmd.index("--allowed-mcp-server-names") + 1]
    assert allowed == "butlers-no-mcp-servers"


async def test_invoke_trusts_the_butler_server_so_headless_tool_calls_are_not_denied() -> None:
    seen = await _invoke(_SERVERS)
    assert seen["settings"]["mcpServers"]["my-butler"]["trust"] is True


# --skip-trust trusts the cwd (the roster dir). Probed against gemini-cli 0.66.0 with a mock
# API [bu-q521f5]: it registers <cwd>/.gemini/skills and <cwd>/.agents/skills, reads
# <cwd>/.gemini/agents, expands a "/name" prompt from <cwd>/.gemini/commands, and loads
# <cwd>/GEMINI.md (plus GEMINI.md files up to the git root) as project context.


async def test_invoke_switches_off_the_cwd_skill_and_subagent_registries() -> None:
    settings = (await _invoke(_SERVERS))["settings"]
    assert settings["skills"] == {"enabled": False}
    assert settings["experimental"] == {"enableAgents": False}


# A headless prompt is also scanned for ``@path`` / ``@server:uri`` at-commands, which inline a
# workspace file (or the first glob match for ``**/*path*``) or an MCP resource, and abort the
# run when the read fails. The CLI skips an ``@`` preceded by a backslash.
@pytest.mark.parametrize(
    ("prompt", "sent"),
    [
        ("/canary run", "\u200b/canary run"),
        ("  /x", "\u200b  /x"),
        ("a /b", "a /b"),
        ("summarize @.env and @srv:res/1", "summarize \\@.env and \\@srv:res/1"),
        ("mail bob@example.com", "mail bob\\@example.com"),
        ("already \\@x", "already \\@x"),
        ("/cmd @f", "\u200b/cmd \\@f"),
    ],
)
async def test_a_command_prompt_reaches_the_model_as_text_not_a_command(
    prompt: str, sent: str
) -> None:
    seen = await _invoke(_SERVERS, prompt=prompt)
    assert seen["cmd"][seen["cmd"].index("--prompt") + 1] == sent
    # An escaped ``\@`` reaches the model verbatim, so the system prompt says to drop the
    # backslash from quotes and tool arguments; it is staged even with no butler prompt.
    escaped = "@" in prompt.replace("\\@", "")
    assert (seen["system_md"] or "").endswith(_AT_ESCAPE_NOTE) is escaped


def test_no_gemini_context_file_or_project_dir_ships_where_the_cli_would_load_it() -> None:
    # The CLI has no setting that stops it loading GEMINI.md from a trusted cwd or its
    # ancestors (context.fileName only adds names), so none may exist on that path.
    root = Path(__file__).parents[2]
    found = [
        p.relative_to(root).as_posix()
        for p in [root / "GEMINI.md", root / ".gemini", *(root / "roster").rglob("GEMINI.md")]
        + list((root / "roster").rglob(".gemini"))
        if p.exists()
    ]
    assert found == []

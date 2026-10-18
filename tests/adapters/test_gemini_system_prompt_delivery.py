"""Gemini invoke() delivers the system prompt through GEMINI_SYSTEM_MD, never argv. [bu-kqyd6c]

The upstream CLI has no ``--system-prompt`` flag and rejects unknown options; the documented
override is a file named by ``GEMINI_SYSTEM_MD``. Prompt text is synthetic.
"""

from __future__ import annotations

import gc
import json
import os
import stat
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import GeminiAdapter

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"
_PROMPT = "synthetic-butler-system-prompt-marker\nline two"


async def _invoke(
    system_prompt: str = _PROMPT,
    env: dict[str, str] | None = None,
    *,
    returncode: int = 0,
    mcp_servers: dict | None = None,
) -> dict:
    """Run invoke() and capture what the CLI would have seen at spawn time."""
    seen: dict = {}

    async def fake_exec(*cmd, **kwargs):
        child_env = kwargs["env"]
        path = (child_env or {}).get("GEMINI_SYSTEM_MD")
        seen.update(cmd=list(cmd), env=child_env, path=path)
        if path:
            seen["content"] = Path(path).read_text(encoding="utf-8")
            seen["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        process = AsyncMock(returncode=returncode)
        process.communicate.return_value = (
            json.dumps({"type": "result", "result": "ok"}).encode(),
            b"synthetic failure" if returncode else b"",
        )
        return process

    with (
        patch("butlers.core.runtimes.gemini.shutil.which", return_value="/synthetic/gemini"),
        patch(_EXEC, side_effect=fake_exec),
    ):
        try:
            await GeminiAdapter().invoke(
                prompt="user prompt",
                system_prompt=system_prompt,
                mcp_servers=mcp_servers or {},
                env={"PATH": "/bin"} if env is None else env,
            )
        except RuntimeError:
            seen["raised"] = True
    return seen


async def test_prompt_reaches_the_cli_as_a_private_file_named_by_the_env_var() -> None:
    seen = await _invoke()
    assert seen["content"] == _PROMPT
    assert seen["mode"] == 0o600
    assert Path(seen["path"]).is_absolute()


async def test_prompt_never_appears_on_argv_and_the_flag_is_gone() -> None:
    seen = await _invoke()
    assert "--system-prompt" not in seen["cmd"]
    assert not any("synthetic-butler-system-prompt-marker" in part for part in seen["cmd"])
    assert "--prompt" in seen["cmd"]


async def test_file_is_removed_after_a_successful_run() -> None:
    seen = await _invoke()
    assert not Path(seen["path"]).exists()


async def test_file_is_removed_after_a_failed_run() -> None:
    seen = await _invoke(returncode=1)
    assert seen["raised"] is True
    assert not Path(seen["path"]).exists()


async def test_empty_prompt_sets_no_variable_and_writes_no_file() -> None:
    seen = await _invoke(system_prompt="")
    assert seen["path"] is None
    assert "GEMINI_SYSTEM_MD" not in (seen["env"] or {})


async def test_empty_env_mapping_still_carries_only_the_prompt_variable() -> None:
    seen = await _invoke(env={})
    assert set(seen["env"]) == {"GEMINI_SYSTEM_MD"}


async def test_prompt_variable_coexists_with_the_mcp_settings_variable() -> None:
    seen = await _invoke(mcp_servers={"my-butler": {"url": "http://localhost:9100/mcp"}})
    assert seen["env"]["GEMINI_SYSTEM_MD"] == seen["path"]
    assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" in seen["env"]


async def test_directory_is_removed_explicitly_not_by_garbage_collection() -> None:
    """An implicit TemporaryDirectory cleanup would emit a ResourceWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await _invoke()
        gc.collect()
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)]

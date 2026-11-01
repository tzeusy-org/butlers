"""CodexAdapter prefers a stored OpenAI API key over ChatGPT device-code auth.

Covers bu-k3fdu8: ``cli-auth/openai`` is used first via ``CODEX_API_KEY``; the
``cli-auth/codex`` device-code authority is the fallback when no key is stored
or the provider rejects the key before any tool call.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.runtimes._codex_auth_sync import (
    _AUTH_AUTHORITY_CACHE,
    _AUTH_SYNC_CACHE,
    _AUTH_SYNC_LOCKS,
)
from butlers.core.runtimes.codex import CodexAdapter, _openai_api_key_rejected

pytestmark = pytest.mark.unit

_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"
_API_KEY = "sk-test-openai-key-value"


@pytest.fixture(autouse=True)
def _clear_auth_sync_state():
    _AUTH_SYNC_CACHE.clear()
    _AUTH_AUTHORITY_CACHE.clear()
    _AUTH_SYNC_LOCKS.clear()
    yield
    _AUTH_SYNC_CACHE.clear()
    _AUTH_AUTHORITY_CACHE.clear()
    _AUTH_SYNC_LOCKS.clear()


@pytest.fixture
def codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A HOME whose device-code auth.json matches the stored Codex authority."""
    auth = tmp_path / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    document = json.dumps({"access_token": "device-code-token"})
    auth.write_text(document, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    return document


def _store(codex_document: str, *, openai_key: str | None) -> MagicMock:
    store = MagicMock()
    store.shared_pool = None
    store.require_system_global_pool = MagicMock(return_value=MagicMock())
    store.load = AsyncMock(
        side_effect=lambda key: {"cli-auth/openai": openai_key}.get(key),
    )
    store.load_codex_cli_auth = AsyncMock(return_value=codex_document)
    store.store_codex_cli_auth = AsyncMock(return_value=None)
    store.store_codex_cli_auth_if_unchanged = AsyncMock(return_value=True)
    store.record_codex_cli_auth_test_result_if_unchanged = AsyncMock(return_value=True)
    store.record_test_result = AsyncMock(return_value=None)
    return store


def _proc(returncode: int, stderr: bytes = b"") -> AsyncMock:
    proc = AsyncMock()
    proc.pid = 4242
    proc.returncode = returncode
    stdout = json.dumps({"type": "result", "result": "ok"}).encode() if returncode == 0 else b""
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class _SpawnRecorder:
    """Capture each spawn's auth environment and whether auth.json was linked."""

    def __init__(self, procs: list[AsyncMock]) -> None:
        self._procs = list(procs)
        self.spawns: list[dict[str, Any]] = []

    async def __call__(self, *_cmd: str, env: dict[str, str], **_kwargs: Any) -> AsyncMock:
        self.spawns.append(
            {
                "codex_api_key": env.get("CODEX_API_KEY"),
                "openai_api_key": env.get("OPENAI_API_KEY"),
                "auth_json_linked": (Path(env["HOME"]) / ".codex" / "auth.json").exists(),
            }
        )
        return self._procs.pop(0)


async def _invoke(adapter: CodexAdapter, recorder: _SpawnRecorder, **env: str) -> Any:
    with (
        patch(_EXEC, new=recorder),
        patch("butlers.core.runtimes.codex._token_needs_refresh", return_value=False),
    ):
        result = await adapter.invoke(prompt="hi", system_prompt="", mcp_servers={}, env=env)
        await asyncio.sleep(0)  # let fire-and-forget health writes run
        return result


async def test_stored_key_runs_on_codex_api_key_without_device_code_auth(codex_home):
    store = _store(codex_home, openai_key=f"  {_API_KEY}\n")
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    recorder = _SpawnRecorder([_proc(0)])

    await _invoke(adapter, recorder, OPENAI_API_KEY="inherited", CODEX_ACCESS_TOKEN="inherited")

    assert recorder.spawns == [
        {"codex_api_key": _API_KEY, "openai_api_key": None, "auth_json_linked": False}
    ]
    # The device-code authority is neither read nor reconciled.
    store.load_codex_cli_auth.assert_not_awaited()
    store.require_system_global_pool.assert_not_called()
    assert adapter.last_process_info["auth_mode"] == "openai_api_key"


async def test_no_stored_key_uses_device_code_auth_unchanged(codex_home):
    store = _store(codex_home, openai_key=None)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    recorder = _SpawnRecorder([_proc(0)])

    await _invoke(adapter, recorder)

    assert recorder.spawns == [
        {"codex_api_key": None, "openai_api_key": None, "auth_json_linked": True}
    ]
    store.load_codex_cli_auth.assert_awaited()
    assert "auth_mode" not in adapter.last_process_info


@pytest.mark.parametrize(
    "stderr",
    [
        b"Error: 401 Unauthorized: Incorrect API key provided",
        b"Error: 429 insufficient_quota: You exceeded your current quota",
    ],
)
async def test_rejected_key_falls_back_to_device_code_auth(codex_home, stderr, caplog):
    store = _store(codex_home, openai_key=_API_KEY)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    recorder = _SpawnRecorder([_proc(1, stderr), _proc(0)])

    with caplog.at_level(logging.DEBUG):
        result_text, _tool_calls, _usage = await _invoke(adapter, recorder)

    assert result_text == "ok"
    assert recorder.spawns == [
        {"codex_api_key": _API_KEY, "openai_api_key": None, "auth_json_linked": False},
        {"codex_api_key": None, "openai_api_key": None, "auth_json_linked": True},
    ]
    store.record_test_result.assert_awaited_once()
    key, ok, _message = store.record_test_result.await_args.args
    assert (key, ok) == ("cli-auth/openai", False)
    assert adapter.last_process_info["auth_fallback"] == "openai_api_key_rejected"
    assert adapter.last_process_info["auth_mode"] == "device_code"
    assert _API_KEY not in caplog.text


async def test_unrelated_failure_with_key_does_not_fall_back(codex_home):
    store = _store(codex_home, openai_key=_API_KEY)
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    recorder = _SpawnRecorder([_proc(1, b"Error: model output could not be parsed")])

    with pytest.raises(RuntimeError, match="exited with code 1"):
        await _invoke(adapter, recorder)

    assert len(recorder.spawns) == 1
    store.load_codex_cli_auth.assert_not_awaited()
    store.record_test_result.assert_not_awaited()


async def test_key_lookup_failure_uses_device_code_auth_without_logging_detail(codex_home, caplog):
    store = _store(codex_home, openai_key=None)
    store.load = AsyncMock(side_effect=RuntimeError(f"bind {_API_KEY}"))
    adapter = CodexAdapter(codex_binary="/usr/bin/codex", credential_store=store, butler_name="qa")
    recorder = _SpawnRecorder([_proc(0)])

    with caplog.at_level(logging.DEBUG):
        await _invoke(adapter, recorder)

    assert recorder.spawns[0]["codex_api_key"] is None
    assert recorder.spawns[0]["auth_json_linked"] is True
    assert _API_KEY not in caplog.text


@pytest.mark.parametrize(
    ("process_info", "expected"),
    [
        ({"is_pre_tool_call": True, "exit_code": 1, "error_detail": "401 Unauthorized"}, True),
        ({"is_pre_tool_call": True, "exit_code": 1, "stderr": "Incorrect API key"}, True),
        ({"is_pre_tool_call": True, "exit_code": 1, "error_detail": "insufficient_quota"}, True),
        # Timeouts and cancellations say nothing about the key.
        ({"is_pre_tool_call": True, "exit_code": -1, "stderr": "401 Unauthorized"}, False),
        ({"is_pre_tool_call": False, "exit_code": 1, "error_detail": "401 Unauthorized"}, False),
        ({"is_pre_tool_call": True, "exit_code": 1, "error_detail": "model is at capacity"}, False),
        (None, False),
    ],
)
def test_openai_api_key_rejected_classifier(process_info, expected):
    assert _openai_api_key_rejected(process_info) is expected

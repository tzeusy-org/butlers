"""Tests for the CLI auth device-code flow."""

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.cli_auth.session import CLIAuthSession, _strip_ansi, clear_sessions, store_session

# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def test_claude_provider_properties():
    """Claude provider is api_key mode with correct binary and no token_path."""
    p = PROVIDERS["claude"]
    assert p.auth_mode == "api_key"
    assert p.env_var == "ANTHROPIC_API_KEY"
    assert p.runtime == "claude"
    assert p.display_name == "Claude (Anthropic)"
    assert p.binary_name == "claude"
    assert p.token_path is None


# ---------------------------------------------------------------------------
# ANSI stripping
# ---------------------------------------------------------------------------


def test_strip_ansi():
    """Strips both color codes and cursor codes."""
    color = "\x1b[34m●\x1b[0m  Go to: https://auth.openai.com/codex/device"
    assert "●  Go to: https://auth.openai.com/codex/device" in _strip_ansi(color)

    cursor = "\x1b[?25l│\n◒  Waiting\x1b[999D\x1b[J"
    cleaned = _strip_ansi(cursor)
    assert "Waiting" in cleaned
    assert "\x1b" not in cleaned


# ---------------------------------------------------------------------------
# Pattern matching tests (against real CLI output)
# ---------------------------------------------------------------------------


OPENCODE_STDOUT = """
┌  Add credential
│
●  Go to: https://auth.openai.com/codex/device
│
●  Enter code: PW8C-DO1Y7

│
◒  Waiting for authorization
◇  Login successful
│
└  Done
"""

CODEX_STDOUT = """
Welcome to Codex [v0.113.0]
OpenAI's command-line coding agent

Follow these steps to sign in with ChatGPT using device code authorization:

1. Open this link in your browser and sign in to your account
   https://auth.openai.com/codex/device

2. Enter this one-time code (expires in 15 minutes)
   PWAT-RXLE2

Device codes are a common phishing target. Never share this code.

Successfully logged in
"""


@pytest.mark.parametrize(
    "provider_key, stdout, expected_url, expected_code",
    [
        (
            "opencode-openai",
            OPENCODE_STDOUT,
            "https://auth.openai.com/codex/device",
            "PW8C-DO1Y7",
        ),
        (
            "codex",
            CODEX_STDOUT,
            "https://auth.openai.com/codex/device",
            "PWAT-RXLE2",
        ),
    ],
)
def test_provider_patterns(provider_key, stdout, expected_url, expected_code):
    """URL, code, and success patterns match their respective CLI outputs."""
    provider = PROVIDERS[provider_key]
    url_m = provider.url_pattern.search(stdout)
    assert url_m is not None and url_m.group(1) == expected_url
    code_m = provider.code_pattern.search(stdout)
    assert code_m is not None and code_m.group(1) == expected_code
    assert provider.success_pattern.search(stdout) is not None


# ---------------------------------------------------------------------------
# Session tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_sessions():
    clear_sessions()
    yield
    clear_sessions()


def _test_provider(tmp_path: Path) -> CLIAuthProviderDef:
    """A provider that runs a simple echo command."""
    return CLIAuthProviderDef(
        name="test",
        display_name="Test Provider",
        command=[
            "bash",
            "-c",
            'echo "Go to: https://auth.openai.com/codex/device"; '
            'echo "Enter code: TEST-12345"; '
            'sleep 0.2; echo "Successfully logged in"',
        ],
        url_pattern=re.compile(r"(https://auth\.openai\.com/codex/device)"),
        code_pattern=re.compile(r"Enter code: ([A-Z0-9]+-[A-Z0-9]+)"),
        success_pattern=re.compile(r"Successfully logged in"),
        token_path=tmp_path / "auth.json",
        runtime="test",
        timeout_seconds=30,
    )


async def test_session_lifecycle(tmp_path):
    """Session parses device code and reaches success; store/get work; timeout expires."""
    provider = _test_provider(tmp_path)
    session = CLIAuthSession(id="test-1", provider=provider)
    await session.start()
    await session.wait(timeout=5.0)

    assert session.auth_url == "https://auth.openai.com/codex/device"
    assert session.device_code == "TEST-12345"
    assert session.state == "success"

    # store and retrieve
    store_session(session)
    from butlers.cli_auth.session import get_session

    assert get_session("test-1") is session
    assert get_session("nonexistent") is None


async def test_session_timeout(tmp_path):
    """Session should expire when timeout is very short."""
    provider = CLIAuthProviderDef(
        name="slow",
        display_name="Slow",
        command=["sleep", "60"],
        url_pattern=re.compile(r"(https://\S+)"),
        code_pattern=re.compile(r"code: (\S+)"),
        success_pattern=re.compile(r"success"),
        token_path=tmp_path / "auth.json",
        runtime="test",
        timeout_seconds=1,
    )
    session = CLIAuthSession(id="timeout-test", provider=provider)
    await session.start()
    await asyncio.sleep(2)

    assert session.state == "expired"


# ---------------------------------------------------------------------------
# Claude provider health probe tests
# ---------------------------------------------------------------------------


async def test_claude_health_probe_authenticated():
    """probe_provider returns authenticated via credential store, env, and non-standard key."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider

    provider = PROVIDERS["claude"]

    # Via credential store
    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value="sk-ant-test-key-abc123")
    with patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/claude"):
        result = await probe_provider(provider, credential_store=mock_store)
    assert result.state == AuthHealthState.authenticated
    mock_store.load.assert_awaited_once_with("cli-auth/claude")

    # Via env fallback (no store)
    import os

    with patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/claude"):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-env-key"}, clear=False):
            result2 = await probe_provider(provider, credential_store=None)
    assert result2.state == AuthHealthState.authenticated

    # Non-standard key format
    mock_store2 = MagicMock()
    mock_store2.load = AsyncMock(return_value="some-other-key-format")
    with patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/claude"):
        result3 = await probe_provider(provider, credential_store=mock_store2)
    assert result3.state == AuthHealthState.authenticated
    assert "non-standard" in (result3.detail or "")


async def test_claude_health_probe_not_authenticated_or_unavailable():
    """probe_provider returns not_authenticated when no key; unavailable when binary missing."""
    import os

    from butlers.cli_auth.health import AuthHealthState, probe_provider

    provider = PROVIDERS["claude"]

    # No key anywhere
    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=None)
    env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/claude"):
        with patch.dict(os.environ, env_without_key, clear=True):
            result = await probe_provider(provider, credential_store=mock_store)
    assert result.state == AuthHealthState.not_authenticated

    # Binary missing
    with patch("butlers.cli_auth.registry.shutil.which", return_value=None):
        result2 = await probe_provider(provider, credential_store=None)
    assert result2.state == AuthHealthState.unavailable


# ---------------------------------------------------------------------------
# Codex backend probe — catches server-side refresh-token revocation that
# `codex login status` alone can't see.
# ---------------------------------------------------------------------------


def _fake_codex_auth_file(tmp_path: Path, exp_offset: int = 86400) -> Path:
    """Write a minimal ~/.codex/auth.json with a JWT that expires in the future."""
    import base64 as _b64
    import json as _json
    import time as _time

    header = _b64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    payload_json = _json.dumps({"exp": int(_time.time()) + exp_offset}).encode()
    payload = _b64.urlsafe_b64encode(payload_json).rstrip(b"=").decode()
    access_token = f"{header}.{payload}.sig"

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(_json.dumps({"tokens": {"access_token": access_token}}))
    return auth_path


async def test_codex_backend_probe_flags_revoked_token(tmp_path):
    """A 401 from the backend downgrades the provider to not_authenticated."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider

    auth_path = _fake_codex_auth_file(tmp_path)

    async def fake_subprocess(*_args, **_kwargs):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"Logged in using ChatGPT\n", b""))
        return proc

    class _Resp:
        status_code = 401

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    import dataclasses as _dc

    codex = _dc.replace(PROVIDERS["codex"], token_path=auth_path)

    with (
        patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/codex"),
        patch("butlers.cli_auth.health.asyncio.create_subprocess_exec", fake_subprocess),
        patch("butlers.cli_auth.health.httpx.AsyncClient", _FakeClient),
    ):
        result = await probe_provider(codex)

    assert result.state == AuthHealthState.not_authenticated
    assert "401" in (result.detail or "")


async def test_codex_backend_probe_network_error_keeps_authenticated(tmp_path):
    """Transient network failure on the backend probe must not red-flag Codex."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider

    auth_path = _fake_codex_auth_file(tmp_path)

    async def fake_subprocess(*_args, **_kwargs):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"Logged in using ChatGPT\n", b""))
        return proc

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            import httpx as _httpx

            raise _httpx.ConnectError("network down")

    import dataclasses as _dc

    codex = _dc.replace(PROVIDERS["codex"], token_path=auth_path)

    with (
        patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/codex"),
        patch("butlers.cli_auth.health.asyncio.create_subprocess_exec", fake_subprocess),
        patch("butlers.cli_auth.health.httpx.AsyncClient", _FakeClient),
    ):
        result = await probe_provider(codex)

    assert result.state == AuthHealthState.authenticated


# ---------------------------------------------------------------------------
# /test endpoint: device_code providers probe live health (no api_key reject)
# ---------------------------------------------------------------------------


async def test_test_endpoint_probes_device_code_provider():
    """POST /cli-auth/{provider}/test must not 400 device_code providers.

    The frontend probe button calls this endpoint for every auth mode. For a
    device_code provider (e.g. Codex) it should run the live health probe and
    map an authenticated result to success=True instead of rejecting.
    """
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

    healthy = AuthHealthResult(
        provider="codex",
        state=AuthHealthState.authenticated,
        detail="Logged in using ChatGPT",
    )
    with patch(
        "butlers.api.routers.cli_auth.probe_provider",
        AsyncMock(return_value=healthy),
    ):
        resp = await test_api_key("codex", db_manager=None)

    assert resp.provider == "codex"
    assert resp.success is True
    assert resp.detail == "Logged in using ChatGPT"


async def test_test_endpoint_device_code_not_authenticated_reports_failure():
    """A not_authenticated probe result maps to success=False with the detail."""
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

    revoked = AuthHealthResult(
        provider="codex",
        state=AuthHealthState.not_authenticated,
        detail="OpenAI rejected the stored token (401) — re-login required.",
    )
    with patch(
        "butlers.api.routers.cli_auth.probe_provider",
        AsyncMock(return_value=revoked),
    ):
        resp = await test_api_key("codex", db_manager=None)

    assert resp.success is False
    assert "re-login required" in resp.detail

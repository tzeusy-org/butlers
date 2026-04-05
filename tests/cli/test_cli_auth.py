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


def test_providers_registered_and_display_names():
    """All expected providers registered with correct display names and modes."""
    assert "opencode-openai" in PROVIDERS
    assert "codex" in PROVIDERS
    assert "opencode-go" in PROVIDERS
    assert "claude" in PROVIDERS

    assert PROVIDERS["codex"].binary() == "codex"
    assert PROVIDERS["opencode-openai"].display_name == "OpenCode (OpenAI)"
    assert PROVIDERS["codex"].display_name == "Codex (OpenAI)"
    assert PROVIDERS["opencode-go"].auth_mode == "api_key"
    assert PROVIDERS["opencode-go"].env_var == "OPENCODE_GO_API_KEY"


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

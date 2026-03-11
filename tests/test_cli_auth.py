"""Tests for the CLI auth device-code flow."""

import asyncio
import re
from pathlib import Path

import pytest

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.cli_auth.session import CLIAuthSession, _strip_ansi, clear_sessions, store_session

# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def test_providers_registered():
    assert "opencode-openai" in PROVIDERS
    assert "codex" in PROVIDERS
    assert "opencode-go" in PROVIDERS


def test_provider_binary_defaults_to_command0():
    p = PROVIDERS["codex"]
    assert p.binary() == "codex"


def test_provider_display_name():
    assert PROVIDERS["opencode-openai"].display_name == "OpenCode (OpenAI)"
    assert PROVIDERS["codex"].display_name == "Codex (OpenAI)"
    assert PROVIDERS["opencode-go"].display_name == "OpenCode Go"


def test_opencode_go_is_api_key_mode():
    p = PROVIDERS["opencode-go"]
    assert p.auth_mode == "api_key"
    assert p.env_var == "OPENCODE_GO_API_KEY"


# ---------------------------------------------------------------------------
# ANSI stripping
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_color_codes():
    raw = "\x1b[34m●\x1b[0m  Go to: https://auth.openai.com/codex/device"
    assert "●  Go to: https://auth.openai.com/codex/device" in _strip_ansi(raw)


def test_strip_ansi_removes_cursor_codes():
    raw = "\x1b[?25l│\n◒  Waiting\x1b[999D\x1b[J"
    cleaned = _strip_ansi(raw)
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


class TestOpenCodePatterns:
    provider = PROVIDERS["opencode-openai"]

    def test_url_pattern(self):
        m = self.provider.url_pattern.search(OPENCODE_STDOUT)
        assert m is not None
        assert m.group(1) == "https://auth.openai.com/codex/device"

    def test_code_pattern(self):
        m = self.provider.code_pattern.search(OPENCODE_STDOUT)
        assert m is not None
        assert m.group(1) == "PW8C-DO1Y7"

    def test_success_pattern(self):
        m = self.provider.success_pattern.search(OPENCODE_STDOUT)
        assert m is not None


class TestCodexPatterns:
    provider = PROVIDERS["codex"]

    def test_url_pattern(self):
        m = self.provider.url_pattern.search(CODEX_STDOUT)
        assert m is not None
        assert m.group(1) == "https://auth.openai.com/codex/device"

    def test_code_pattern(self):
        m = self.provider.code_pattern.search(CODEX_STDOUT)
        assert m is not None
        assert m.group(1) == "PWAT-RXLE2"

    def test_success_pattern(self):
        m = self.provider.success_pattern.search(CODEX_STDOUT)
        assert m is not None


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


async def test_session_parses_device_code(tmp_path):
    provider = _test_provider(tmp_path)
    session = CLIAuthSession(id="test-1", provider=provider)
    await session.start()
    await session.wait(timeout=5.0)

    assert session.auth_url == "https://auth.openai.com/codex/device"
    assert session.device_code == "TEST-12345"
    assert session.state == "success"


async def test_session_store():
    provider = CLIAuthProviderDef(
        name="dummy",
        display_name="Dummy",
        command=["true"],
        url_pattern=re.compile(r"x"),
        code_pattern=re.compile(r"x"),
        success_pattern=re.compile(r"x"),
        token_path=Path("/nonexistent"),
        runtime="dummy",
    )
    session = CLIAuthSession(id="s1", provider=provider)
    store_session(session)

    from butlers.cli_auth.session import get_session
    assert get_session("s1") is session
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

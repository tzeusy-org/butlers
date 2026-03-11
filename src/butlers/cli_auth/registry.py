"""CLI auth provider registry.

Each provider defines how a CLI tool authenticates. Two modes are supported:

- **device_code**: Interactive device-code flow (e.g. OpenCode, Codex). The
  provider defines the command, stdout patterns, and token path.
- **api_key**: Simple API key entry. The key is stored in the credential
  store and injected as an environment variable at runtime.

All providers support an optional status command for health probing.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CLIAuthProviderDef:
    """Definition of a CLI tool's auth flow."""

    name: str
    """Unique identifier (used in API paths)."""

    display_name: str
    """Human-readable label for the dashboard."""

    runtime: str
    """Butler runtime adapter name this provider authenticates."""

    auth_mode: str = "device_code"
    """Auth mode: ``"device_code"`` or ``"api_key"``."""

    # -- device_code fields --------------------------------------------------

    command: list[str] = field(default_factory=list)
    """Command + args to spawn the login flow (device_code mode only)."""

    url_pattern: re.Pattern[str] | None = None
    """Regex to extract the authorization URL from stdout."""

    code_pattern: re.Pattern[str] | None = None
    """Regex to extract the device code from stdout."""

    success_pattern: re.Pattern[str] | None = None
    """Regex to detect successful login in stdout."""

    token_path: Path | None = None
    """Path to the credential file written by the CLI on success."""

    # -- api_key fields ------------------------------------------------------

    env_var: str = ""
    """Environment variable name to inject the API key as (api_key mode)."""

    test_command: list[str] = field(default_factory=list)
    """Command to run for testing the API key (api_key mode). The env_var is
    set in the subprocess environment before running this command."""

    test_ok_pattern: re.Pattern[str] | None = None
    """Regex that matches test command output on success."""

    # -- shared fields -------------------------------------------------------

    status_command: list[str] | None = None
    """Command to check current auth status (e.g. ``["codex", "login", "status"]``)."""

    status_ok_pattern: re.Pattern[str] | None = None
    """Regex that matches status output when authenticated."""

    binary_name: str = ""
    """CLI binary name to check availability (defaults to command[0])."""

    timeout_seconds: int = 900
    """Maximum time to wait for authorization (15 minutes default)."""

    def binary(self) -> str:
        if self.binary_name:
            return self.binary_name
        if self.command:
            return self.command[0]
        if self.test_command:
            return self.test_command[0]
        return ""

    def is_available(self) -> bool:
        """Check if the CLI binary is on PATH."""
        b = self.binary()
        return bool(b) and shutil.which(b) is not None

    def is_authenticated(self) -> bool:
        """Check if credential file exists on disk (device_code mode)."""
        if self.token_path is not None:
            return self.token_path.exists()
        return False


# ---------------------------------------------------------------------------
# Provider definitions
# ---------------------------------------------------------------------------

# Shared patterns for OpenAI device code flow
_OPENAI_DEVICE_URL = re.compile(r"(https://auth\.openai\.com/codex/device)")
_OPENAI_DEVICE_CODE = re.compile(r"(?:Enter code:\s*|^\s+)([A-Z0-9]+-[A-Z0-9]+)", re.MULTILINE)

PROVIDERS: dict[str, CLIAuthProviderDef] = {}


def _register(provider: CLIAuthProviderDef) -> CLIAuthProviderDef:
    PROVIDERS[provider.name] = provider
    return provider


_register(
    CLIAuthProviderDef(
        name="opencode-openai",
        display_name="OpenCode (OpenAI)",
        command=["opencode", "auth", "login", "-p", "openai", "-m", "ChatGPT Pro/Plus (headless)"],
        url_pattern=_OPENAI_DEVICE_URL,
        code_pattern=_OPENAI_DEVICE_CODE,
        success_pattern=re.compile(r"Login successful", re.IGNORECASE),
        token_path=Path.home() / ".local" / "share" / "opencode" / "auth.json",
        runtime="opencode",
        # `opencode auth list` outputs "● OpenAI oauth" when authenticated
        status_command=["opencode", "auth", "list"],
        status_ok_pattern=re.compile(r"OpenAI\s+oauth", re.IGNORECASE),
    )
)

_register(
    CLIAuthProviderDef(
        name="codex",
        display_name="Codex (OpenAI)",
        command=["codex", "login", "--device-auth"],
        url_pattern=_OPENAI_DEVICE_URL,
        code_pattern=_OPENAI_DEVICE_CODE,
        success_pattern=re.compile(r"Successfully logged in", re.IGNORECASE),
        token_path=Path.home() / ".codex" / "auth.json",
        runtime="codex",
        # `codex login status` outputs "Logged in using ChatGPT" when authenticated
        status_command=["codex", "login", "status"],
        status_ok_pattern=re.compile(r"Logged in", re.IGNORECASE),
    )
)

_register(
    CLIAuthProviderDef(
        name="opencode-go",
        display_name="OpenCode Go",
        runtime="opencode",
        auth_mode="api_key",
        env_var="OPENCODE_GO_API_KEY",
        binary_name="opencode",
        # Token is stored inside the shared opencode auth.json
        token_path=Path.home() / ".local" / "share" / "opencode" / "auth.json",
        # Test: run a minimal prompt with an OpenCode Go model
        test_command=[
            "opencode", "run", "--model", "opencode-go/minimax-m2.5", "respond with ok",
        ],
        test_ok_pattern=re.compile(r"(?:ok|OK|Ok)", re.IGNORECASE),
    )
)

"""CLI auth provider registry.

Each provider defines the command, stdout patterns, and token path for a
specific CLI tool's device-code login flow, plus a status command for
health probing.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CLIAuthProviderDef:
    """Definition of a CLI tool's device-code auth flow."""

    name: str
    """Unique identifier (used in API paths)."""

    display_name: str
    """Human-readable label for the dashboard."""

    command: list[str]
    """Command + args to spawn the login flow."""

    url_pattern: re.Pattern[str]
    """Regex to extract the authorization URL from stdout."""

    code_pattern: re.Pattern[str]
    """Regex to extract the device code from stdout."""

    success_pattern: re.Pattern[str]
    """Regex to detect successful login in stdout."""

    token_path: Path
    """Path to the credential file written by the CLI on success."""

    runtime: str
    """Butler runtime adapter name this provider authenticates."""

    status_command: list[str] | None = None
    """Command to check current auth status (e.g. ``["codex", "login", "status"]``)."""

    status_ok_pattern: re.Pattern[str] | None = None
    """Regex that matches status output when authenticated."""

    binary_name: str = ""
    """CLI binary name to check availability (defaults to command[0])."""

    timeout_seconds: int = 900
    """Maximum time to wait for authorization (15 minutes default)."""

    def binary(self) -> str:
        return self.binary_name or self.command[0]

    def is_available(self) -> bool:
        """Check if the CLI binary is on PATH."""
        return shutil.which(self.binary()) is not None

    def is_authenticated(self) -> bool:
        """Check if credential file exists on disk."""
        return self.token_path.exists()


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

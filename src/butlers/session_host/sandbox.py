"""Session profiles over the existing exact-image Bubblewrap/PID1 launcher."""

from pathlib import Path

from butlers.cli_auth.registry import PROVIDERS
from butlers.cli_auth.sandbox import ReadonlySandboxAuthority
from butlers.cli_auth.sandbox_platform import BubblewrapDashboardCLIAuthSandbox
from butlers.session_host.launch import PreparedLaunch, Refused, StagedFile


class BubblewrapSessionSandbox:
    """No direct subprocess, ambient credential or inherited-home fallback."""

    def __init__(self, platform=None):
        self._platform = platform if platform is not None else BubblewrapDashboardCLIAuthSandbox()

    async def run(self, prepared: PreparedLaunch, *, timeout_s: float) -> bytes:
        if type(prepared) is not PreparedLaunch:
            raise Refused("invalid_session_staging")
        if prepared.runtime == "codex":
            provider = PROVIDERS["codex"]
            expected = {Path(".codex/auth.json"), Path(".codex/config.toml"), Path("system.md")}
        elif prepared.runtime == "opencode":
            provider = PROVIDERS["opencode-openai"]
            expected = {
                Path(".local/share/opencode/auth.json"),
                Path(".config/opencode/opencode.json"),
                Path("system.md"),
            }
        else:
            raise Refused("invalid_session_staging")
        if (
            type(prepared) is not PreparedLaunch
            or not prepared.command
            or any(type(item) is not str for item in prepared.command)
            or len(prepared.authorities) != 3
            or any(type(item) is not StagedFile for item in prepared.authorities)
            or {item.relative_path for item in prepared.authorities} != expected
        ):
            raise Refused("invalid_session_staging")
        result = await self._platform.run_staged_command(
            provider,
            command=(provider.runtime, *prepared.command[1:]),
            expected_executable=Path(prepared.command[0]),
            authorities=tuple(
                ReadonlySandboxAuthority(item.relative_path, item.content)
                for item in prepared.authorities
            ),
            timeout_s=timeout_s,
        )
        if result.returncode != 0:
            raise Refused("runtime_failed")
        return result.output

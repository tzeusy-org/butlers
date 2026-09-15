"""Tests for the CLI auth device-code flow."""

import asyncio
import os
import re
from dataclasses import replace
from inspect import getsource
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef, providers_for_runtime
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


def test_providers_for_runtime_returns_matching_providers_only():
    """providers_for_runtime (bu-ur7go) filters PROVIDERS by .runtime, used by
    DiscretionDispatcher.get_auth_health() to find the on-disk auth artifact
    for a resolved model-catalog runtime_type without hardcoding provider
    names."""
    codex_providers = providers_for_runtime("codex")
    assert [p.name for p in codex_providers] == ["codex"]

    # "opencode" has two registered providers (device-code + api_key) sharing
    # the same on-disk auth.json.
    opencode_providers = providers_for_runtime("opencode")
    assert {p.name for p in opencode_providers} == {"opencode-openai", "opencode-go"}

    assert providers_for_runtime("no-such-runtime") == []


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


class _TestSandboxHandle:
    """Test-only process owner; production sessions have no direct fallback."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process

    async def finalize(self, *, succeeded: bool) -> bytes | None:
        if self.process.returncode is None:
            await self.terminate()
        return b'{"tokens":{"access_token":"test-only"}}' if succeeded else None

    async def terminate(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=1.0)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()


class _TestSandbox:
    """Fixture adapter that supplies a harmless Bash child to session tests."""

    async def launch_device_auth(self, provider: CLIAuthProviderDef) -> _TestSandboxHandle:
        process = await asyncio.create_subprocess_exec(
            *provider.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
        return _TestSandboxHandle(process)


async def _test_persistence_callback(
    _provider: CLIAuthProviderDef, *, staged_output: bytes
) -> bool:
    """Accept harmless fake staged bytes for session parsing tests."""
    assert staged_output == b'{"tokens":{"access_token":"test-only"}}'
    return True


async def test_session_lifecycle(tmp_path):
    """Session parses device code and reaches success; store/get work; timeout expires."""
    provider = _test_provider(tmp_path)
    session = CLIAuthSession(
        id="test-1",
        provider=provider,
        on_success=_test_persistence_callback,
        sandbox=_TestSandbox(),
    )
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


async def test_codex_session_never_logs_device_code_or_callback_error(tmp_path, caplog) -> None:
    """Codex device-auth diagnostics do not leak into shared process logs."""
    provider = replace(
        _test_provider(tmp_path),
        name="codex",
        command=[
            "bash",
            "-c",
            'echo "Enter code: DEVICE-CODE-MARKER"; echo "Successfully logged in"',
        ],
    )

    async def _raise_with_sensitive_detail(_: CLIAuthProviderDef, *, staged_output: bytes) -> None:
        del staged_output
        raise RuntimeError("callback-error-marker")

    session = CLIAuthSession(
        id="codex-redaction",
        provider=provider,
        on_success=_raise_with_sensitive_detail,
        sandbox=_TestSandbox(),
    )
    with caplog.at_level("INFO", logger="butlers.cli_auth.session"):
        await session.start()
        await session.wait(timeout=5.0)

    assert session.state == "failed"
    assert session.message == "Codex authentication was not saved to the system authority."
    assert "DEVICE-CODE-MARKER" not in caplog.text
    assert "callback-error-marker" not in caplog.text
    assert "Codex on_success callback failed safely" in caplog.text


async def test_codex_session_fails_when_global_persistence_is_not_confirmed(tmp_path: Path) -> None:
    """REQ-core-credentials-001: a local-only device-auth result is not a successful login."""
    provider = replace(
        _test_provider(tmp_path),
        name="codex",
        command=["bash", "-c", 'echo "Successfully logged in"'],
    )

    async def _not_persisted(_: CLIAuthProviderDef, *, staged_output: bytes) -> bool:
        del staged_output
        return False

    session = CLIAuthSession(
        id="codex-global-persist",
        provider=provider,
        on_success=_not_persisted,
        sandbox=_TestSandbox(),
    )
    await session.start()
    await session.wait(timeout=5.0)

    assert session.state == "failed"
    assert session.message == "Codex authentication was not saved to the system authority."


async def test_codex_device_auth_refuses_to_start_without_global_authority() -> None:
    """REQ-core-credentials-001: no local Codex device-auth session is spawned without authority."""
    from fastapi import HTTPException

    from butlers.api.routers.cli_auth import start_auth

    with (
        patch("butlers.api.routers.cli_auth._make_credential_store", return_value=None),
        patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/codex"),
        patch("butlers.api.routers.cli_auth.CLIAuthSession.start", new_callable=AsyncMock) as start,
        patch("butlers.api.routers.cli_auth.CLIAuthSession.wait", new_callable=AsyncMock),
        pytest.raises(HTTPException) as exc_info,
    ):
        await start_auth("codex", db_manager=MagicMock())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "System-global Codex credential authority unavailable."
    start.assert_not_awaited()


async def test_device_auth_captures_its_authority_baseline_before_starting_the_child() -> None:
    """REQ-core-credentials-002: a child never starts before its CAS baseline exists."""
    from butlers.api.routers.cli_auth import start_auth

    events: list[str] = []
    store = MagicMock()

    async def _capture(*_args, **_kwargs) -> str | None:
        events.append("baseline")
        return "authority-before-launch"

    async def _start(session: CLIAuthSession) -> None:
        assert session.on_success is not None
        assert await session.on_success.prepare_for_device_auth(session.provider) is True
        events.append("start")

    with (
        patch("butlers.api.routers.cli_auth._make_credential_store", return_value=store),
        patch(
            "butlers.api.routers.cli_auth.capture_device_auth_authority_baseline",
            side_effect=_capture,
        ) as capture,
        patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/opencode"),
        patch("butlers.api.routers.cli_auth.CLIAuthSession.start", new=_start),
        patch("butlers.api.routers.cli_auth.CLIAuthSession.wait", new_callable=AsyncMock),
    ):
        await start_auth("opencode-openai", db_manager=MagicMock())

    assert events == ["baseline", "start"]
    capture.assert_awaited_once_with(
        PROVIDERS["opencode-openai"],
        store,
        codex_authority=None,
    )


async def test_codex_provider_listing_does_not_trust_a_local_auth_file(
    tmp_path: Path, monkeypatch
) -> None:
    """REQ-core-credentials-001: dashboard status follows the explicit authority probe."""
    from butlers.api.routers.cli_auth import list_providers
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

    local_auth = tmp_path / "auth.json"
    local_auth.write_text('{"stale": "local"}', encoding="utf-8")
    provider = CLIAuthProviderDef(
        name="codex",
        display_name="Codex",
        runtime="codex",
        auth_mode="device_code",
        command=["true"],
        url_pattern=re.compile(r"(https://\\S+)"),
        code_pattern=re.compile(r"code: (\\S+)"),
        success_pattern=re.compile(r"success"),
        token_path=local_auth,
        status_command=["true"],
        status_ok_pattern=re.compile(r"Logged in"),
    )
    monkeypatch.setitem(PROVIDERS, "codex", provider)
    unavailable = AuthHealthResult(
        provider="codex",
        state=AuthHealthState.probe_failed,
        detail="System-global Codex authority unavailable; probe was not run.",
    )

    with patch(
        "butlers.api.routers.cli_auth.probe_all", AsyncMock(return_value={"codex": unavailable})
    ):
        providers = await list_providers(db_manager=None)

    codex = next(item for item in providers if item.name == "codex")
    assert codex.authenticated is False
    assert codex.health is not None
    assert codex.health.value == AuthHealthState.probe_failed.value


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
    session = CLIAuthSession(
        id="timeout-test",
        provider=provider,
        on_success=_test_persistence_callback,
        sandbox=_TestSandbox(),
    )
    await session.start()
    await asyncio.sleep(2)

    assert session.state == "expired"


async def test_codex_dashboard_success_never_launches_a_prewarm_child(tmp_path: Path) -> None:
    """REQ-core-credentials-002: device auth persists without a Dashboard CLI child."""
    from butlers.api.routers.cli_auth import _build_on_success

    provider = replace(PROVIDERS["codex"], token_path=tmp_path / ".codex" / "auth.json")
    store = MagicMock()

    async def _persist(*_args, **_kwargs) -> bool:
        return True

    with (
        patch("butlers.api.routers.cli_auth._make_credential_store", return_value=store),
        patch(
            "butlers.api.routers.cli_auth.capture_device_auth_authority_baseline",
            new_callable=AsyncMock,
            return_value="authority-before-launch",
        ) as capture,
        patch(
            "butlers.api.routers.cli_auth.persist_validated_staged_device_auth_bytes",
            side_effect=_persist,
        ) as persist,
        patch(
            "butlers.core.runtimes.codex.run_codex_pre_warm",
            new_callable=AsyncMock,
            return_value=True,
        ) as prewarm,
    ):
        on_success = _build_on_success(MagicMock())
        assert on_success is not None
        assert await on_success.prepare_for_device_auth(provider) is True
        assert (
            await on_success(
                provider,
                staged_output=b'{"tokens":{"access_token":"not-a-real-token"}}',
            )
            is True
        )

    prewarm.assert_not_awaited()
    capture.assert_awaited_once_with(provider, store, codex_authority=store)
    assert persist.await_args.kwargs["expected_authority_value"] == "authority-before-launch"


def test_legacy_on_success_factory_remains_a_synchronous_callable_for_secrets_v2() -> None:
    """REQ-core-credentials-002: the excluded secrets router never receives a coroutine."""
    from butlers.api.routers.cli_auth import _build_on_success

    store = MagicMock()
    with patch("butlers.api.routers.cli_auth._make_credential_store", return_value=store):
        on_success = _build_on_success(MagicMock())

    assert callable(on_success)
    assert not asyncio.iscoroutine(on_success)
    assert callable(on_success.prepare_for_device_auth)


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


@pytest.mark.parametrize(
    "canonical_model",
    [
        "opencode-go/minimax-m2.7",
        "opencode-go/mimo-v2.5",
        "opencode-go/minimax-m3",
    ],
)
async def test_opencode_go_health_command_preserves_provider_qualified_model(
    canonical_model, tmp_path: Path
):
    """REQ-runtime-opencode-001/REQ-core-credentials-002: qualified argv stays sandboxed."""
    from butlers.api.routers.cli_auth import _run_provider_test

    auth_path = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    peer_openai_sentinel = "OPENAI-PEER-AUTHORITY-MUST-NOT-REACH-GO"
    auth_path.write_text(
        '{"opencode-go":{"type":"api","key":"test-only"},'
        f'"openai":{{"type":"oauth","refresh":"{peer_openai_sentinel}"}}}}',
        encoding="utf-8",
    )
    os.chmod(auth_path, 0o600)
    provider = replace(
        PROVIDERS["opencode-go"],
        token_path=auth_path,
        test_command=[
            "opencode",
            "run",
            "--model",
            canonical_model,
            "respond with only the word ok",
        ],
    )
    commands: list[tuple[str, ...]] = []

    class _RecordingSandbox:
        async def run_readonly_command(self, _provider, *, command, authority, timeout_s):
            assert _provider is provider
            assert authority.relative_path == Path(".local") / "share" / "opencode" / "auth.json"
            assert authority.content == b'{"opencode-go":{"key":"test-only","type":"api"}}'
            assert peer_openai_sentinel.encode() not in authority.content
            assert timeout_s == 30
            commands.append(command)
            return SimpleNamespace(returncode=0, output=b"ok")

    result = await _run_provider_test(provider, None, sandbox=_RecordingSandbox())

    assert result.success is True
    command = commands[0]
    assert command[command.index("--model") + 1] == canonical_model
    assert provider.test_command[provider.test_command.index("--model") + 1] == canonical_model
    assert "create_subprocess_exec" not in getsource(_run_provider_test)


async def test_opencode_go_health_command_accepts_short_form_model_flag(tmp_path: Path):
    """bu-8xdhf: the model lookup must not assume ``--model`` is the only spelling.

    registry.py's opencode-openai entry already uses ``-m`` as a live short
    form on this CLI, and registry.py's opencode-go maintenance note warns the
    test_command shape may be repinned — the lookup must survive that.
    """
    from butlers.api.routers.cli_auth import _run_provider_test

    auth_path = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text('{"opencode-go":{"type":"api","key":"test-only"}}', encoding="utf-8")
    os.chmod(auth_path, 0o600)
    canonical_model = "opencode-go/minimax-m3"
    provider = replace(
        PROVIDERS["opencode-go"],
        token_path=auth_path,
        test_command=[
            "opencode",
            "run",
            "-m",
            canonical_model,
            "respond with only the word ok",
        ],
    )
    commands: list[tuple[str, ...]] = []

    class _RecordingSandbox:
        async def run_readonly_command(self, _provider, *, command, authority, timeout_s):
            commands.append(command)
            return SimpleNamespace(returncode=0, output=b"ok")

    result = await _run_provider_test(provider, None, sandbox=_RecordingSandbox())

    assert result.success is True
    command = commands[0]
    assert command[command.index("-m") + 1] == canonical_model


async def test_opencode_go_health_command_reports_shape_mismatch_distinctly(tmp_path: Path):
    """bu-8xdhf: a reshaped test_command must not read as a credential failure.

    Previously ``test_command.index('--model')`` raised ``ValueError`` when the
    flag was missing/renamed, which the broad ``except Exception`` turned into
    the same generic "Test command failed to execute." detail as a real
    credential failure. The distinct detail here must not claim the
    credential itself failed.
    """
    from butlers.api.routers.cli_auth import _run_provider_test

    auth_path = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text('{"opencode-go":{"type":"api","key":"test-only"}}', encoding="utf-8")
    os.chmod(auth_path, 0o600)
    provider = replace(
        PROVIDERS["opencode-go"],
        token_path=auth_path,
        test_command=[
            "opencode",
            "run",
            "--llm",
            "opencode-go/minimax-m3",
            "respond with only the word ok",
        ],
    )

    class _UnreachableSandbox:
        async def run_readonly_command(self, _provider, *, command, authority, timeout_s):
            raise AssertionError("sandbox must not run when the test command shape is unrecognized")

    result = await _run_provider_test(provider, None, sandbox=_UnreachableSandbox())

    assert result.success is False
    assert "credential" not in result.detail.lower() or "not a credential failure" in result.detail
    assert "misconfigured" in result.detail.lower()


async def test_status_probe_stages_regular_authority_copy_through_shared_sandbox(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: health children never receive the canonical authority path."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider

    auth_path = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    peer_go_sentinel = "GO-PEER-AUTHORITY-MUST-NOT-REACH-OPENAI"
    auth_path.write_text(
        '{"openai":{"type":"oauth","refresh":"openai-refresh",'
        '"access":"openai-access","expires":1700000000},'
        f'"opencode-go":{{"type":"api","key":"{peer_go_sentinel}"}}}}',
        encoding="utf-8",
    )
    os.chmod(auth_path, 0o600)
    provider = replace(PROVIDERS["opencode-openai"], token_path=auth_path)

    class _RecordingSandbox:
        async def run_readonly_command(self, _provider, *, command, authority, timeout_s):
            assert _provider is provider
            assert command == tuple(provider.status_command or ())
            assert authority.relative_path == Path(".local") / "share" / "opencode" / "auth.json"
            assert authority.content == (
                b'{"openai":{"access":"openai-access","expires":1700000000,'
                b'"refresh":"openai-refresh","type":"oauth"}}'
            )
            assert peer_go_sentinel.encode() not in authority.content
            assert timeout_s == 15
            return SimpleNamespace(returncode=0, output=b"OpenAI oauth\n")

    with (
        patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/opencode"),
        patch(
            "butlers.cli_auth.health.asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as direct_spawn,
    ):
        result = await probe_provider(provider, sandbox=_RecordingSandbox())

    assert result.state is AuthHealthState.authenticated
    direct_spawn.assert_not_awaited()


# ---------------------------------------------------------------------------
# Codex parent-only backend probe — catches server-side refresh-token
# revocation without launching a credential-mutating CLI child.
# ---------------------------------------------------------------------------


async def test_codex_probe_is_parent_only_and_never_launches_or_finalizes(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: Codex health never spawns a status child."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider
    from butlers.core.runtimes._codex_auth_sync import CodexAuthSyncResult

    auth_path = _fake_codex_auth_file(tmp_path)
    os.chmod(auth_path, 0o600)
    expected_authority = auth_path.read_text(encoding="utf-8")
    codex = replace(PROVIDERS["codex"], token_path=auth_path)

    class _PoisonSandbox:
        async def run_readonly_command(self, *_args, **_kwargs):
            pytest.fail("Codex health must not launch a sandbox status child")

    with (
        patch(
            "butlers.cli_auth.health.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as direct_spawn,
        patch(
            "butlers.cli_auth.health._probe_codex_backend",
            AsyncMock(return_value=(False, None)),
        ) as backend_probe,
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            return_value=CodexAuthSyncResult(expected_store_value=expected_authority),
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.codex_auth_file_matches_authority",
            return_value=True,
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.finalize_codex_auth_rotation",
            new_callable=AsyncMock,
        ) as finalizer,
    ):
        result = await probe_provider(
            codex,
            codex_authority=MagicMock(),
            sandbox=_PoisonSandbox(),
        )

    assert result.state is AuthHealthState.authenticated
    direct_spawn.assert_not_awaited()
    finalizer.assert_not_awaited()
    backend_probe.assert_awaited_once()


@pytest.mark.parametrize(
    ("authority_document", "expected_detail"),
    [
        ("{not json", "malformed"),
        ("{}", "missing"),
        ('{"tokens":{"access_token":"not-a-jwt"}}', "malformed"),
    ],
)
async def test_codex_probe_rejects_malformed_or_missing_authority_before_backend_or_child(
    tmp_path: Path,
    authority_document: str,
    expected_detail: str,
) -> None:
    """REQ-core-credentials-002: invalid parent authority fails before any child."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider
    from butlers.core.runtimes._codex_auth_sync import CodexAuthSyncResult

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(authority_document, encoding="utf-8")
    os.chmod(auth_path, 0o600)
    codex = replace(PROVIDERS["codex"], token_path=auth_path)

    class _PoisonSandbox:
        async def run_readonly_command(self, *_args, **_kwargs):
            pytest.fail("invalid Codex authority must not launch a child")

    with (
        patch(
            "butlers.cli_auth.health.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as direct_spawn,
        patch(
            "butlers.cli_auth.health._probe_codex_backend",
            AsyncMock(side_effect=AssertionError("backend must not receive invalid auth")),
        ) as backend_probe,
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            return_value=CodexAuthSyncResult(expected_store_value=authority_document),
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.codex_auth_file_matches_authority",
            return_value=True,
        ),
    ):
        result = await probe_provider(
            codex,
            codex_authority=MagicMock(),
            sandbox=_PoisonSandbox(),
        )

    assert result.state is AuthHealthState.not_authenticated
    assert expected_detail in (result.detail or "").lower()
    direct_spawn.assert_not_awaited()
    backend_probe.assert_not_awaited()


async def test_codex_probe_requires_explicit_system_global_authority(tmp_path: Path) -> None:
    """REQ-core-credentials-001: a local Codex auth file never authorizes a probe."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"authority":"opaque-local-only"}', encoding="utf-8")
    codex = replace(PROVIDERS["codex"], token_path=auth_path)

    with (
        patch("butlers.cli_auth.registry.shutil.which", return_value="/usr/bin/codex"),
        patch("butlers.cli_auth.health.asyncio.create_subprocess_exec") as spawn,
    ):
        result = await probe_provider(codex)

    assert result.state is AuthHealthState.probe_failed
    assert "system-global" in (result.detail or "").lower()
    spawn.assert_not_awaited()


async def test_codex_probe_returns_a_value_free_parent_only_detail(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-core-credentials-001: Codex parent-only diagnostics stay value-free."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider
    from butlers.core.runtimes._codex_auth_sync import CodexAuthSyncResult

    auth_path = _fake_codex_auth_file(tmp_path)
    raw_authority_marker = "raw-authority-must-not-be-persisted"
    auth_path.write_text(
        auth_path.read_text(encoding="utf-8").replace(
            '{"tokens"',
            f'{{"marker":"{raw_authority_marker}","tokens"',
        ),
        encoding="utf-8",
    )
    os.chmod(auth_path, 0o600)
    expected_authority = auth_path.read_text(encoding="utf-8")
    codex = replace(PROVIDERS["codex"], token_path=auth_path)

    with (
        patch(
            "butlers.cli_auth.health._probe_codex_backend", AsyncMock(return_value=(False, None))
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            return_value=CodexAuthSyncResult(expected_store_value=expected_authority),
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.codex_auth_file_matches_authority",
            return_value=True,
        ),
        caplog.at_level("DEBUG"),
    ):
        result = await probe_provider(codex, codex_authority=MagicMock())

    assert result.state is AuthHealthState.authenticated
    assert result.detail == "Codex authority validated."
    assert raw_authority_marker not in caplog.text


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
    from butlers.core.runtimes._codex_auth_sync import CodexAuthSyncResult

    auth_path = _fake_codex_auth_file(tmp_path)

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

    authority = MagicMock()
    expected_authority = auth_path.read_text(encoding="utf-8")
    with (
        patch("butlers.cli_auth.health.httpx.AsyncClient", _FakeClient),
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            return_value=CodexAuthSyncResult(expected_store_value=expected_authority),
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.codex_auth_file_matches_authority",
            return_value=True,
        ),
    ):
        result = await probe_provider(codex, codex_authority=authority)

    assert result.state == AuthHealthState.not_authenticated
    assert "401" in (result.detail or "")


async def test_codex_backend_probe_network_error_keeps_authenticated(tmp_path):
    """Transient network failure on the backend probe must not red-flag Codex."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider
    from butlers.core.runtimes._codex_auth_sync import CodexAuthSyncResult

    auth_path = _fake_codex_auth_file(tmp_path)

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

    authority = MagicMock()
    expected_authority = auth_path.read_text(encoding="utf-8")
    with (
        patch("butlers.cli_auth.health.httpx.AsyncClient", _FakeClient),
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            return_value=CodexAuthSyncResult(expected_store_value=expected_authority),
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.codex_auth_file_matches_authority",
            return_value=True,
        ),
    ):
        result = await probe_provider(codex, codex_authority=authority)

    assert result.state == AuthHealthState.authenticated


async def test_codex_parent_probe_rejects_expired_authority_before_backend_or_child(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: expiry is a parent-side no-child rejection."""
    from butlers.cli_auth.health import AuthHealthState, probe_provider
    from butlers.core.runtimes._codex_auth_sync import CodexAuthSyncResult

    auth_path = _fake_codex_auth_file(tmp_path, exp_offset=-1)
    os.chmod(auth_path, 0o600)
    expected_authority = auth_path.read_text(encoding="utf-8")
    codex = replace(PROVIDERS["codex"], token_path=auth_path)

    class _PoisonSandbox:
        async def run_readonly_command(self, *_args, **_kwargs):
            pytest.fail("expired Codex authority must not launch a child")

    with (
        patch(
            "butlers.cli_auth.health.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as direct_spawn,
        patch(
            "butlers.cli_auth.health._probe_codex_backend",
            AsyncMock(side_effect=AssertionError("backend must not receive an expired token")),
        ) as backend_probe,
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            return_value=CodexAuthSyncResult(expected_store_value=expected_authority),
        ),
        patch(
            "butlers.core.runtimes._codex_auth_sync.codex_auth_file_matches_authority",
            return_value=True,
        ),
    ):
        result = await probe_provider(
            codex,
            codex_authority=MagicMock(),
            sandbox=_PoisonSandbox(),
        )

    assert result.state is AuthHealthState.not_authenticated
    assert "expired" in (result.detail or "").lower()
    direct_spawn.assert_not_awaited()
    backend_probe.assert_not_awaited()


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


# ---------------------------------------------------------------------------
# /test endpoint: outcome persistence (probe log + test-state cache + audit)
# ---------------------------------------------------------------------------


class _RecordingPool:
    """Minimal asyncpg-pool stand-in that records every execute() call."""

    def __init__(self):
        self.execute_calls: list[tuple[str, tuple]] = []
        self.events: list[str] = []
        self.after_transaction = None

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        if "UPDATE butler_secrets" in sql:
            self.events.append("health")
        elif "secret_probe_log" in sql:
            self.events.append("probe-log")
        return "UPDATE 1" if "UPDATE butler_secrets" in sql else "INSERT 0 1"

    def acquire(self):
        from contextlib import asynccontextmanager

        pool = self

        @asynccontextmanager
        async def _cm():
            conn = MagicMock()
            conn.execute = pool.execute

            @asynccontextmanager
            async def _transaction():
                pool.events.append("transaction-enter")
                try:
                    yield
                finally:
                    pool.events.append("transaction-exit")
                    if pool.after_transaction is not None:
                        await pool.after_transaction()

            conn.transaction = _transaction
            yield conn

        return _cm()


def _make_persisting_db_manager() -> tuple[MagicMock, _RecordingPool]:
    pool = _RecordingPool()
    db_manager = MagicMock()
    db_manager.credential_shared_pool = MagicMock(return_value=pool)
    return db_manager, pool


async def test_api_key_test_never_returns_or_persists_sandbox_stdout_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: child output cannot become a response or audit secret sink."""
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.sandbox import SandboxedCommandResult

    sentinel = "SANDBOXED-AUTHORITY-MUST-NOT-ESCAPE"
    auth_path = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        f'{{"opencode-go":{{"type":"api","key":"{sentinel}"}}}}',
        encoding="utf-8",
    )
    os.chmod(auth_path, 0o600)
    provider = replace(PROVIDERS["opencode-go"], token_path=auth_path)
    db_manager, pool = _make_persisting_db_manager()
    audit_notes: list[str | None] = []

    class _LeakingSandbox:
        async def run_readonly_command(self, *_args, **_kwargs) -> SandboxedCommandResult:
            return SandboxedCommandResult(returncode=0, output=f"ok {sentinel}".encode())

    async def _audit(_conn, *, action, credential_id, note=None) -> None:
        assert action == "verified"
        assert credential_id == "cli-auth/opencode-go"
        audit_notes.append(note)

    import butlers.api.routers.secrets_v2 as secrets_v2

    monkeypatch.setitem(PROVIDERS, "opencode-go", provider)
    monkeypatch.setattr("butlers.api.routers.cli_auth.dashboard_cli_auth_sandbox", _LeakingSandbox)
    monkeypatch.setattr(secrets_v2, "_write_cli_audit", _audit)

    response = await test_api_key("opencode-go", db_manager=db_manager)

    assert response.success is True
    assert response.detail == "Provider CLI credential check succeeded."
    persisted_or_returned = "\n".join(
        [
            response.detail,
            repr(pool.execute_calls),
            repr(audit_notes),
        ]
    )
    assert sentinel not in persisted_or_returned


async def test_test_endpoint_persists_successful_probe(monkeypatch):
    """A successful test writes probe_log + test-state cache + verified audit.

    Regression [2026-07-05]: the endpoint returned its result without
    persisting anything, so the passport's "probe · last test" panel (which
    reads only public.secret_probe_log) reverted to "never probed" on refresh
    and the cli-auth rows were stuck in the needs-hand bucket forever.
    """
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

    db_manager, pool = _make_persisting_db_manager()

    audit_calls: list[dict] = []

    async def _fake_audit(p, *, action, credential_id, note=None):
        pool.events.append("audit")
        audit_calls.append({"action": action, "credential_id": credential_id, "note": note})

    import butlers.api.routers.secrets_v2 as _secrets_v2

    monkeypatch.setattr(_secrets_v2, "_write_cli_audit", _fake_audit)

    healthy = AuthHealthResult(
        provider="codex",
        state=AuthHealthState.authenticated,
        detail="Logged in using ChatGPT",
    )
    with (
        patch(
            "butlers.api.routers.cli_auth.probe_provider",
            AsyncMock(return_value=healthy),
        ),
        patch(
            "butlers.api.routers.cli_auth._prepare_codex_test_authority",
            AsyncMock(return_value=("authority-A", Path("/tmp/codex-auth.json"))),
        ),
        patch(
            "butlers.api.routers.cli_auth._codex_test_authority_still_matches", return_value=True
        ),
    ):
        resp = await test_api_key("codex", db_manager=db_manager)

    assert resp.success is True

    probe_log_calls = [c for c in pool.execute_calls if "secret_probe_log" in c[0]]
    assert len(probe_log_calls) == 1
    scope, key, ok, code, message, latency_ms = probe_log_calls[0][1]
    assert (scope, key, ok, code) == ("cli", "cli-auth/codex", True, None)
    assert message == "Logged in using ChatGPT"
    assert isinstance(latency_ms, int)

    cache_calls = [c for c in pool.execute_calls if "last_test_ok" in c[0]]
    assert len(cache_calls) == 1
    assert cache_calls[0][1] == (True, None, "cli-auth/codex", "authority-A")

    assert audit_calls == [
        {
            "action": "verified",
            "credential_id": "cli-auth/codex",
            "note": "Probe ok: Logged in using ChatGPT",
        }
    ]
    assert pool.events == [
        "transaction-enter",
        "health",
        "probe-log",
        "audit",
        "transaction-exit",
    ]


async def test_test_endpoint_finishes_codex_history_before_dashboard_replacement(monkeypatch):
    """A replacement cannot split a Codex health/log/audit outcome.

    The replacement is modelled as waiting on the credential-row transaction:
    it may run only after the fenced health update, probe log, and audit have
    all finished.  Its value write then resets that completed prior health
    state, so no old failure can become a newer result for the replacement.
    """
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

    db_manager, pool = _make_persisting_db_manager()

    async def _dashboard_replacement() -> None:
        pool.events.append("dashboard-B")

    pool.after_transaction = _dashboard_replacement

    async def _fake_audit(_connection, *, action, credential_id, note=None):
        pool.events.append("audit")

    import butlers.api.routers.secrets_v2 as _secrets_v2

    monkeypatch.setattr(_secrets_v2, "_write_cli_audit", _fake_audit)
    healthy = AuthHealthResult(
        provider="codex",
        state=AuthHealthState.authenticated,
        detail="Logged in using ChatGPT",
    )
    with (
        patch("butlers.api.routers.cli_auth.probe_provider", AsyncMock(return_value=healthy)),
        patch(
            "butlers.api.routers.cli_auth._prepare_codex_test_authority",
            AsyncMock(return_value=("authority-A", Path("/tmp/codex-auth.json"))),
        ),
        patch(
            "butlers.api.routers.cli_auth._codex_test_authority_still_matches", return_value=True
        ),
    ):
        response = await test_api_key("codex", db_manager=db_manager)

    assert response.success is True
    assert pool.events == [
        "transaction-enter",
        "health",
        "probe-log",
        "audit",
        "transaction-exit",
        "dashboard-B",
    ]


async def test_test_endpoint_persists_failed_probe(monkeypatch):
    """A failed test records ok=False with the failure detail in all stores."""
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

    db_manager, pool = _make_persisting_db_manager()

    audit_calls: list[dict] = []

    async def _fake_audit(p, *, action, credential_id, note=None):
        pool.events.append("audit")
        audit_calls.append({"action": action, "credential_id": credential_id, "note": note})

    import butlers.api.routers.secrets_v2 as _secrets_v2

    monkeypatch.setattr(_secrets_v2, "_write_cli_audit", _fake_audit)

    revoked = AuthHealthResult(
        provider="codex",
        state=AuthHealthState.not_authenticated,
        detail="OpenAI rejected the stored token (401) — re-login required.",
    )
    with (
        patch(
            "butlers.api.routers.cli_auth.probe_provider",
            AsyncMock(return_value=revoked),
        ),
        patch(
            "butlers.api.routers.cli_auth._prepare_codex_test_authority",
            AsyncMock(return_value=("authority-A", Path("/tmp/codex-auth.json"))),
        ),
        patch(
            "butlers.api.routers.cli_auth._codex_test_authority_still_matches", return_value=True
        ),
    ):
        resp = await test_api_key("codex", db_manager=db_manager)

    assert resp.success is False

    probe_log_calls = [c for c in pool.execute_calls if "secret_probe_log" in c[0]]
    assert len(probe_log_calls) == 1
    assert probe_log_calls[0][1][2] is False  # ok column

    cache_calls = [c for c in pool.execute_calls if "last_test_ok" in c[0]]
    assert len(cache_calls) == 1
    ok, message, key, expected_value = cache_calls[0][1]
    assert ok is False
    assert "re-login required" in message
    assert key == "cli-auth/codex"
    assert expected_value == "authority-A"

    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "failed"
    assert "re-login required" in audit_calls[0]["note"]


async def test_test_endpoint_reconciles_codex_auth_before_fencing_probe_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A stale canonical file is replaced before a Codex probe consumes it.

    The probe starts with local A while the dashboard has persisted B.  The
    test endpoint reconciles the actual auth file to B first, then carries B
    into the conditional health update.  ``False`` simulates a later dashboard
    refresh winning while the probe is in flight.
    """
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState
    from butlers.core.runtimes._codex_auth_sync import CodexAuthSyncResult

    events: list[str] = []
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir()
    auth_path.write_text('{"refresh_token":"local-A"}', encoding="utf-8")
    authority_b = '{"refresh_token":"dashboard-B"}'
    provider = replace(PROVIDERS["codex"], token_path=auth_path)
    store = MagicMock()
    store.pool = MagicMock()
    store.pool.execute = AsyncMock()

    async def _reconcile(
        token_path: Path, passed_store, *, butler_name: str
    ) -> CodexAuthSyncResult:
        assert token_path == auth_path
        assert passed_store is store
        assert butler_name == "dashboard"
        events.append("reconcile")
        token_path.write_text(authority_b, encoding="utf-8")
        return CodexAuthSyncResult(expected_store_value=authority_b)

    async def _probe(probed_provider, *_args, **_kwargs) -> AuthHealthResult:
        assert probed_provider.token_path is not None
        assert probed_provider.token_path.read_text(encoding="utf-8") == authority_b
        events.append("probe")
        return AuthHealthResult(
            provider="codex",
            state=AuthHealthState.not_authenticated,
            detail="old token rejected",
        )

    async def _record(*_args, **_kwargs) -> bool:
        events.append("fenced-write")
        return False

    import butlers.api.routers.cli_auth as _cli_auth

    monkeypatch.setattr(_cli_auth, "_persist_codex_test_outcome_if_current", _record)
    audit = AsyncMock()

    import butlers.api.routers.secrets_v2 as _secrets_v2

    monkeypatch.setattr(_secrets_v2, "_write_cli_audit", audit)
    monkeypatch.setitem(PROVIDERS, "codex", provider)
    with (
        patch("butlers.api.routers.cli_auth._make_credential_store", return_value=store),
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            side_effect=_reconcile,
        ),
        patch("butlers.api.routers.cli_auth.probe_provider", side_effect=_probe),
    ):
        response = await test_api_key("codex", db_manager=MagicMock())

    assert response.success is False
    assert events == ["reconcile", "probe", "fenced-write"]
    store.pool.execute.assert_not_awaited()
    audit.assert_not_awaited()


async def test_test_endpoint_withholds_result_when_codex_authority_changes_during_parent_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A concurrent Codex update withholds a stale parent-only health result."""
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState
    from butlers.core.runtimes._codex_auth_sync import CodexAuthSyncResult

    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir()
    authority_b = '{"refresh_token":"dashboard-B"}'
    authority_c = '{"refresh_token":"dashboard-C"}'
    provider = replace(PROVIDERS["codex"], token_path=auth_path)
    store = MagicMock()
    store.pool = MagicMock()
    store.pool.execute = AsyncMock()
    events: list[str] = []

    async def _reconcile(token_path: Path, *_args, **_kwargs) -> CodexAuthSyncResult:
        events.append("reconcile")
        token_path.write_text(authority_b, encoding="utf-8")
        return CodexAuthSyncResult(expected_store_value=authority_b)

    async def _probe(*_args, **_kwargs) -> AuthHealthResult:
        events.append("probe")
        auth_path.write_text(authority_c, encoding="utf-8")
        return AuthHealthResult(
            provider="codex",
            state=AuthHealthState.authenticated,
            detail="Codex authority validated.",
        )

    monkeypatch.setitem(PROVIDERS, "codex", provider)
    with (
        patch("butlers.api.routers.cli_auth._make_credential_store", return_value=store),
        patch(
            "butlers.core.runtimes._codex_auth_sync.reconcile_codex_auth",
            side_effect=_reconcile,
        ),
        patch("butlers.api.routers.cli_auth.probe_provider", side_effect=_probe),
        patch(
            "butlers.core.runtimes._codex_auth_sync.finalize_codex_auth_rotation",
            new_callable=AsyncMock,
        ) as finalizer,
    ):
        response = await test_api_key("codex", db_manager=MagicMock())

    assert response.success is True
    assert events == ["reconcile", "probe"]
    finalizer.assert_not_awaited()
    store.pool.execute.assert_not_awaited()


async def test_test_endpoint_persistence_failure_does_not_mask_result():
    """Persistence errors are swallowed — the test result still returns."""
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=Exception("DB down"))
    pool.acquire = MagicMock(side_effect=Exception("DB down"))
    db_manager = MagicMock()
    db_manager.credential_shared_pool = MagicMock(return_value=pool)

    healthy = AuthHealthResult(
        provider="codex",
        state=AuthHealthState.authenticated,
        detail="Logged in using ChatGPT",
    )
    with (
        patch(
            "butlers.api.routers.cli_auth.probe_provider",
            AsyncMock(return_value=healthy),
        ),
        patch(
            "butlers.api.routers.cli_auth._prepare_codex_test_authority",
            AsyncMock(return_value=("authority-A", Path("/tmp/codex-auth.json"))),
        ),
        patch(
            "butlers.api.routers.cli_auth._codex_test_authority_still_matches", return_value=True
        ),
    ):
        resp = await test_api_key("codex", db_manager=db_manager)

    assert resp.success is True
    assert resp.detail == "Logged in using ChatGPT"


async def test_test_endpoint_does_not_log_codex_authority_on_persistence_failure(
    monkeypatch,
    caplog,
) -> None:
    """A credential-bearing persistence exception is reduced to safe context."""
    from butlers.api.routers.cli_auth import test_api_key
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

    sentinel = "raw-codex-authority-must-not-reach-logs"
    healthy = AuthHealthResult(
        provider="codex",
        state=AuthHealthState.authenticated,
        detail="Logged in using ChatGPT",
    )

    async def _raise_with_authority(*_args, **_kwargs) -> bool:
        raise RuntimeError(sentinel)

    import butlers.api.routers.cli_auth as _cli_auth

    monkeypatch.setattr(_cli_auth, "_persist_codex_test_outcome_if_current", _raise_with_authority)
    with (
        patch("butlers.api.routers.cli_auth.probe_provider", AsyncMock(return_value=healthy)),
        patch(
            "butlers.api.routers.cli_auth._prepare_codex_test_authority",
            AsyncMock(return_value=(sentinel, Path("/tmp/codex-auth.json"))),
        ),
        patch(
            "butlers.api.routers.cli_auth._codex_test_authority_still_matches", return_value=True
        ),
        caplog.at_level("WARNING"),
    ):
        response = await test_api_key("codex", db_manager=_make_persisting_db_manager()[0])

    assert response.success is True
    assert sentinel not in caplog.text

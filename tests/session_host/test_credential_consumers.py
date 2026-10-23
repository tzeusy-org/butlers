"""Configured credential consumers; synthetic authority, no provider/native IO."""

import json
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


async def test_configured_startup_restore_uses_remote_selected_authority_before_local_io(
    monkeypatch,
):
    from butlers.cli_auth import persistence
    from butlers.session_host import service_auth

    remote = type("Remote", (), {"sync_provider": AsyncMock()})()
    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
    monkeypatch.setattr(service_auth, "_dashboard_backend", lambda: remote)
    store = type("Store", (), {"load": AsyncMock(side_effect=AssertionError("local read"))})()

    def local_write(*_):
        raise AssertionError("dashboard must not materialize a local credential file")

    monkeypatch.setattr(persistence, "_write_token_file_atomically", local_write)
    result = await persistence.restore_tokens(store, codex_authority=store)
    assert {name for name, ok in result.items() if ok} == {
        "codex",
        "opencode-openai",
        "opencode-go",
    }
    assert [call.args for call in remote.sync_provider.await_args_list] == [
        (name,) for name in persistence.PROVIDERS if name in result and result[name]
    ]
    store.load.assert_not_awaited()


@pytest.mark.parametrize("shared", [False, True])
async def test_opencode_authority_uses_same_selected_pool_for_read_CAS_and_delete(shared):
    from butlers.credential_store import CredentialStore

    class Pool:
        def __init__(self, value):
            self.value, self.calls = value, []

        @asynccontextmanager
        async def acquire(self):
            yield self

        async def fetch(self, sql, keys):
            self.calls.append(("read", keys))
            return [{"secret_key": "cli-auth/opencode-go", "secret_value": self.value}]

        async def execute(self, sql, key):
            self.calls.append(("delete", key))
            self.value = None
            return "DELETE 1"

    local, other = Pool("local"), Pool("selected")
    store = CredentialStore(local, fallback_pools=[other] if shared else None)
    selected = other if shared else local
    assert (await store.load_opencode_cli_auths())["opencode-go"] == selected.value

    async def cas(pool, key, value, **kwargs):
        assert pool is selected and key == "cli-auth/opencode-go"
        assert kwargs["expected_value"] == selected.value
        pool.value = value
        return True

    store._store_if_unchanged_on_pool = cas
    assert await store.store_shared_if_unchanged(
        "cli-auth/opencode-go", "new", expected_value=selected.value
    )
    assert (await store.load_opencode_cli_auths())["opencode-go"] == "new"
    assert await store.delete_opencode_cli_auth("opencode-go")
    assert selected.value is None
    if shared:
        assert local.value == "local" and local.calls == []
    with pytest.raises(ValueError):
        await store.delete_opencode_cli_auth("codex")
    selected.fetch = AsyncMock(side_effect=OSError("unavailable"))
    with pytest.raises(OSError):
        await store.load_opencode_cli_auths()


async def test_actual_API_save_delete_commits_then_syncs_without_dashboard_file(
    monkeypatch,
    tmp_path,
):
    from dataclasses import replace

    from butlers.api.routers import cli_auth
    from butlers.session_host import service_auth

    values = {"opencode-openai": "selected OAuth sibling", "opencode-go": None}
    calls = []

    class Store:
        async def load_opencode_cli_auths(self):
            return dict(values)

        async def store_shared_if_unchanged(self, key, value, *, expected_value, **_):
            assert key == "cli-auth/opencode-go" and values["opencode-go"] == expected_value
            values["opencode-go"] = value
            calls.append("committed-save")
            return True

        async def delete_opencode_cli_auth(self, name):
            assert name == "opencode-go"
            values[name] = None
            calls.append("committed-delete")

        async def delete(self, *_):
            raise AssertionError("local-first delete must not select another authority")

    class Remote:
        async def sync_provider(self, name):
            assert name == "opencode-go"
            assert calls[-1] in {"committed-save", "committed-delete"}
            calls.append("host-sync")

    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
    monkeypatch.setattr(service_auth, "_dashboard_backend", Remote)
    monkeypatch.setattr(cli_auth, "_make_credential_store", lambda _: Store())
    provider = replace(
        cli_auth.PROVIDERS["opencode-go"], token_path=tmp_path / "absent" / "auth.json"
    )
    monkeypatch.setitem(cli_auth.PROVIDERS, "opencode-go", provider)
    result = await cli_auth.save_api_key(
        "opencode-go", cli_auth.CLIAuthApiKeyRequest(api_key="synthetic-test-key"), object()
    )
    assert (
        result.stored
        and json.loads(values["opencode-go"])["opencode-go"]["key"] == "synthetic-test-key"
    )
    assert not provider.token_path.parent.exists()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as invalid:
        await cli_auth.save_api_key(
            "opencode-go", cli_auth.CLIAuthApiKeyRequest(api_key=""), object()
        )
    assert invalid.value.status_code == 422 and calls == ["committed-save", "host-sync"]
    await cli_auth.delete_api_key("opencode-go", object())
    assert calls == ["committed-save", "host-sync", "committed-delete", "host-sync"]
    assert values == {"opencode-openai": "selected OAuth sibling", "opencode-go": None}
    assert not provider.token_path.parent.exists()


async def test_real_fixed_volume_rebuild_removes_absent_entry_and_refuses_unavailable_peer(
    tmp_path,
):
    from butlers.session_host.credential_volume import (
        ControlOpenCodeSync,
        OpenCodeCredentialVolume,
    )
    from butlers.session_host.launch import Refused

    root = tmp_path / "volume"
    root.mkdir(mode=0o700)
    volume = OpenCodeCredentialVolume(root, expected_uid=os.getuid())
    oauth = {
        "openai": {
            "type": "oauth",
            "refresh": "synthetic-refresh",
            "access": "synthetic-access",
            "expires": 9999999999999,
        }
    }
    api = {"opencode-go": {"type": "api", "key": "synthetic-key"}}
    values = {"opencode-openai": json.dumps(oauth), "opencode-go": json.dumps(api)}

    class Authority:
        async def load_opencode_cli_auths(self):
            return dict(values)

    class Host:
        async def read_opencode(self):
            return await volume.read()

        async def replace_opencode(self, **kwargs):
            return await volume.replace(**kwargs)

    sync = ControlOpenCodeSync(Host(), Authority())
    await sync.prepare()
    assert json.loads((await volume.read()).content) == oauth | api
    values["opencode-go"] = None
    await sync.prepare()
    before = await volume.read()
    assert json.loads(before.content) == oauth
    values["opencode-go"] = '{"unknown":{"type":"api","key":"synthetic"}}'
    with pytest.raises(Refused, match="credential_authority_unavailable"):
        await sync.prepare()
    assert await volume.read() == before
    values["opencode-go"] = None
    values["opencode-openai"] = None
    await sync.prepare()
    assert (await volume.read()).content == b"{}"
    await volume.close()


async def test_configured_API_probe_is_parent_HTTP_and_fences_exact_tested_value(monkeypatch):
    import base64

    from butlers.api.routers import cli_auth
    from butlers.cli_auth import health, persistence
    from butlers.session_host import service_auth
    from butlers.session_host.credential_volume import ControlCodexSync

    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": 9999999999}).encode()).decode().rstrip("=")
    )
    tested = json.dumps({"tokens": {"access_token": f"synthetic.{payload}.signature"}})
    authority = type("Authority", (), {"load_codex_cli_auth": AsyncMock(return_value=tested)})()
    sync = ControlCodexSync(object(), authority)
    events = []

    async def http_probe(token):
        assert token == json.loads(tested)["tokens"]["access_token"]
        events.append("HTTP")
        authority.load_codex_cli_auth.return_value = "newer authority"
        return False, None

    async def record(store, **kwargs):
        assert store is authority and kwargs["expected_store_value"] == tested
        assert authority.load_codex_cli_auth.return_value != tested
        events.append("stale result withheld")
        return False

    class Remote:
        async def probe_provider(self, name, *, record=False):
            assert name == "codex" and record
            return await sync.health(record=record)

    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
    monkeypatch.setattr(service_auth, "_dashboard_backend", Remote)
    monkeypatch.setattr(health, "_probe_codex_backend", http_probe)
    monkeypatch.setattr(cli_auth, "_persist_codex_test_outcome_if_current", record)
    monkeypatch.setattr(
        cli_auth,
        "_prepare_codex_test_authority",
        AsyncMock(side_effect=AssertionError("local file")),
    )
    monkeypatch.setattr(
        persistence, "_write_token_file_atomically", lambda *_: pytest.fail("local write")
    )
    result = await cli_auth.test_api_key("codex", object())
    assert result.success and events == ["HTTP", "stale result withheld"]
    cli_auth._prepare_codex_test_authority.assert_not_awaited()


async def test_actual_device_auth_API_and_listing_do_not_require_dashboard_binary_or_file(
    monkeypatch,
):
    import asyncio
    from types import SimpleNamespace

    from butlers.api.routers import cli_auth
    from butlers.cli_auth.health import AuthHealthResult, AuthHealthState
    from butlers.cli_auth.registry import CLIAuthProviderDef
    from butlers.cli_auth.session import clear_sessions, get_session
    from butlers.session_host import service_auth

    selected = {"opencode-openai": None, "opencode-go": None}
    staged = b'{"openai":{"type":"oauth","refresh":"synthetic","access":"synthetic","expires":9999999999999}}'
    events = []

    class Store:
        async def load_opencode_cli_auths(self):
            events.append("selected baseline")
            return dict(selected)

        async def store_shared_if_unchanged(self, key, value, *, expected_value, **_):
            assert key == "cli-auth/opencode-openai" and expected_value is None
            assert events[-1] == "settled artifact"
            selected["opencode-openai"] = value
            events.append("committed")
            return True

    class Handle:
        def __init__(self):
            reader = asyncio.StreamReader()
            reader.feed_data(b"Enter code: ABCD-EFGH\nLogin successful\n")
            reader.feed_eof()
            self.process = SimpleNamespace(stdout=reader, returncode=None)

        async def finalize(self, *, succeeded):
            assert succeeded
            self.process.returncode = 0
            events.append("settled artifact")
            return staged

        async def terminate(self):
            pass

    class Remote:
        async def launch_device_auth(self, provider):
            assert provider is cli_auth.PROVIDERS["opencode-openai"]
            assert events == ["selected baseline"]
            return Handle()

        async def sync_provider(self, name):
            assert name == "opencode-openai" and events[-1] == "committed"
            events.append("host selected authority sync")

        async def probe_provider(self, name):
            return AuthHealthResult(name, AuthHealthState.authenticated, "Synthetic host status.")

    remote = Remote()
    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
    monkeypatch.setattr(service_auth, "_dashboard_backend", lambda: remote)
    monkeypatch.setattr(cli_auth, "_make_credential_store", lambda _: Store())
    monkeypatch.setattr(CLIAuthProviderDef, "is_available", lambda _: pytest.fail("local binary"))
    monkeypatch.setattr(
        CLIAuthProviderDef, "is_authenticated", lambda _: pytest.fail("local token file")
    )
    result = await cli_auth.start_auth("opencode-openai", object())
    session = get_session(result.session_id)
    try:
        async with asyncio.timeout(2):
            await session._done_event.wait()
        assert session.state == "success" and json.loads(selected["opencode-openai"]) == json.loads(
            staged
        )
        assert events == [
            "selected baseline",
            "settled artifact",
            "committed",
            "host selected authority sync",
        ]
        providers = await cli_auth.list_providers(object())
        assert next(row for row in providers if row.name == "opencode-openai").authenticated
        assert all(row.token_path is None for row in providers)
    finally:
        await session.kill()
        clear_sessions()


async def test_fixed_registered_status_waits_for_actual_worker_on_repeated_cancel(
    monkeypatch, tmp_path
):
    import asyncio
    from pathlib import Path

    from butlers.cli_auth import sandbox_platform
    from butlers.cli_auth.registry import PROVIDERS
    from butlers.session_host.auth import AuthService
    from butlers.session_host.credential_volume import OpenCodeCredentialVolume
    from butlers.session_host.launch import Refused

    volume = OpenCodeCredentialVolume(tmp_path, expected_uid=os.getuid())
    tmp_path.chmod(0o700)
    content = b'{"openai":{"type":"oauth","refresh":"synthetic","access":"synthetic","expires":9999999999999}}'
    await volume.replace(expected=None, content=content)
    entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    definition = PROVIDERS["opencode-openai"]

    def resolve(provider, command):
        assert provider is definition and command == tuple(definition.status_command)
        return sandbox_platform.ReadonlySandboxInvocation(("/fixture/opencode", *command[1:]), ())

    class Backend:
        healthy = False

        async def run_staged_command(self, provider, **kwargs):
            assert provider is definition and kwargs["timeout_s"] == 15
            assert kwargs["expected_executable"] == Path("/fixture/opencode")
            assert json.loads(kwargs["authorities"][0].content) == json.loads(content)
            if self.healthy:
                from butlers.cli_auth.sandbox import SandboxedCommandResult

                return SandboxedCommandResult(returncode=0, output=b"OpenAI oauth")
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()
                raise

    monkeypatch.setattr(sandbox_platform, "resolve_readonly_runtime_inputs", resolve)
    backend = Backend()
    service = AuthService(backend, opencode_volume=volume)
    task = asyncio.create_task(service._status("opencode-openai"))
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            task.cancel()
            await cancelling.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and len(service._cleanup) == 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(Refused):
            await service._status("caller-selected-command")
        assert not service._cleanup
        backend.healthy = True
        assert (await service._status("opencode-openai")).state.value == "authenticated"
    finally:
        release.set()
        try:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            await service.close()


async def test_runtime_sync_selects_host_before_path_and_never_credits_local_rotation(
    monkeypatch, tmp_path
):
    from butlers.core.runtimes import _codex_auth_sync as sync
    from butlers.session_host import service_auth

    authority = object()
    remote_sync = AsyncMock()
    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
    monkeypatch.setattr(service_auth, "reconcile_runtime_codex", remote_sync)
    monkeypatch.setattr(
        sync, "_read_valid_auth_snapshot", lambda *_: pytest.fail("local authority read")
    )
    path = tmp_path / "not-materialized" / "auth.json"
    result = await sync.reconcile_codex_auth(path, authority)
    remote_sync.assert_awaited_once_with(authority)
    assert not result.authority_known and result.expected_store_value is None
    finalized = await sync.finalize_codex_auth_rotation(
        path, authority, expected_store_value="old selected bytes", authority_known=True
    )
    assert not finalized.authority_known and not path.parent.exists()


@pytest.mark.parametrize("shared", [False, True])
async def test_opencode_probe_audit_uses_selected_transaction_and_stale_value_cannot_write(
    shared, monkeypatch
):
    from butlers.api.routers import cli_auth, secrets_v2
    from butlers.credential_store import CredentialStore

    events = []

    class Pool:
        current = "selected version"

        @asynccontextmanager
        async def acquire(self):
            assert self is selected
            yield self

        @asynccontextmanager
        async def transaction(self):
            events.append("BEGIN")
            yield
            events.append("COMMIT")

        async def execute(self, sql, *args):
            if "UPDATE butler_secrets" in sql:
                assert args[2:] == ("cli-auth/opencode-openai", expected)
                events.append("value fence")
                return "UPDATE 1" if expected == self.current else "UPDATE 0"
            assert "INSERT INTO public.secret_probe_log" in sql
            events.append("probe log")
            return "INSERT 1"

    local, other = Pool(), Pool()
    selected = other if shared else local
    store = CredentialStore(local, fallback_pools=[other] if shared else None)

    async def audit(connection, **kwargs):
        assert connection is selected and kwargs["credential_id"] == "cli-auth/opencode-openai"
        events.append("audit")

    monkeypatch.setattr(secrets_v2, "_write_cli_audit", audit)
    for expected, recorded in (("selected version", True), ("retired version", False)):
        events.clear()
        assert (
            await cli_auth._persist_selected_test_outcome_if_current(
                store,
                provider="opencode-openai",
                key="cli-auth/opencode-openai",
                ok=True,
                detail="Fixed status result.",
                message=None,
                latency_ms=1,
                expected_store_value=expected,
            )
            is recorded
        )
        assert events == (
            ["BEGIN", "value fence", "probe log", "audit", "COMMIT"]
            if recorded
            else ["BEGIN", "value fence", "COMMIT"]
        )


@pytest.mark.parametrize("role", ["control", "dashboard"])
def test_projected_material_init_makes_immutable_exact_snapshot_and_refuses_retarget(
    role, tmp_path
):
    from butlers.session_host.launch import Refused
    from butlers.session_host.material import stage_transport
    from butlers.session_host.service_auth import _configuration

    source, output = tmp_path / "projected", tmp_path / "prepared"
    source.mkdir()
    output.mkdir(mode=0o755)
    output.chmod(0o777)  # actual emptyDir initial mode, independent of host umask/ACL
    tls = {
        "ca": "/etc/session-host/ca.crt",
        "certificate": "/etc/session-host/tls.crt",
        "private_key": "/etc/session-host/tls.key",
    }
    value = {"hostname": "butlers-control", "port": 41100, "tls": tls}
    names = ["ca.crt", "tls.crt", "tls.key"]
    if role == "control":
        names += ["host-ca.crt", "host.crt", "host.key"]
        value = {
            "tls": tls,
            "host": {
                "hostname": "session-egress",
                "port": 41300,
                "tls": {
                    "ca": "/etc/session-host/host-ca.crt",
                    "certificate": "/etc/session-host/host.crt",
                    "private_key": "/etc/session-host/host.key",
                },
            },
        }
    name = role + "-auth.json"
    data = json.dumps(value)
    for filename, content in [(name, data), *((name, "synthetic material") for name in names)]:
        underlying = tmp_path / ("projected-" + filename)
        underlying.write_text(content)
        underlying.chmod(0o400)
        (source / filename).symlink_to(underlying)
    stage_transport(role, source, output)
    assert _configuration(output / name) == value
    assert all(not path.is_symlink() for path in output.iterdir())
    assert (output / "tls.key").stat().st_mode & 0o777 == 0o600
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    stage_transport(role, source, output)  # exact repeated init only
    target = (source / "tls.key").resolve()
    target.chmod(0o600)
    target.write_text("changed synthetic material")
    target.chmod(0o400)
    with pytest.raises(Refused, match="invalid_control_config"):
        stage_transport(role, source, output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
    (output / name).unlink()
    (output / name).symlink_to(source / name)
    with pytest.raises(Refused, match="invalid_control_config"):
        _configuration(output / name)

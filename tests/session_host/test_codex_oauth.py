"""Codex OAuth mode on the session host, dark (bu-kiuh8v.16).

Software tests: they run everywhere. The native proof that the pinned CLI refreshes and
persists through the one writable file is in test_codex_adapter_proof.py (opt-in).
Every token and account here is a synthetic marker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from butlers.core.model_call import NotInvoked
from butlers.goal_runner.contracts import HostOutcome, Launch
from butlers.goal_runner.session_host_client import CodexAuthFencedHost, CodexOAuthCredentials
from butlers.goal_runner.staging import ControlSessionHost, CredentialUnavailable
from butlers.session_host.bubblewrap import BubblewrapDomain
from butlers.session_host.codex import (
    AUTH_NAME,
    AUTH_PATH,
    CONFIG_NAME,
    NATIVE_FILES,
    OAUTH,
    CodexSettings,
)
from butlers.session_host.codex_oauth import CodexOAuthVolume, oauth_account
from butlers.session_host.contracts import CredentialNotStaged, Refused
from butlers.session_host.control import LocalControl, local_command
from butlers.session_host.credential_volume import CredentialVolume
from butlers.session_host.lifecycle import InvocationOwner
from butlers.session_host.material import InvocationMaterial
from butlers.session_host.profiles import Profile, ProfileFactory
from tests.session_host import codex_proof
from tests.session_host.test_codex_adapter_proof import image
from tests.session_host.test_codex_auth import Authority, VolumeTarget
from tests.session_host.test_core import request

pytestmark = pytest.mark.unit

PROVEN_DEVICE_SETTINGS_SHA256 = "4f89ab269cadb39996f25dab1dbea1a66b6c005c648a2bd80cd873df792edc64"
DOCUMENT = codex_proof.oauth_document()


def refreshed(account: str = codex_proof.OAUTH_ACCOUNT, *, claim: str | None = None) -> bytes:
    """What the pinned CLI writes after a refresh: new tokens, the same account_id."""
    parsed = json.loads(DOCUMENT)
    parsed["tokens"].update(
        id_token=codex_proof.oauth_id_token(claim or account),
        access_token="SYNTHETIC-NEW-ACCESS",
        refresh_token="SYNTHETIC-NEW-REFRESH",
        account_id=account,
    )
    parsed["last_refresh"] = "2026-11-10T00:00:00Z"
    return json.dumps(parsed, indent=2).encode()


def sha(document: bytes) -> str:
    return hashlib.sha256(document).hexdigest()


@pytest.fixture
def volume(tmp_path) -> CredentialVolume:
    root = tmp_path / "credentials"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    volume = CredentialVolume(root)
    volume.install(DOCUMENT, expected_sha256=None)
    return volume


@pytest.fixture
def lease(volume) -> CodexOAuthVolume:
    return CodexOAuthVolume(volume)


# ── The document: one closed device-auth shape, for one account ─────────────


def test_a_device_auth_document_names_its_account():
    assert oauth_account(DOCUMENT) == codex_proof.OAUTH_ACCOUNT
    assert oauth_account(refreshed()) == codex_proof.OAUTH_ACCOUNT


def _edit(change):
    parsed = json.loads(DOCUMENT)
    change(parsed)
    return json.dumps(parsed).encode()


@pytest.mark.parametrize(
    "document",
    [
        b"",
        b"garbage",
        b"[]",
        b"{}",
        json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-synthetic"}).encode(),
        _edit(lambda d: d.update(OPENAI_API_KEY="sk-synthetic")),
        _edit(lambda d: d.update(auth_mode="apikey")),
        _edit(lambda d: d.update(extra=True)),
        _edit(lambda d: d["tokens"].update(refresh_token="")),
        _edit(lambda d: d["tokens"].pop("account_id")),
        _edit(lambda d: d["tokens"].update(account_id="../x")),
        _edit(lambda d: d["tokens"].update(extra="x")),
        _edit(lambda d: d["tokens"].update(id_token="not-a-jwt")),
        refreshed(claim="acct-somebody-else"),  # the id token is for another account
    ],
)
def test_anything_else_is_not_a_device_auth_document(document):
    assert oauth_account(document) is None


# ── The lease: stage the pushed document, collect the refresh ─────────────────


def test_the_pushed_digest_stages_the_volume_document(lease):
    staged = lease.stage(sha(DOCUMENT).encode())
    assert staged.document == DOCUMENT and staged.account == codex_proof.OAUTH_ACCOUNT
    assert DOCUMENT.decode() not in repr(staged)


@pytest.mark.parametrize(
    ("reference", "code"),
    [
        (b"sk-synthetic", "unsupported_codex_credential"),
        (sha(DOCUMENT).upper().encode(), "unsupported_codex_credential"),
        (sha(b"another").encode(), "codex_auth_stale"),
    ],
)
def test_a_launch_that_does_not_name_the_volume_document_refuses(lease, reference, code):
    with pytest.raises(Refused, match=f"^{code}$"):
        lease.stage(reference)
    lease.stage(sha(DOCUMENT).encode())  # nothing was leased by the refusal


def test_an_api_key_document_on_the_volume_is_not_staged(tmp_path):
    root = tmp_path / "v"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    volume = CredentialVolume(root)
    document = json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-synthetic"}).encode()
    volume.install(document, expected_sha256=None)
    with pytest.raises(Refused, match="^unsupported_codex_credential$"):
        CodexOAuthVolume(volume).stage(sha(document).encode())


def test_one_staged_token_at_a_time(lease):
    staged = lease.stage(sha(DOCUMENT).encode())
    with pytest.raises(Refused, match="^codex_auth_busy$"):
        lease.stage(sha(DOCUMENT).encode())
    assert lease.collect(staged, DOCUMENT) == "unchanged"
    lease.stage(sha(DOCUMENT).encode())


def test_a_refresh_for_the_same_account_is_installed_against_the_pushed_digest(lease, volume):
    staged = lease.stage(sha(DOCUMENT).encode())
    assert lease.collect(staged, refreshed()) == "installed"
    assert volume.read() == (refreshed(), sha(refreshed()))


@pytest.mark.parametrize(
    "written",
    [
        None,
        b"",
        b"garbage",
        refreshed("acct-somebody-else"),
        refreshed(claim="acct-somebody-else"),
        json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-synthetic"}).encode(),
    ],
)
def test_garbage_or_another_account_leaves_the_volume_untouched(lease, volume, written):
    staged = lease.stage(sha(DOCUMENT).encode())
    assert lease.collect(staged, written) == "rejected"
    assert volume.read() == (DOCUMENT, sha(DOCUMENT))
    lease.stage(sha(DOCUMENT).encode())  # released


def test_a_newer_document_on_the_volume_wins_over_the_refresh(lease, volume):
    staged = lease.stage(sha(DOCUMENT).encode())
    login = refreshed()  # a dashboard login landed while the runtime ran
    login = login.replace(b"SYNTHETIC-NEW-ACCESS", b"SYNTHETIC-LOGIN-ACCESS")
    assert volume.install(login, expected_sha256=sha(DOCUMENT))
    assert lease.collect(staged, refreshed()) == "conflict"
    assert volume.read() == (login, sha(login))


def test_only_the_held_lease_collects(lease):
    staged = lease.stage(sha(DOCUMENT).encode())
    lease.collect(staged, DOCUMENT)
    with pytest.raises(Refused, match="^codex_auth_lease_unavailable$"):
        lease.collect(staged, refreshed())


# ── Settings: one more closed preset, its own identity ────────────────────────


def test_oauth_settings_are_their_own_proven_identity():
    settings = CodexSettings(codex_proof.MODEL, auth=OAUTH)
    identity = settings.identity
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    assert digest == PROVEN_DEVICE_SETTINGS_SHA256
    assert identity["writable_native_files"] == [AUTH_PATH]
    assert 'forced_login_method = "chatgpt"' in identity["config_utf8"]
    api_key = CodexSettings(codex_proof.MODEL).identity
    assert "writable_native_files" not in api_key
    assert 'forced_login_method = "api"' in api_key["config_utf8"]


@pytest.mark.parametrize("auth", ["", "OAuth", "chatgpt", None])
def test_only_the_two_modes_exist(auth):
    with pytest.raises(Refused, match="^unsupported_codex_settings$"):
        CodexSettings(codex_proof.MODEL, auth=auth)  # type: ignore[arg-type]


def test_oauth_render_stages_the_document_byte_for_byte_and_refuses_an_api_key():
    req = replace(request(), runtime="codex", model=codex_proof.MODEL)
    settings = CodexSettings(codex_proof.MODEL, auth=OAUTH)
    rendered = settings.render(req.broker, DOCUMENT)
    assert rendered[AUTH_NAME] == DOCUMENT
    assert b"SYNTHETIC-STALE-REFRESH" not in rendered[CONFIG_NAME]
    for credential in (b"sk-synthetic", sha(DOCUMENT).encode(), refreshed(claim="acct-x")):
        with pytest.raises(Refused, match="^unsupported_codex_credential$"):
            settings.render(req.broker, credential)


# ── The domain: one writable file, collected only after proven settlement ─────


def _material(tmp_path, reference: bytes) -> InvocationMaterial:
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    req = replace(request(), model=codex_proof.MODEL, model_credential=reference)
    return InvocationMaterial.stage(root, req)


def _domain(tmp_path, lease) -> BubblewrapDomain:
    settings = CodexSettings(codex_proof.MODEL, auth=OAUTH)
    domain = BubblewrapDomain(
        image=image(tmp_path), command=settings.command, codex=settings, codex_oauth=lease
    )
    domain._bwrap = Path("/usr/bin/true")
    return domain


def test_oauth_needs_the_lease_and_only_oauth_may_hold_it(tmp_path, lease):
    oauth = CodexSettings(codex_proof.MODEL, auth=OAUTH)
    api_key = CodexSettings(codex_proof.MODEL)
    files = image(tmp_path)
    with pytest.raises(Refused, match="^invalid_domain_profile$"):
        BubblewrapDomain(image=files, command=oauth.command, codex=oauth)
    with pytest.raises(Refused, match="^invalid_domain_profile$"):
        BubblewrapDomain(image=files, command=api_key.command, codex=api_key, codex_oauth=lease)
    with pytest.raises(Refused, match="^invalid_domain_profile$"):
        BubblewrapDomain(
            image=files,
            command=oauth.command,
            codex=oauth,
            codex_oauth=object(),  # type: ignore[arg-type]
        )


def test_the_plan_makes_only_the_staged_auth_file_writable(tmp_path, lease):
    domain = _domain(tmp_path, lease)
    material = _material(tmp_path, sha(DOCUMENT).encode())
    try:
        argv, *_ = domain._plan(material)
        writable = [i for i, item in enumerate(argv) if item in ("--bind", "--bind-fd")]
        assert len(writable) == 1 and argv[writable[0] + 2] == AUTH_PATH
        assert not {"--bind-try", "--dev-bind", "--dev-bind-try", "--bind-data"} & set(argv)
        # The bound descriptor is the staged file itself, holding the leased document.
        descriptor = int(argv[writable[0] + 1])
        assert os.pread(descriptor, 65536, 0) == DOCUMENT
        assert os.fstat(descriptor).st_ino == (material.directory / AUTH_NAME).stat().st_ino
        for name, destination in NATIVE_FILES:
            if name == AUTH_NAME:
                continue
            at = argv.index(destination)
            assert argv[at - 4 : at - 1] == ["--perms", "0400", "--ro-bind-data"], name
        assert "--unshare-net" in argv
    finally:
        domain._close_all()
        material.discard()


def test_a_stale_launch_stages_nothing(tmp_path, lease):
    domain = _domain(tmp_path, lease)
    material = _material(tmp_path, sha(b"another").encode())
    try:
        with pytest.raises(Refused, match="^codex_auth_stale$"):
            domain._plan(material)
        assert AUTH_NAME not in material.content_hashes
    finally:
        domain._close_all()
        material.discard()


async def test_settlement_collects_the_refresh_and_frees_the_lease(tmp_path, lease, volume):
    domain = _domain(tmp_path, lease)
    material = _material(tmp_path, sha(DOCUMENT).encode())
    try:
        domain._plan(material)
        domain._close_all()
        (material.directory / AUTH_NAME).write_bytes(refreshed())  # what the runtime wrote
        assert await domain.settle() is True  # nothing spawned: settled
        assert volume.read() == (refreshed(), sha(refreshed()))
        lease.stage(sha(refreshed()).encode())  # released
    finally:
        material.discard()


async def test_garbage_written_by_the_runtime_leaves_the_volume(tmp_path, lease, volume):
    domain = _domain(tmp_path, lease)
    material = _material(tmp_path, sha(DOCUMENT).encode())
    try:
        domain._plan(material)
        domain._close_all()
        (material.directory / AUTH_NAME).write_bytes(b"x" * (70 * 1024))
        assert await domain.settle() is True
        assert volume.read() == (DOCUMENT, sha(DOCUMENT))
    finally:
        material.discard()


async def test_unproven_settlement_keeps_the_lease(tmp_path, lease, volume):
    domain = _domain(tmp_path, lease)
    material = _material(tmp_path, sha(DOCUMENT).encode())
    try:
        domain._plan(material)
        domain._close_all()
        (material.directory / AUTH_NAME).write_bytes(refreshed())

        async def unproven() -> bool:
            return False

        domain._settle_domain = unproven  # type: ignore[method-assign]
        assert await domain.settle() is False
        assert volume.read() == (DOCUMENT, sha(DOCUMENT))  # a live runtime may still write
        with pytest.raises(Refused, match="^codex_auth_busy$"):
            lease.stage(sha(DOCUMENT).encode())
    finally:
        material.discard()


# ── Profiles and the factory ────────────────────────────────────────────────


def test_an_oauth_profile_is_a_different_subject_and_needs_the_hosts_lease(tmp_path, lease):
    settings = CodexSettings(codex_proof.MODEL, auth=OAUTH)
    files = image(tmp_path)
    oauth = Profile("codex", settings.model, files, settings.command, codex_auth=OAUTH)
    api_key = Profile("codex", settings.model, files, settings.command)
    assert oauth.subject["settings_sha256"] != api_key.subject["settings_sha256"]
    assert oauth.subject["image_sha256"] == api_key.subject["image_sha256"]
    reviewed = codex_proof_reviewed(oauth)
    with pytest.raises(Refused, match="^invalid_runtime_profiles$"):
        ProfileFactory((reviewed,))
    domain = ProfileFactory((reviewed,), codex_oauth=lease)("codex", settings.model)
    assert domain._codex_oauth is lease and domain._codex is not None
    assert domain._codex.writable_auth


@pytest.mark.parametrize("runtime", ["opencode", "claude"])
def test_only_codex_has_an_oauth_mode(tmp_path, runtime):
    with pytest.raises(Refused, match="^invalid_runtime_profile$"):
        Profile(runtime, "gpt-6-sol", image(tmp_path), ("/runtime/codex",), codex_auth=OAUTH)


def codex_proof_reviewed(profile: Profile):
    """A ReviewedProfile over synthetic all-pass bytes: admission parsing only."""
    from butlers.session_host.profiles import ReviewedProfile

    checks = {check: "pass" for check in (*codex_proof.CHECKED, *codex_proof.UNPROVEN)}
    raw = json.dumps(
        {
            "version": 1,
            "subject": profile.subject,
            "checks": checks,
            "execution_sha256": "0" * 64,
            "artifact_sha256": "1" * 64,
        }
    ).encode()
    return ReviewedProfile(profile, report=raw, expected_sha256=hashlib.sha256(raw).hexdigest())


# ── The control plane: the launch names the pushed document ───────────────────


class Recording:
    """The wrapped host: records the credential each launch would stage."""

    def __init__(self, credentials: CodexOAuthCredentials):
        self.credentials = credentials
        self.staged: list[bytes] = []

    async def launch(self, launch: Launch) -> HostOutcome:
        self.staged.append(await self.credentials.resolve(launch.runtime, launch.model))
        return HostOutcome()

    async def terminate(self, invocation_id):
        pass


class Base:
    async def resolve(self, runtime: str, model: str) -> bytes:
        return b"sk-synthetic"


def launch(runtime: str = "codex") -> Launch:
    from uuid import uuid4

    return Launch(uuid4(), uuid4(), "t" * 43, runtime, codex_proof.MODEL, "prompt", 30)


async def test_a_fenced_launch_carries_the_pushed_digest_not_a_secret(volume):
    credentials = CodexOAuthCredentials(Base(), runtimes={"codex"})
    inner = Recording(credentials)
    authority = Authority(DOCUMENT.decode())
    host = CodexAuthFencedHost(inner, authority, VolumeTarget(volume), runtimes={"codex"})
    await host.launch(launch())
    assert inner.staged == [sha(DOCUMENT).encode()]
    # The digest names that one launch: after it, the caller holds no codex credential.
    with pytest.raises(CredentialUnavailable, match="^codex_auth_unavailable$"):
        await credentials.resolve("codex", codex_proof.MODEL)
    await host.launch(launch("opencode"))
    assert inner.staged[-1] == b"sk-synthetic"  # other runtimes resolve as before


async def test_outside_the_fence_there_is_no_codex_credential():
    credentials = CodexOAuthCredentials(Base(), runtimes={"codex"})
    with pytest.raises(CredentialUnavailable, match="^codex_auth_unavailable$"):
        await credentials.resolve("codex", codex_proof.MODEL)
    assert await credentials.resolve("opencode", "m") == b"sk-synthetic"


async def test_the_digest_does_not_leak_into_a_concurrent_unfenced_resolve(volume):
    credentials = CodexOAuthCredentials(Base(), runtimes={"codex"})
    seen: list[object] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    class Slow(Recording):
        async def launch(self, launch: Launch) -> HostOutcome:
            entered.set()
            await release.wait()
            return await super().launch(launch)

    host = CodexAuthFencedHost(
        Slow(credentials), Authority(DOCUMENT.decode()), VolumeTarget(volume), runtimes={"codex"}
    )
    fenced = asyncio.create_task(host.launch(launch()))
    await entered.wait()
    try:
        await credentials.resolve("codex", codex_proof.MODEL)
    except CredentialUnavailable as refusal:
        seen.append(str(refusal))
    release.set()
    await fenced
    assert seen == ["codex_auth_unavailable"]


# ── A lease the host cannot take: settled before spawn, so not-invoked ─────────


async def test_a_login_between_push_and_stage_is_not_invoked(tmp_path, pki, volume, lease):
    """Stale between push and stage: the runner gets NotInvoked, never runtime_failed."""
    stage = tmp_path / "owner"
    stage.mkdir(mode=0o700)
    stage.chmod(0o700)
    server, client, pin = pki
    owner = InvocationOwner(stage)
    domains: list[BubblewrapDomain] = []

    def factory(runtime: str, model: str) -> BubblewrapDomain:
        domains.append(_domain(tmp_path, lease))
        return domains[-1]

    host = LocalControl(
        owner=owner,
        factory=factory,
        **server(),
        caller_sha256=pin("runner"),
        server_sha256=pin("server"),
        broker_origin="https://broker.test",
    )
    port = await host.start()

    async def send(command: dict) -> dict:
        # Another login lands on the volume after the fence pushed and named DOCUMENT.
        assert volume.install(refreshed(), expected_sha256=sha(DOCUMENT))
        return await local_command(
            port,
            context=client("runner"),
            server_hostname="host.test",
            server_sha256=pin("server"),
            command=command,
        )

    credentials = CodexOAuthCredentials(Base(), runtimes={"codex"})
    runner = ControlSessionHost(send, credentials, broker_origin="https://broker.test")
    fenced = CodexAuthFencedHost(
        runner, Authority(DOCUMENT.decode()), VolumeTarget(volume), runtimes={"codex"}
    )
    order = Launch(uuid4(), uuid4(), "A" * 43, "codex", codex_proof.MODEL, "prompt", 30)
    try:
        with pytest.raises(NotInvoked, match="^session_host_credential_unavailable$"):
            await fenced.launch(order)
        assert len(domains) == 1 and domains[0]._spawn is None  # no runtime started
        assert owner.healthy  # settled and discarded, not quarantined
        assert volume.read() == (refreshed(), sha(refreshed()))  # the new login is untouched
        lease.stage(sha(refreshed()).encode())  # and no lease was left behind
    finally:
        await host.close()


async def test_a_busy_lease_is_not_invoked_either(tmp_path, lease):
    stage = tmp_path / "owner"
    stage.mkdir(mode=0o700)
    stage.chmod(0o700)
    owner = InvocationOwner(stage)
    lease.stage(sha(DOCUMENT).encode())  # another launch holds the one token
    req = replace(request(), model=codex_proof.MODEL, model_credential=sha(DOCUMENT).encode())
    with pytest.raises(CredentialNotStaged, match="^credential_unavailable$"):
        await owner.run(req, lambda: _domain(tmp_path, lease))
    assert owner.healthy


async def test_a_refusal_after_spawn_is_never_called_not_invoked(tmp_path, lease):
    """Only a pre-spawn refusal may claim nothing ran."""
    domain = _domain(tmp_path, lease)
    material = _material(tmp_path, sha(DOCUMENT).encode())

    def planned(_material):
        domain._spawn = asyncio.get_running_loop().create_future()  # as if spawned
        raise CredentialNotStaged("codex_auth_stale")

    domain._plan = planned  # type: ignore[method-assign]
    try:
        with pytest.raises(Refused, match="^sandbox_execution_failed$") as refused:
            await domain.execute(material)
        assert not isinstance(refused.value, CredentialNotStaged)
    finally:
        domain._spawn = None
        material.discard()


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"status": "refused", "reason": "credential_unavailable"}, "not_staged"),
        ({"status": "refused", "reason": "host_busy"}, "busy"),
        ({"status": "refused", "reason": "something_else"}, "session_host_malformed_reply"),
        ({"status": "refused"}, "session_host_refused"),
    ],
)
def test_the_runner_reads_only_the_closed_refusal_reasons(reply, expected):
    from butlers.goal_runner import staging

    outcome = staging._reply_outcome(reply)
    if expected == "not_staged":
        assert outcome is staging._NOT_STAGED
    elif expected == "busy":
        assert outcome is staging._BUSY
    else:
        assert str(outcome.failure) == expected

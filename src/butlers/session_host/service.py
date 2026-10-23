"""Default-off session-host construction and immutable adapter evidence loading.

No startup path produces eligibility evidence. Operators install independently
reviewed, exact-image conformance artifacts; absence or mutation refuses startup.
The host has no database, secret-store resolver, or ambient model authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from butlers.cli_auth.sandbox_platform import BubblewrapDashboardCLIAuthSandbox
from butlers.session_host.control import ControlService, TLSFiles, _unique_object
from butlers.session_host.launch import _PROFILE, LaunchRequest, Refused, SessionHost, TupleProof
from butlers.session_host.model_function import (
    FUNCTION_PROFILE,
    ModelFunctionHost,
    ModelFunctionRequest,
)
from butlers.session_host.sandbox import BubblewrapSessionSandbox

_CHECKS = {
    "builtin_tools": "denied",
    "mcp": "broker-only",
    "provider_hosted_tools": "absent",
    "filesystem": "isolated",
    "processes": "isolated",
    "descendants": "dead",
    "egress": "allowlist-only",
    "catalog_conformance": "passed",
}
_PROOF_KEYS = {
    "runtime",
    "model",
    "provider",
    "executable_sha256",
    "evidence_sha256",
    "profile_sha256",
}
HOST_STAGE_ROOT = Path("/tmp/session-host-stage")


def _immutable(path: Path, *, expected_uid: int, projected: bool = False) -> bytes:
    """Bounded regular-file read; only init may follow a trusted projection."""
    descriptor = None
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
        if not projected:
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_uid
            or stat.S_IMODE(before.st_mode) != 0o444
            or before.st_nlink != 1
            or not 0 < before.st_size <= 65536
        ):
            raise ValueError
        content = os.read(descriptor, 65537)
        after = os.fstat(descriptor)
        if (before.st_ino, before.st_dev, before.st_mtime_ns, before.st_size) != (
            after.st_ino,
            after.st_dev,
            after.st_mtime_ns,
            after.st_size,
        ) or len(content) != before.st_size:
            raise ValueError
        return content
    except (OSError, ValueError):
        raise Refused("runtime_tuple_unproven") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_proofs(path: Path, *, expected_uid: int = 0) -> tuple[TupleProof, ...]:
    """Read an immutable evidence index, not caller assertions or runtime flags.

    ``expected_uid`` exists for unprivileged fixture ownership checks. The
    production factory always requires UID 0 and exposes no configurable UID.
    Actual conformance jobs must install the artifact and retain native proof;
    a synthetic artifact in a unit test is only a loader test.
    """
    try:
        index = json.loads(
            _immutable(path, expected_uid=expected_uid), object_pairs_hook=_unique_object
        )
        if (
            type(index) is not dict
            or set(index) != {"version", "tuples"}
            or type(index["version"]) is not int
            or index["version"] != 1
            or type(index["tuples"]) is not list
            or not 0 <= len(index["tuples"]) <= 64
        ):
            raise ValueError
        proofs, seen = [], set()
        for row in index["tuples"]:
            if type(row) is not dict or set(row) != _PROOF_KEYS:
                raise ValueError
            proof = TupleProof(**row)
            if (
                (proof.runtime, proof.profile_sha256)
                not in {("codex", _PROFILE), ("opencode", FUNCTION_PROFILE)}
                or proof.provider != "openai"
                or type(proof.model) is not str
                or not proof.model
            ):
                raise ValueError
            identity = (proof.runtime, proof.model, proof.provider)
            if identity in seen:
                raise ValueError
            seen.add(identity)
            content = _immutable(
                path.parent / f"{proof.evidence_sha256}.json", expected_uid=expected_uid
            )
            if hashlib.sha256(content).hexdigest() != proof.evidence_sha256:
                raise ValueError
            evidence = json.loads(content, object_pairs_hook=_unique_object)
            expected = {key: row[key] for key in _PROOF_KEYS - {"evidence_sha256"}}
            checks = (
                _CHECKS
                if proof.runtime == "codex"
                else {
                    **{
                        key: value for key, value in _CHECKS.items() if key != "catalog_conformance"
                    },
                    "mcp": "none",
                    "egress": "provider-only",
                    "single_turn": "passed",
                }
            )
            expected.update(protocol="session-adapter-proof-v1", checks=checks)
            if evidence != expected:
                raise ValueError
            proofs.append(proof)
        return tuple(proofs)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Refused("runtime_tuple_unproven") from None


@dataclass(frozen=True)
class HostConfig:
    enabled: bool = False
    listen_host: str = "0.0.0.0"
    port: int = 41299
    binary: Path | None = None
    broker_url: str | None = None
    proof_index: Path | None = None
    tls: TLSFiles | None = None
    model_function_binary: Path | None = None
    cli_auth_enabled: bool = False
    credential_volume: Path | None = None


@dataclass
class RunningHost:
    server: asyncio.Server
    consumer: ControlService

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        await self.consumer.close()


async def start_host(config: HostConfig) -> RunningHost | None:
    """OFF performs no file access, platform construction, worker or socket IO."""
    if type(config) is not HostConfig or type(config.enabled) is not bool:
        raise Refused("invalid_host_config")
    if not config.enabled:
        return None
    if (
        type(config.binary) is not Path
        and not isinstance(config.binary, Path)
        or not isinstance(config.proof_index, Path)
        or type(config.broker_url) is not str
        or type(config.tls) is not TLSFiles
        or type(config.port) is not int
        or not 0 < config.port < 65536
        or type(config.listen_host) is not str
        or (
            config.model_function_binary is not None
            and not isinstance(config.model_function_binary, Path)
        )
        or type(config.cli_auth_enabled) is not bool
        or (config.cli_auth_enabled and not isinstance(config.credential_volume, Path))
    ):
        raise Refused("invalid_host_config")
    proofs = await asyncio.to_thread(load_proofs, config.proof_index)
    context = config.tls.server_context()
    # One host-wide lease owner spans every profile, including quarantined
    # goal/function domains and writable CLI-login domains. No global pool or
    # second platform may independently recycle one of these outer UIDs.
    platform = BubblewrapDashboardCLIAuthSandbox(stage_root=HOST_STAGE_ROOT)
    sandbox = BubblewrapSessionSandbox(platform)
    host = SessionHost(
        binary=config.binary,
        broker_url=config.broker_url,
        proofs=proofs,
        sandbox=sandbox,
    )
    functions = ModelFunctionHost(
        binary=config.model_function_binary, proofs=proofs, sandbox=sandbox
    )

    class Profiles:
        async def launch(self, request):
            if type(request) is ModelFunctionRequest:
                return await functions.launch(request)
            if type(request) is LaunchRequest:
                return await host.launch(request)
            raise Refused("invalid_launch")

    auth_service = None
    if config.cli_auth_enabled:
        from butlers.session_host.auth import AuthService
        from butlers.session_host.credential_volume import (
            CodexCredentialVolume,
            OpenCodeCredentialVolume,
        )

        auth_service = AuthService(
            platform,
            volume=CodexCredentialVolume(config.credential_volume),
            opencode_volume=OpenCodeCredentialVolume(config.credential_volume),
        )
    consumer = ControlService(Profiles(), auth_service=auth_service)
    server = await asyncio.start_server(
        consumer.handle,
        config.listen_host,
        config.port,
        ssl=context,
        ssl_handshake_timeout=5,
        ssl_shutdown_timeout=2,
        limit=262148,
    )
    return RunningHost(server, consumer)


def prepare_auth_volume(root: Path, *, expected_uid: int = 0):
    """Init-only preparation of the isolated, empty credential volume.

    Fresh mounted directories may start with mode 0755 or 0777. Tighten only that empty,
    owned directory, never an existing credential tree, symlink or mount
    supplied by a request. The host's runtime volume validator stays strict.
    """
    descriptor = None
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if metadata.st_uid != expected_uid:
            raise ValueError
        mode = stat.S_IMODE(metadata.st_mode)
        if mode == 0o700:
            return
        if mode not in {0o755, 0o777} or os.listdir(descriptor):
            raise ValueError
        os.fchmod(descriptor, 0o700)
    except (OSError, ValueError):
        raise Refused("credential_volume_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def stage_proofs(source: Path, destination: Path, *, expected_uid: int = 0):
    """Init-only snapshot of trusted ConfigMap files into real immutable files.

    Kubelet projects ConfigMap keys as symlinks. The runtime deliberately refuses
    such paths; this separate root init container copies only bounded JSON files
    to a fresh volume, which the running host mounts READ-ONLY. It generates no
    conformance evidence and never grants eligibility to a mutable runtime file.
    ``expected_uid`` is fixture-only, like load_proofs; the init CLI never
    accepts an ownership override and always requires root-owned output.
    """
    created = []
    try:
        # The source is an operator-owned ConfigMap projection, not model input.
        index_bytes = _immutable(source / "index.json", expected_uid=expected_uid, projected=True)
        if not 0 < len(index_bytes) <= 65536:
            raise ValueError
        index = json.loads(index_bytes, object_pairs_hook=_unique_object)
        rows = index["tuples"]
        if type(rows) is not list or not 0 <= len(rows) <= 64:
            raise ValueError
        names = {"index.json": index_bytes}
        for row in rows:
            digest = row["evidence_sha256"]
            if (
                type(digest) is not str
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ValueError
            name = f"{digest}.json"
            content = _immutable(source / name, expected_uid=expected_uid, projected=True)
            if not 0 < len(content) <= 65536 or hashlib.sha256(content).hexdigest() != digest:
                raise ValueError
            names[name] = content
        for name, content in names.items():
            path = destination / name
            fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o444
            )
            created.append(path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o444)
        load_proofs(destination / "index.json", expected_uid=expected_uid)
    except BaseException as exc:
        for path in created:
            path.unlink(missing_ok=True)
        if not isinstance(exc, Exception):
            raise
        raise Refused("runtime_tuple_unproven") from None

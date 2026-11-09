"""The control plane's session-host clients, built from mounted PKI (bu-kiuh8v.11; RFC 0038 D3).

Dark: nothing on a live path calls :func:`session_host_clients` or builds a
:class:`CodexAuthFencedHost`; ``tests/session_host/test_codex_auth.py`` pins that.
Calling either is the switch (``bu-atc3xi``).

* :func:`session_host_clients` binds :func:`butlers.session_host.control.local_command`
  to one host: the dialed address and port, the mTLS client identity a deployment
  mounts as three files (CA, leaf, private key), and the host's exact leaf pin. It
  returns the runner's :class:`~butlers.goal_runner.staging.ControlSessionHost` and
  the credential volume's :class:`~butlers.session_host.codex_auth.SessionHostCodexAuth`
  over that one channel. Every refusal is the fixed code
  ``session_host_client_unavailable``; no path or file byte appears in it.
* :class:`CodexAuthFencedHost` wraps the runner's host for the runtimes that
  authenticate from the volume (Codex device auth): the authority's document is
  pushed before each launch and a rotation the runtime made is collected after it,
  under the previous push's fence, so a single-use refresh token is never rolled
  back. Those launches are serialized: two runs on one document would spend the
  same refresh token twice.

Production CA/workload identity and key rotation remain their own gate; this takes
whatever a trusted deployment mounts.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import ssl
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from butlers.session_host.codex_auth import (
    CodexAuthTarget,
    PushedAuthority,
    SessionHostCodexAuth,
    collect_codex_rotation,
    push_codex_authority,
)
from butlers.session_host.contracts import Refused
from butlers.session_host.control import local_command

from .contracts import HostOutcome, Launch, SessionHost
from .staging import ControlSessionHost, CredentialUnavailable, ModelCredentials

logger = logging.getLogger(__name__)

_PEM_BYTES = 64 * 1024
_NAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_PIN = re.compile(r"[0-9a-f]{64}")


def _refused() -> Refused:
    return Refused("session_host_client_unavailable")


@dataclass(frozen=True, slots=True)
class SessionHostClientConfig:
    """Where the session host listens and the identity the control plane presents.

    Trusted deployment configuration only: paths to mounted files, never their
    contents. ``server_sha256`` is the host's exact leaf pin, distributed out of band.
    """

    host: str
    port: int
    server_hostname: str
    server_sha256: str
    ca_path: Path
    certificate_path: Path
    key_path: Path


@dataclass(frozen=True, slots=True)
class SessionHostClients:
    """Both control-plane clients of one host, over one pinned channel."""

    host: ControlSessionHost
    codex_auth: SessionHostCodexAuth


def _read(path: Path, *, private: bool) -> tuple[int, bytes]:
    """Open one mounted file (a secret mount's links are followed); bounded and regular.

    Returns the open descriptor too, so the TLS library loads the very file checked.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        raise _refused() from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _PEM_BYTES:
            raise _refused()
        if private and info.st_mode & 0o077:
            raise _refused()  # a key others can read is not a private identity
        data = os.pread(fd, _PEM_BYTES + 1, 0)
        if len(data) > _PEM_BYTES:
            raise _refused()
    except BaseException:
        os.close(fd)
        raise
    return fd, data


def client_context(config: SessionHostClientConfig) -> ssl.SSLContext:
    """TLS 1.3 client context: the mounted CA as the only trust root, and the leaf+key."""
    opened: list[int] = []
    try:
        ca_fd, ca = _read(config.ca_path, private=False)
        opened.append(ca_fd)
        cert_fd, certificate = _read(config.certificate_path, private=False)
        opened.append(cert_fd)
        key_fd, _ = _read(config.key_path, private=True)
        opened.append(key_fd)
        try:
            leaf = ssl.PEM_cert_to_DER_cert(certificate.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            raise _refused() from None
        if hashlib.sha256(leaf).hexdigest() == config.server_sha256:
            raise _refused()  # the host's identity must not be the caller's
        try:
            context = ssl.create_default_context(cadata=ca.decode("ascii"))
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.load_cert_chain(f"/proc/self/fd/{cert_fd}", f"/proc/self/fd/{key_fd}")
        except (ssl.SSLError, ValueError, OSError, UnicodeDecodeError):
            raise _refused() from None
        return context
    finally:
        for fd in opened:
            os.close(fd)


def _checked(config: SessionHostClientConfig) -> None:
    if (
        type(config.host) is not str
        or _NAME.fullmatch(config.host) is None
        or type(config.server_hostname) is not str
        or _NAME.fullmatch(config.server_hostname) is None
        or type(config.port) is not int
        or not 0 < config.port < 65536
        or type(config.server_sha256) is not str
        or _PIN.fullmatch(config.server_sha256) is None
    ):
        raise _refused()


def session_host_clients(
    config: SessionHostClientConfig, credentials: ModelCredentials, *, broker_origin: str
) -> SessionHostClients:
    """Bind both clients to the configured host and identity; refuse anything unsound."""
    _checked(config)
    context = client_context(config)

    async def send(command: dict) -> Any:
        return await local_command(
            config.port,
            context=context,
            server_hostname=config.server_hostname,
            server_sha256=config.server_sha256,
            command=command,
            host=config.host,
        )

    return SessionHostClients(
        host=ControlSessionHost(send, credentials, broker_origin=broker_origin),
        codex_auth=SessionHostCodexAuth(send),
    )


class CodexAuthFencedHost:
    """A ``SessionHost`` that keeps the volume's Codex document in step around launches.

    For a launch whose runtime is in ``runtimes``: push the authority's document
    (fenced by the last push), refuse the launch as ``NotInvoked`` when that fails
    closed, run it, then collect any rotation, whether it succeeded or not. Other
    runtimes pass straight through. A rotation a collect misses (a cancelled
    collect, a lost reply) is flushed by the next push, under the same fence.
    """

    def __init__(
        self,
        host: SessionHost,
        authority: Any,
        target: CodexAuthTarget,
        *,
        runtimes: Iterable[str],
    ) -> None:
        self._host = host
        self._authority = authority
        self._target = target
        self._runtimes = frozenset(runtimes)
        self._fence: PushedAuthority | None = None
        self._lock = asyncio.Lock()

    async def launch(self, launch: Launch) -> HostOutcome:
        if launch.runtime not in self._runtimes:
            return await self._host.launch(launch)
        async with self._lock:
            pushed = await push_codex_authority(self._authority, self._target, self._fence)
            if not pushed.known:
                raise CredentialUnavailable("codex_auth_unavailable")
            self._fence = pushed
            try:
                return await self._host.launch(launch)
            finally:
                with contextlib.suppress(Exception):
                    await collect_codex_rotation(self._authority, self._target, pushed)

    async def terminate(self, invocation_id: UUID) -> None:
        await self._host.terminate(invocation_id)


__all__ = [
    "CodexAuthFencedHost",
    "SessionHostClientConfig",
    "SessionHostClients",
    "client_context",
    "session_host_clients",
]

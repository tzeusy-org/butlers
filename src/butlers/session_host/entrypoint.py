"""The session-host process: one ``LocalControl`` built from mounted material (bu-kiuh8v.15).

Dark. ``butlers session-host`` runs this; Compose and Helm define it off by default
(the ``session-host`` profile, ``sessionHost.enabled=false``). It installs no reviewed
runtime profile, so every launch refuses; it serves only the credential volume's
operations and revokes, over mTLS with the pinned caller.

Everything comes from the trusted deployment, never a request:

* ``pki_dir`` holds ``tls.crt`` and ``tls.key`` (the host's leaf and key; the key
  readable by its owner only), ``client-ca.crt`` (the only CA a caller may chain to)
  and two pins, ``server.sha256`` (must be ``tls.crt``'s) and ``caller.sha256`` (the
  control plane's leaf). Each file is regular and bounded; a secret mount's links are
  followed.
* ``staging_root`` must be absent or an empty directory: leftover material is never
  adopted (see the restart policy in ``docs/runtime/session-host.md``).
* ``credentials_root``, when set, is the credential volume's ``0700`` directory,
  created inside its mount if absent.
* ``bind_host``/``port`` default to loopback. The session-host pod binds its pod
  address; there mTLS with the pinned caller and the network policy (only
  ``butlers-control`` may connect) both apply.

This module imports neither the butler daemon nor the goal runner.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import stat
from dataclasses import dataclass
from pathlib import Path

from .contracts import Refused
from .control import LocalControl
from .credential_volume import CredentialVolume
from .lifecycle import InvocationOwner
from .profiles import ProfileFactory

logger = logging.getLogger(__name__)

_PEM_BYTES = 64 * 1024
_PIN_BYTES = 128
_PIN = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True, slots=True)
class SessionHostServeConfig:
    """The host owner's choices; paths are mounted files and directories."""

    pki_dir: Path
    staging_root: Path
    broker_origin: str
    credentials_root: Path | None = None
    bind_host: str = "127.0.0.1"
    port: int = 0
    max_concurrent: int = 4


def _pki() -> Refused:
    return Refused("session_host_pki_unavailable")


def _read(path: Path, limit: int, *, private: bool = False) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        raise _pki() from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise _pki()
        if private and info.st_mode & 0o077:
            raise _pki()
        data = os.pread(fd, limit + 1, 0)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise _pki()
    return data


def _pin(path: Path) -> str:
    try:
        value = _read(path, _PIN_BYTES).decode("ascii").strip()
    except UnicodeDecodeError:
        raise _pki() from None
    if _PIN.fullmatch(value) is None:
        raise _pki()
    return value.lower()


def _private_dir(path: Path, *, empty: bool) -> Path:
    """A ``0700`` directory, created if absent; an existing one is checked, not adopted."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        raise Refused("session_host_storage_unavailable") from None
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise Refused("session_host_storage_unavailable")
        if empty and any(path.iterdir()):
            raise Refused("session_host_storage_unavailable")
        path.chmod(0o700)
    except OSError:
        raise Refused("session_host_storage_unavailable") from None
    return path


def build(config: SessionHostServeConfig) -> LocalControl:
    """Construct the host from mounted material; nothing listens until ``start``."""
    pki = Path(config.pki_dir)
    certificate = _read(pki / "tls.crt", _PEM_BYTES)
    key = _read(pki / "tls.key", _PEM_BYTES, private=True)
    client_ca = _read(pki / "client-ca.crt", _PEM_BYTES)
    server_pin, caller_pin = _pin(pki / "server.sha256"), _pin(pki / "caller.sha256")
    staging = _private_dir(Path(config.staging_root), empty=True)
    credentials = None
    if config.credentials_root is not None:
        credentials = CredentialVolume(_private_dir(Path(config.credentials_root), empty=False))
    return LocalControl(
        owner=InvocationOwner(staging),
        factory=ProfileFactory(),  # no reviewed tuple: every launch refuses
        certificate_pem=certificate,
        private_key_pem=key,
        client_ca_pem=client_ca,
        caller_sha256=caller_pin,
        server_sha256=server_pin,
        broker_origin=config.broker_origin,
        credentials=credentials,
        max_concurrent=config.max_concurrent,
        bind_host=config.bind_host,
        bind_port=config.port,
    )


async def serve(config: SessionHostServeConfig, stop: asyncio.Event) -> None:
    """Listen until ``stop`` is set, then settle every invocation and close."""
    control = build(config)
    port = await control.start()
    logger.info("session host listening port=%d", port)
    try:
        await stop.wait()
    finally:
        await control.close()
        logger.info("session host stopped")


async def run(config: SessionHostServeConfig) -> None:
    """``serve`` until SIGTERM or SIGINT."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await serve(config, stop)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


__all__ = ["SessionHostServeConfig", "build", "run", "serve"]

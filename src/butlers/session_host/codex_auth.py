"""Codex auth sync and dashboard login, retargeted to the session host (dark).

bu-kiuh8v.3, RFC 0038 D3. With the session host on, the control plane keeps the
system-global ``cli-auth/codex`` authority it owns today, and the session host's
credential volume replaces butlers-up's ``~/.codex/auth.json`` as the runtime
copy. The control plane reaches it only through the authenticated control
channel (:class:`SessionHostCodexAuth`), never a dashboard-facing port.

The two sync steps keep today's fencing (``_codex_auth_sync``):

* :func:`push_codex_authority` installs the authority's exact document before a
  runtime needs it, by compare-and-set on the volume, and fails closed (no
  fallback, no blanking) when the authority is missing, invalid or unreachable.
  Fenced by the previous push, it first flushes an uncollected host rotation
  to the authority, so a single-use refresh token is never rolled back.
* :func:`collect_codex_rotation` stores a document the runtime refreshed on the
  host back to the authority, only if the authority still holds the value that
  was pushed, so a newer dashboard login always wins.

Nothing outside this package builds a target while the session host is dark;
``tests/session_host/test_codex_auth.py`` pins that.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from butlers.core.runtimes._codex_auth_sync import (
    _load_authoritative_codex_auth,
    _read_valid_auth_value,
    _store_rotation_if_authority_unchanged,
)

from .contracts import Refused
from .credential_volume import valid_document

logger = logging.getLogger(__name__)

_DIGEST = re.compile(r"[0-9a-f]{64}")
Send = Callable[[dict], Awaitable[Any]]


class CodexAuthTarget(Protocol):
    """Where the runtime copy of the Codex document lives."""

    async def status(self) -> str | None: ...

    async def install(self, document: str, expected_sha256: str | None) -> bool: ...

    async def read(self) -> tuple[str, str] | None: ...


def _refused() -> Refused:
    return Refused("session_host_auth_unavailable")


def _digest(reply: dict) -> str:
    value = reply.get("sha256")
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _refused()
    return value


class SessionHostCodexAuth:
    """The control plane's client for the session host's credential volume.

    ``send`` delivers one command over the pinned mTLS control channel
    (:func:`butlers.session_host.control.local_command` bound to the host's
    port, context and pin by trusted construction). Every reply outside the
    fixed contract refuses, so a confused or hostile host cannot make the
    control plane store something it did not return as a valid document.
    """

    def __init__(self, send: Send) -> None:
        self._send = send

    async def _call(self, command: dict, allowed: dict[str, set[str]]) -> dict:
        reply = await self._send({"version": 1, **command})
        if (
            type(reply) is not dict
            or reply.get("status") not in allowed
            or set(reply) != allowed[reply["status"]]
        ):
            raise _refused()
        return reply

    async def status(self) -> str | None:
        reply = await self._call(
            {"operation": "codex_auth_status"},
            {"absent": {"status"}, "present": {"status", "sha256"}},
        )
        return None if reply["status"] == "absent" else _digest(reply)

    async def install(self, document: str, expected_sha256: str | None) -> bool:
        reply = await self._call(
            {
                "operation": "codex_auth_install",
                "document": base64.b64encode(document.encode()).decode(),
                "expected_sha256": expected_sha256,
            },
            {"installed": {"status"}, "conflict": {"status"}},
        )
        return reply["status"] == "installed"

    async def read(self) -> tuple[str, str] | None:
        reply = await self._call(
            {"operation": "codex_auth_read"},
            {"absent": {"status"}, "present": {"status", "document", "sha256"}},
        )
        if reply["status"] == "absent":
            return None
        digest = _digest(reply)
        try:
            document = base64.b64decode(reply["document"], validate=True)
        except (ValueError, TypeError):
            raise _refused() from None
        if hashlib.sha256(document).hexdigest() != digest or not valid_document(document):
            raise _refused()
        return document.decode(), digest


@dataclass(frozen=True)
class PushedAuthority:
    """What one push established: the fence a later push or rotation must match.

    The caller keeps the latest one and hands it to the next
    :func:`push_codex_authority`. ``authority_value`` is credential material
    and stays out of ``repr``.
    """

    known: bool
    authority_value: str | None = field(default=None, repr=False)
    installed_sha256: str | None = None


_UNKNOWN = PushedAuthority(known=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def push_codex_authority(
    authority: Any, target: CodexAuthTarget, previous: PushedAuthority | None = None
) -> PushedAuthority:
    """Make the host hold the authority's document without losing a rotation.

    Codex refresh tokens are single-use, so a rotation the runtime made on the
    host must never be overwritten by the document it replaced. ``previous``
    is the fence from the last push. When the host holds a document other
    than that push's, it is a rotation nobody collected: it is stored into the
    authority first, conditional on the authority still holding what was
    pushed, as today's local reconcile flushes a rotated ``auth.json``. Only
    if that conditional write loses (a newer login exists) does the authority
    replace it. Without a fence an unknown host document cannot be judged,
    so the push fails closed and changes nothing.
    """
    try:
        value = await _load_authoritative_codex_auth(authority)
    except Exception:
        logger.warning("session host codex auth: authority unavailable; volume left unchanged")
        return _UNKNOWN
    if value is None or _read_valid_auth_value(value) is None:
        logger.warning("session host codex auth: no valid authority; volume left unchanged")
        return _UNKNOWN
    try:
        current = await target.status()
        if current == _sha(value):
            return PushedAuthority(known=True, authority_value=value, installed_sha256=current)
        if current is not None:
            if previous is None or not previous.known or previous.authority_value is None:
                logger.warning("session host codex auth: unfenced host document; left unchanged")
                return _UNKNOWN
            if current != previous.installed_sha256:
                found = await target.read()
                if found is None or found[1] != current:
                    logger.warning("session host codex auth: volume moved during flush")
                    return _UNKNOWN
                rotated = found[0]
                if _read_valid_auth_value(rotated) is None:
                    return _UNKNOWN
                if await _store_rotation_if_authority_unchanged(
                    authority,
                    expected_store_value=previous.authority_value,
                    rotated_value=rotated,
                ):
                    logger.info("session host codex auth: host rotation persisted before launch")
                    return PushedAuthority(
                        known=True, authority_value=rotated, installed_sha256=current
                    )
                # A newer login won; its document replaces the stale lineage.
                value = await _load_authoritative_codex_auth(authority)
                if value is None or _read_valid_auth_value(value) is None:
                    return _UNKNOWN
                if current == _sha(value):
                    return PushedAuthority(
                        known=True, authority_value=value, installed_sha256=current
                    )
        if not await target.install(value, current):
            logger.warning("session host codex auth: volume changed during install")
            return _UNKNOWN
    except Exception:
        logger.warning("session host codex auth: push failed safely")
        return _UNKNOWN
    return PushedAuthority(known=True, authority_value=value, installed_sha256=_sha(value))


async def collect_codex_rotation(
    authority: Any, target: CodexAuthTarget, pushed: PushedAuthority
) -> bool:
    """Store a rotation the runtime made on the host, fenced to ``pushed``."""
    if not pushed.known or pushed.authority_value is None:
        return False
    try:
        found = await target.read()
    except Exception:
        logger.warning("session host codex auth: rotation read failed safely")
        return False
    if found is None or found[1] == pushed.installed_sha256:
        return False
    document = found[0]
    if _read_valid_auth_value(document) is None:
        return False
    try:
        stored = await _store_rotation_if_authority_unchanged(
            authority, expected_store_value=pushed.authority_value, rotated_value=document
        )
    except Exception:
        logger.warning("session host codex auth: rotation write failed safely")
        return False
    if not stored:
        logger.info("session host codex auth: authority moved on; rotation not stored")
    return bool(stored)


async def install_login(target: CodexAuthTarget, document: str) -> bool:
    """Put a just-committed dashboard login on the host, replacing what it holds."""
    try:
        return await target.install(document, await target.status())
    except Exception:
        logger.warning("session host codex auth: login install failed safely")
        return False

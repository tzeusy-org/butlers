"""Per-invocation model credential staging over the session-host control channel (RFC 0038 D3).

The goal runner, which may read the credential store, resolves each launch's
model credential at launch time and stages exactly that one credential with the
launch; the session host never reads the store or the database. A goal session
therefore holds only its resolved model's credential, for its own invocation.

``ControlSessionHost`` is the runner side of the session host's ``launch`` and
``revoke`` commands (``butlers.session_host.control``). ``send`` delivers one
command over the pinned mTLS control channel; trusted construction binds it to
the host's port, TLS context and pin. Nothing here logs a credential, token or
prompt, and no reply field reaches a log.

Dark: nothing builds a ``ControlSessionHost`` until the session host is
deployed (bu-kiuh8v).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from butlers.core.model_call import NotInvoked

from .contracts import HostOutcome, Launch

logger = logging.getLogger(__name__)

Send = Callable[[dict], Awaitable[Any]]

# The reviewed session-host profiles (``butlers.session_host.profiles``) both
# authenticate with an OpenAI API key; a runtime with no reviewed profile has no
# credential to stage and is refused before anything is sent.
RUNTIME_CREDENTIAL_KEYS: Mapping[str, str] = MappingProxyType(
    {"codex": "cli-auth/openai", "opencode": "cli-auth/openai"}
)

_MAX_CREDENTIAL_BYTES = 65536


class CredentialUnavailable(NotInvoked, RuntimeError):
    """No model credential can be staged for this launch; no model ran.

    A ``NotInvoked`` refusal: the kernel records it as suppressed, never as a
    runtime failure, and makes no relaunch. The message is a fixed code.
    """


class SessionHostRefused(RuntimeError):
    """The session host refused the invocation, or its reply was unusable.

    A non-retryable runtime failure: the message is a fixed code, and the failover
    classifier matches none of them.
    """


class SessionHostFailed(RuntimeError):
    """The invocation ran and did not finish well (D14); non-retryable.

    The message is the host's failure class (``runtime_failed``,
    ``containment_failed``, ``cancelled``) or ``failure_class_unknown``. The host
    never reads runtime output, so none of these says a retry could not repeat work.
    """


class SessionHostTimeout(SessionHostFailed, TimeoutError):
    """The runtime ran out its timeout (D14 ``timeout``).

    A ``TimeoutError``, so the existing taxonomy relaunches it once on the tier's
    fallback model, and only if the broker recorded no call of its token (D12).
    """


class SessionHostBusy(NotInvoked, RuntimeError):
    """Every offer of the launch met a host at its concurrency bound; nothing started.

    A ``NotInvoked`` refusal, like ``CredentialUnavailable``: no model ran.
    """


# Failure classes the host reports (``butlers.session_host.control``); anything
# else is ``failure_class_unknown``, which is default-closed (non-retryable).
_FAILURE_CLASSES = frozenset({"timeout", "runtime_failed", "containment_failed", "cancelled"})
# The host's counts-only usage report: the same closed keys and bounds it applies.
_USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "cached_input_tokens"})
_USAGE_MAX = 2**53
_BUSY = HostOutcome(failure=SessionHostRefused("host_busy"))
_MALFORMED = "session_host_malformed_reply"


def _usage(reply: dict) -> tuple[bool, dict[str, int] | None]:
    """``(valid, usage)`` for a settled reply's optional ``usage``, checked as the host does."""
    if "usage" not in reply:
        return True, None
    value = reply["usage"]
    if (
        type(value) is not dict
        or not value
        or not set(value) <= _USAGE_KEYS
        or any(type(v) is not int or not 0 <= v <= _USAGE_MAX for v in value.values())
    ):
        return False, None
    return True, dict(value)


def _reply_outcome(reply: Any) -> HostOutcome:
    """Map one launch reply (#5691's closed shape) to how the invocation ended.

    Returns ``_BUSY`` for the retryable ``host_busy`` refusal. Anything outside the
    closed shape is a malformed reply; nothing of it is kept or logged.
    """
    malformed = HostOutcome(failure=SessionHostRefused(_MALFORMED))
    if type(reply) is not dict:
        return malformed
    status = reply.get("status")
    if status == "refused":
        if set(reply) == {"status"}:
            return HostOutcome(failure=SessionHostRefused("session_host_refused"))
        if set(reply) == {"status", "reason"} and reply["reason"] == "host_busy":
            return _BUSY
        return malformed
    valid, usage = _usage(reply)
    if not valid:
        return malformed
    if status == "completed" and set(reply) <= {"status", "usage"}:
        return HostOutcome(usage=usage)
    if status == "failed" and {"status", "failure_class"} <= set(reply) <= {
        "status",
        "failure_class",
        "usage",
    }:
        failure_class = reply["failure_class"]
        if type(failure_class) is not str:
            return malformed
        if failure_class == "timeout":
            return HostOutcome(failure=SessionHostTimeout("timeout"), usage=usage)
        code = failure_class if failure_class in _FAILURE_CLASSES else "failure_class_unknown"
        return HostOutcome(failure=SessionHostFailed(code), usage=usage)
    return malformed


class CredentialSource(Protocol):
    async def load(self, key: str) -> str | None: ...


class ModelCredentials:
    """Resolve the one credential a launch's runtime needs, fresh for each launch."""

    def __init__(
        self, store: CredentialSource, keys: Mapping[str, str] = RUNTIME_CREDENTIAL_KEYS
    ) -> None:
        self._store = store
        self._keys = dict(keys)

    async def resolve(self, runtime: str, model: str) -> bytes:
        key = self._keys.get(runtime)
        if key is None:
            raise CredentialUnavailable("credential_runtime_unsupported")
        try:
            value = await self._store.load(key)
        except Exception:
            raise CredentialUnavailable("credential_store_unavailable") from None
        if not value:
            raise CredentialUnavailable("credential_missing")
        credential = value.encode()
        if len(credential) > _MAX_CREDENTIAL_BYTES:
            raise CredentialUnavailable("credential_invalid")
        return credential


class ControlSessionHost:
    """The goal runner's ``SessionHost`` over the session host's control channel."""

    #: Offers of one launch to a host at its concurrency bound, and the first backoff.
    busy_attempts = 4
    busy_backoff_s = 2.0

    def __init__(self, send: Send, credentials: ModelCredentials, *, broker_origin: str) -> None:
        self._send = send
        self._credentials = credentials
        self._origin = broker_origin.rstrip("/")

    async def launch(self, launch: Launch) -> HostOutcome:
        """Stage the launch's model credential with it; report how it ended.

        The host replies when the invocation settles (see ``_reply_outcome``). A
        ``host_busy`` refusal is the one retried here, with the same invocation id,
        which the host did not remember. A credential that cannot be resolved, or a
        host that stays busy, is raised as a ``NotInvoked`` refusal: no model ran.
        Any other failure is reported, never raised, as ``SessionHost`` requires.
        """
        credential = await self._credentials.resolve(launch.runtime, launch.model)
        command = {
            "version": 1,
            "operation": "launch",
            "invocation_id": str(launch.invocation_id),
            "goal_id": str(launch.goal_id),
            "endpoint": f"{self._origin}/goals/{launch.goal_id}/mcp",
            "token": launch.token,
            "runtime": launch.runtime,
            "model": launch.model,
            "credential": base64.b64encode(credential).decode("ascii"),
            "prompt": launch.prompt,
            "timeout_s": launch.timeout_s,
        }
        for attempt in range(self.busy_attempts):
            if attempt:
                await asyncio.sleep(self.busy_backoff_s * 2 ** (attempt - 1))
            try:
                reply = await self._send(command)
            except Exception as failure:
                logger.warning(
                    "session host launch unavailable invocation=%s error_class=%s",
                    launch.invocation_id,
                    type(failure).__name__,
                )
                # The reply is awaited until the invocation settles, so a send that
                # fails is a broken channel, and the host may still hold the launch:
                # revoke it, so it frees the credential and its slot now.
                try:
                    await self.terminate(launch.invocation_id)
                except Exception:
                    logger.warning("session host revoke failed invocation=%s", launch.invocation_id)
                return HostOutcome(failure=SessionHostRefused("session_host_unavailable"))
            outcome = _reply_outcome(reply)
            if outcome is not _BUSY:
                if isinstance(outcome.failure, SessionHostRefused) and (
                    str(outcome.failure) == "session_host_malformed_reply"
                ):
                    logger.warning(
                        "session host launch reply malformed invocation=%s", launch.invocation_id
                    )
                return outcome
        # Every offer was refused at the concurrency bound: nothing started, and the
        # host did not remember the id. No model ran, so no relaunch is owed.
        raise SessionHostBusy("session_host_busy")

    async def terminate(self, invocation_id: UUID) -> None:
        """Revoke the invocation on the host. Idempotent: an unknown one is already gone."""
        try:
            await self._send(
                {"version": 1, "operation": "revoke", "invocation_id": str(invocation_id)}
            )
        except Exception:
            logger.warning("session host revoke unavailable invocation=%s", invocation_id)

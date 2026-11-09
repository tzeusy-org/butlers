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
    """The session host refused or failed the invocation (a runtime failure)."""


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

    def __init__(self, send: Send, credentials: ModelCredentials, *, broker_origin: str) -> None:
        self._send = send
        self._credentials = credentials
        self._origin = broker_origin.rstrip("/")

    async def launch(self, launch: Launch) -> HostOutcome:
        """Stage the launch's model credential with it; report how it ended.

        A credential that cannot be resolved is raised (the runner records a
        failure without a model run). Any other failure is reported, never
        raised, as the ``SessionHost`` protocol requires.
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
        try:
            reply = await self._send(command)
        except Exception as failure:
            logger.warning(
                "session host launch unavailable invocation=%s error_class=%s",
                launch.invocation_id,
                type(failure).__name__,
            )
            # The host may still hold the launch (a reply that outlived the read
            # timeout): revoke it, so it frees the credential and its slot now.
            try:
                await self.terminate(launch.invocation_id)
            except Exception:
                logger.warning("session host revoke failed invocation=%s", launch.invocation_id)
            return HostOutcome(failure=SessionHostRefused("session_host_unavailable"))
        status = reply.get("status") if type(reply) is dict else None
        if status == "completed" and set(reply) == {"status"}:
            return HostOutcome()
        return HostOutcome(failure=SessionHostRefused("session_host_refused"))

    async def terminate(self, invocation_id: UUID) -> None:
        """Revoke the invocation on the host. Idempotent: an unknown one is already gone."""
        try:
            await self._send(
                {"version": 1, "operation": "revoke", "invocation_id": str(invocation_id)}
            )
        except Exception:
            logger.warning("session host revoke unavailable invocation=%s", invocation_id)

"""Closed v1 runner-to-host contract (RFC 0038 D3).

The runner supplies an opaque broker token and the exact goal endpoint. Shape
checks do not authenticate the caller or grant authority: a future authenticated
control listener must construct these objects after authenticating the runner.
No broker/DB implementation is imported by this package.
"""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID


class Refused(ValueError):
    """Content-blind refusal: messages contain fixed reason codes only."""


class CredentialNotStaged(Refused):
    """The launch's credential could not be staged, so no runtime was started.

    Raised before spawn (a stale or busy Codex OAuth lease). The owner still settles and
    discards the invocation, then reports ``credential_unavailable``, which the runner
    takes as not-invoked rather than a runtime failure.
    """


class RuntimeTimeout(Refused):
    """The runtime did not finish within its timeout (a D14 ``timeout`` failure class).

    Still ``runtime_failed`` to every caller that matches on the message; the control
    channel reads the type to report the class.
    """


def _origin(value: str) -> str:
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or parts.port == 0
            or any(c.isspace() for c in value)
        ):
            raise ValueError
        return f"https://{parts.netloc}"
    except (ValueError, TypeError):
        raise Refused("invalid_session_origin") from None


@dataclass(frozen=True)
class BrokerBinding:
    """Trusted deployment origin plus exact goal URL; token has no local meaning."""

    goal_id: UUID
    endpoint: str
    token: str = field(repr=False)
    trusted_origin: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.goal_id) is not UUID:
            raise Refused("invalid_broker_binding")
        origin = _origin(self.trusted_origin)
        if self.endpoint != f"{origin}/goals/{self.goal_id}/mcp":
            raise Refused("invalid_broker_binding")
        if type(self.token) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{43}", self.token):
            raise Refused("invalid_broker_binding")
        raw = base64.urlsafe_b64decode(self.token + "=")
        if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != self.token:
            raise Refused("invalid_broker_binding")


@dataclass(frozen=True)
class LaunchRequest:
    """One finite invocation, without arbitrary argv, env, files or adapter proofs."""

    invocation_id: UUID
    broker: BrokerBinding = field(repr=False)
    runtime: str
    model: str
    model_credential: bytes = field(repr=False)
    prompt: str = field(repr=False)
    timeout_s: float = 300
    version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != 1
            or type(self.invocation_id) is not UUID
            or type(self.broker) is not BrokerBinding
        ):
            raise Refused("invalid_launch")
        for name in (self.runtime, self.model):
            if type(name) is not str or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}", name
            ):
                raise Refused("invalid_launch")
        if type(self.model_credential) is not bytes or not 0 < len(self.model_credential) <= 65536:
            raise Refused("invalid_model_credential")
        try:
            if type(self.prompt) is not str or "\0" in self.prompt:
                raise ValueError
            if len(self.prompt.encode()) > 128 * 1024:
                raise ValueError
        except (ValueError, UnicodeError):
            raise Refused("invalid_launch") from None
        if (
            type(self.timeout_s) not in (int, float)
            or not math.isfinite(self.timeout_s)
            or not 0 < self.timeout_s <= 300
        ):
            raise Refused("invalid_launch")

"""Typed transport outcomes for Switchboard-mediated external delivery.

REQ-core-notify-027 requires a caller to tell three situations apart that a
single ``retryable`` boolean cannot express:

* the send was **confirmed** by the peer,
* the send was **proven not attempted** (route construction or recipient
  resolution failed before anything left this process), and
* the send is **uncertain** — transport may have begun and no confirmation is
  available.

REQ-runtime-attention-outbox-002 then hangs at-most-once delivery off exactly
that distinction: only a proven not-attempted outcome may be retried, and an
uncertain outcome is terminal and never replayed.

The ``(error_class, error_detail)`` pairs below are the *same fixed vocabulary*
that ``public.runtime_attention_outbox``'s
``ck_runtime_attention_outbox_delivery_evidence`` constraint accepts.  Keeping
one closed set on both sides is what makes safe telemetry structural rather
than a review-time promise (AC 8): there is no code path that can attach a
recipient, a message body, a credential, or a raw provider string to a
transport outcome, because the only fields a ``TransportResult`` carries are
members of these enums.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx


class TransportOutcome(StrEnum):
    """What is known about an external send attempt."""

    CONFIRMED = "confirmed"
    NOT_ATTEMPTED = "not_attempted"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


class TransportErrorClass(StrEnum):
    """Coarse stage at which a delivery attempt stopped."""

    PRE_TRANSPORT = "pre_transport"
    TRANSPORT_REJECTED = "transport_rejected"
    TRANSPORT_UNCERTAIN = "transport_uncertain"


class TransportErrorDetail(StrEnum):
    """Fixed, non-secret reason codes. Never free-form provider text."""

    RECIPIENT_UNAVAILABLE = "recipient_unavailable"
    POLICY_DENIED = "policy_denied"
    PROVIDER_REJECTED = "provider_rejected"
    TRANSPORT_TIMEOUT = "transport_timeout"
    TRANSPORT_CONNECTION_LOST = "transport_connection_lost"
    WORKER_RECOVERY = "worker_recovery"


# Mirrors ck_runtime_attention_outbox_delivery_evidence in scripts/init-db.sql.
# A pair outside this set is a programming error, not a runtime condition.
SAFE_DELIVERY_EVIDENCE: frozenset[tuple[TransportErrorClass, TransportErrorDetail]] = frozenset(
    {
        (TransportErrorClass.PRE_TRANSPORT, TransportErrorDetail.RECIPIENT_UNAVAILABLE),
        (TransportErrorClass.PRE_TRANSPORT, TransportErrorDetail.POLICY_DENIED),
        (TransportErrorClass.TRANSPORT_REJECTED, TransportErrorDetail.PROVIDER_REJECTED),
        (TransportErrorClass.TRANSPORT_UNCERTAIN, TransportErrorDetail.TRANSPORT_TIMEOUT),
        (TransportErrorClass.TRANSPORT_UNCERTAIN, TransportErrorDetail.TRANSPORT_CONNECTION_LOST),
        (TransportErrorClass.TRANSPORT_UNCERTAIN, TransportErrorDetail.WORKER_RECOVERY),
    }
)

# The stage an attempt stopped at determines what is knowable about it, so the
# two cannot be mixed and matched: a pre-transport stop is *always* proven
# not-attempted, and an uncertain stage is never retryable.
_OUTCOME_FOR_ERROR_CLASS: dict[TransportErrorClass, TransportOutcome] = {
    TransportErrorClass.PRE_TRANSPORT: TransportOutcome.NOT_ATTEMPTED,
    TransportErrorClass.TRANSPORT_REJECTED: TransportOutcome.REJECTED,
    TransportErrorClass.TRANSPORT_UNCERTAIN: TransportOutcome.UNCERTAIN,
}


class TransportNotAttempted(ConnectionError):
    """The peer was never reached, so no request can have been delivered.

    Subclasses :class:`ConnectionError` so existing route callers that already
    treat a connection failure as transient keep behaving exactly as before.
    """


class TransportRejected(RuntimeError):
    """The peer received the call and answered with an explicit error.

    Subclasses :class:`RuntimeError` for the same reason: ``route()`` has
    always surfaced a target-tool error as ``RuntimeError``.
    """


class TransportUncertain(RuntimeError):
    """Transport may have begun; no confirmation is available either way."""


@dataclass(frozen=True, slots=True)
class TransportResult:
    """A closed, non-secret description of one external send attempt."""

    outcome: TransportOutcome
    error_class: TransportErrorClass | None = None
    error_detail: TransportErrorDetail | None = None

    def __post_init__(self) -> None:
        if self.outcome is TransportOutcome.CONFIRMED:
            if self.error_class is not None or self.error_detail is not None:
                raise ValueError("a confirmed transport carries no failure evidence")
            return
        if self.error_class is None or self.error_detail is None:
            raise ValueError(f"{self.outcome} transport requires safe failure evidence")
        if (self.error_class, self.error_detail) not in SAFE_DELIVERY_EVIDENCE:
            raise ValueError(
                f"({self.error_class}, {self.error_detail}) is not safe delivery evidence"
            )
        if _OUTCOME_FOR_ERROR_CLASS[self.error_class] is not self.outcome:
            raise ValueError(f"{self.error_class} evidence cannot describe a {self.outcome} send")

    @property
    def retryable(self) -> bool:
        """Only a proven not-attempted send may be retried.

        A rejected send is terminal, and an uncertain send must never be
        replayed — that is the whole at-most-once guarantee.
        """
        return self.outcome is TransportOutcome.NOT_ATTEMPTED

    def as_dict(self) -> dict[str, str | bool]:
        """Return the additive envelope fragment carried on route results."""
        payload: dict[str, str | bool] = {
            "outcome": str(self.outcome),
            "retryable": self.retryable,
        }
        if self.error_class is not None and self.error_detail is not None:
            payload["error_class"] = str(self.error_class)
            payload["error_detail"] = str(self.error_detail)
        return payload


CONFIRMED = TransportResult(TransportOutcome.CONFIRMED)
RECIPIENT_UNAVAILABLE = TransportResult(
    TransportOutcome.NOT_ATTEMPTED,
    TransportErrorClass.PRE_TRANSPORT,
    TransportErrorDetail.RECIPIENT_UNAVAILABLE,
)
POLICY_DENIED = TransportResult(
    TransportOutcome.NOT_ATTEMPTED,
    TransportErrorClass.PRE_TRANSPORT,
    TransportErrorDetail.POLICY_DENIED,
)
PROVIDER_REJECTED = TransportResult(
    TransportOutcome.REJECTED,
    TransportErrorClass.TRANSPORT_REJECTED,
    TransportErrorDetail.PROVIDER_REJECTED,
)
TRANSPORT_TIMEOUT = TransportResult(
    TransportOutcome.UNCERTAIN,
    TransportErrorClass.TRANSPORT_UNCERTAIN,
    TransportErrorDetail.TRANSPORT_TIMEOUT,
)
TRANSPORT_CONNECTION_LOST = TransportResult(
    TransportOutcome.UNCERTAIN,
    TransportErrorClass.TRANSPORT_UNCERTAIN,
    TransportErrorDetail.TRANSPORT_CONNECTION_LOST,
)
WORKER_RECOVERY = TransportResult(
    TransportOutcome.UNCERTAIN,
    TransportErrorClass.TRANSPORT_UNCERTAIN,
    TransportErrorDetail.WORKER_RECOVERY,
)


# A refused, unroutable, or unreachable *connect* proves the request never left
# this process.  Mid-flight resets (ECONNRESET, EPIPE) are deliberately absent:
# they are ambiguous and must classify as uncertain.
_NOT_ATTEMPTED_ERRNOS = frozenset(
    {
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETUNREACH,
    }
)


def proves_transport_not_attempted(exc: BaseException) -> bool:
    """Return whether *exc* proves the peer was never handed a request.

    FastMCP wraps a connect-phase failure in its exact
    ``RuntimeError('Client failed to connect: ...')`` shape, so that wrapper is
    unwrapped through ``__cause__``.  Anything unrecognized is ambiguous and
    therefore not proof.
    """
    if isinstance(exc, TransportNotAttempted):
        return True
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    if isinstance(exc, OSError) and exc.errno in _NOT_ATTEMPTED_ERRNOS:
        return True
    if type(exc) is RuntimeError and str(exc).startswith("Client failed to connect:"):
        cause = exc.__cause__
        return cause is not None and proves_transport_not_attempted(cause)
    return False


def classify_transport_exception(exc: BaseException) -> TransportResult:
    """Map a transport exception onto the typed outcome vocabulary.

    The default is deliberately :data:`TRANSPORT_CONNECTION_LOST` rather than a
    failure: an unrecognized error is *unknown*, and REQ-runtime-attention-
    outbox-002 makes an unknown result terminal-uncertain, never replayable.
    """
    if isinstance(exc, TransportRejected):
        return PROVIDER_REJECTED
    if proves_transport_not_attempted(exc):
        return RECIPIENT_UNAVAILABLE
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return TRANSPORT_TIMEOUT
    return TRANSPORT_CONNECTION_LOST


def transport_result_from_envelope(envelope: Any) -> TransportResult | None:
    """Recover a :class:`TransportResult` from a route/deliver result dict.

    Returns ``None`` for envelopes produced before this vocabulary existed, so
    callers can keep their pre-existing ``retryable`` handling.
    """
    if not isinstance(envelope, dict):
        return None
    fragment = envelope.get("transport")
    if not isinstance(fragment, dict):
        return None
    try:
        outcome = TransportOutcome(str(fragment.get("outcome")))
    except ValueError:
        return None
    if outcome is TransportOutcome.CONFIRMED:
        return CONFIRMED
    try:
        return TransportResult(
            outcome,
            TransportErrorClass(str(fragment.get("error_class"))),
            TransportErrorDetail(str(fragment.get("error_detail"))),
        )
    except ValueError:
        return None

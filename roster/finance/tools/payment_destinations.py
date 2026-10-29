"""Payment destination fingerprints for bill payment-redirect detection (bu-s11n0s.21).

Finance remembers, per counterparty, which payment destinations (IBAN, bank
account, payment handle) its bills have asked the owner to pay. The raw value
is normalized, classified, and immediately reduced to a keyed HMAC-SHA256
fingerprint plus the last four characters and the kind. The raw value is never
stored, logged, or returned: not in ``finance.bills``, not in
``finance.payment_destinations``, not in the SPO mirror, not in the tool result.

The HMAC key is a Tier-1 secret in the shared credential store, resolved once at
module startup (``FinanceModule.on_startup``, ``env_fallback=False``). A key
stored beside the fingerprints would let a database dump brute-force the small
account-number space, so it deliberately lives outside the schema. When the key
is missing the verdict is ``unverifiable`` and nothing about the destination is
stored.

Key rotation is out of scope: rotating the key resets every baseline (all next
sightings read as ``new_for_counterparty`` until re-learned) and must be paired
with clearing ``payment_destinations``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PAYMENT_DESTINATION_KEY_NAME = "FINANCE_PAYMENT_DESTINATION_KEY"

VERDICT_KNOWN = "known"
VERDICT_NEW = "new_for_counterparty"
VERDICT_UNVERIFIABLE = "unverifiable"

REASON_KEY_UNAVAILABLE = "fingerprint_key_unavailable"
REASON_UNPARSEABLE = "unparseable_destination"
REASON_NO_PRIOR = "no_prior_destination"

_SEPARATORS_RE = re.compile(r"[\s\-./]+")
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_HANDLE_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
_HANDLE_SIGIL_RE = re.compile(r"^[$@][A-Za-z0-9_.\-]{2,}$")
_ACCOUNT_RE = re.compile(r"^[A-Z0-9]*[0-9][A-Z0-9]*$")

# Process-wide key, set once at module startup. ``None`` means fail-closed.
_destination_key: bytes | None = None


@dataclass(frozen=True)
class Destination:
    """A normalized, classified destination. Held only in memory, never persisted."""

    kind: str
    normalized: str

    @property
    def last4(self) -> str:
        return self.normalized[-4:]


def configure_destination_key(value: str | None) -> bool:
    """Set (or clear) the process-wide HMAC key. Returns whether a key is configured."""
    global _destination_key
    if value is None or not str(value).strip():
        _destination_key = None
        return False
    _destination_key = str(value).strip().encode("utf-8")
    return True


def destination_key_configured() -> bool:
    return _destination_key is not None


async def resolve_destination_key(credential_store: Any) -> bool:
    """Resolve the key from the shared credential store once, at module startup.

    Uses ``env_fallback=False`` (the ``APPROVAL_CALLBACK_SECRET`` pattern): the
    key must come from the credential store, never from a process environment
    variable. Logs one warning when the key is unavailable and never raises.
    """
    value: str | None = None
    if credential_store is not None:
        try:
            value = await credential_store.resolve(PAYMENT_DESTINATION_KEY_NAME, env_fallback=False)
        except Exception:  # noqa: BLE001 - a probe failure must not block startup
            logger.warning(
                "Could not resolve %s at startup; bill payment destinations will be "
                "reported as unverifiable",
                PAYMENT_DESTINATION_KEY_NAME,
                exc_info=True,
            )
            value = None
    configured = configure_destination_key(value)
    if not configured:
        logger.warning(
            "%s is not provisioned: bill payment destinations will be reported as "
            "unverifiable and nothing about them will be stored",
            PAYMENT_DESTINATION_KEY_NAME,
        )
    return configured


def normalize_destination(raw: str | None) -> Destination | None:
    """Strip separators, classify the kind, and canonicalize case.

    Returns ``None`` when nothing usable remains (empty, or no digit and no
    handle shape after normalization).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if _HANDLE_EMAIL_RE.match(text) or _HANDLE_SIGIL_RE.match(text):
        return Destination(kind="handle", normalized=text.lower())
    compact = _SEPARATORS_RE.sub("", text).upper()
    if not compact:
        return None
    if _IBAN_RE.match(compact):
        return Destination(kind="iban", normalized=compact)
    if _ACCOUNT_RE.match(compact):
        return Destination(kind="account", normalized=compact)
    return None


def fingerprint_destination(destination: Destination) -> str | None:
    """HMAC-SHA256 over ``kind:normalized``; ``None`` when no key is configured."""
    key = _destination_key
    if key is None:
        return None
    message = f"{destination.kind}:{destination.normalized}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()

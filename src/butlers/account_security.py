"""Deterministic account-security mail classifier (bu-q7vx1q.10).

Turns first-party identity-provider alerts (new sign-in, password changed,
2-step verification changed, recovery info changed, account deletion
scheduled) into a typed verdict from *metadata only*: the sender address, the
subject line, and, when the connector kept it, the ``Authentication-Results``
header. It never reads a message body, never extracts a code or link, and the
verdict carries no free text from the message. Bearer material (OTP codes,
reset links) is quarantined at ingest by ``butlers.ingestion_bearer_scrub``;
this module is deliberately blind to it. A typed ``auth_artifact_observed``
event is a separate deferred slice (bu-q7vx1q.20) and is not produced here.

Trust assumption
----------------
The sender must be an *exact* address on the first-party allowlist below. The
``From`` header alone is forgeable, so when an ``Authentication-Results`` header
is available it is consulted, topmost header only (the trusted receiver's; a
later or sender-injected one never counts), parsed per method clause: a DMARC
failure, or a failure of a DKIM signature aligned with the provider's signing
domain, rejects the message as a spoof (``None``); a ``dkim=pass`` whose own
``header.d`` or a ``dmarc=pass`` whose own ``header.from`` is the provider's
domain (or a subdomain, on a label boundary) yields ``authenticated``; anything
else, including a missing header, yields ``unverified``. Downstream owner prompts must only page on
``authenticated`` verdicts (an ``unverified`` one is a quiet record), so a
forged alert can never wake the owner. Subject matching is a conservative
phrase table: no match means no event, and the caller counts the message as
unclassified so coverage stays honest.

Fixtures and tests use synthetic addresses only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

KIND_NEW_SIGN_IN = "new_sign_in"
KIND_PASSWORD_CHANGED = "password_changed"
KIND_MFA_CHANGED = "mfa_changed"
KIND_RECOVERY_CHANGED = "recovery_changed"
KIND_DELETION_SCHEDULED = "deletion_scheduled"

SECURITY_EVENT_KINDS = frozenset(
    {
        KIND_NEW_SIGN_IN,
        KIND_PASSWORD_CHANGED,
        KIND_MFA_CHANGED,
        KIND_RECOVERY_CHANGED,
        KIND_DELETION_SCHEDULED,
    }
)

VERIFICATION_AUTHENTICATED = "authenticated"
VERIFICATION_UNVERIFIED = "unverified"

# Exact first-party sender address -> provider key. Exact addresses (not
# domains, not subdomains) so a lookalike or a marketing sub-stream on the same
# domain never classifies.
_FIRST_PARTY_SENDERS: dict[str, str] = {
    "no-reply@accounts.google.com": "google",
    "account-security-noreply@accountprotection.microsoft.com": "microsoft",
    "appleid@id.apple.com": "apple",
}

# Provider -> recovery-door URL surfaced when the owner answers "no". Static
# provider pages; nothing from the message is ever interpolated.
PROVIDER_RECOVERY_DOORS: dict[str, str] = {
    "google": "https://myaccount.google.com/notification/recovery",
    "microsoft": "https://account.live.com/Activity",
    "apple": "https://iforgot.apple.com/",
}

# Ordered: the first matching kind wins, most specific first so a "recovery
# email changed" subject is not swallowed by the generic sign-in phrases.
_SUBJECT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        KIND_DELETION_SCHEDULED,
        re.compile(r"(scheduled for deletion|account deletion|being deleted)", re.I),
    ),
    (
        KIND_RECOVERY_CHANGED,
        re.compile(r"recovery (e-?mail|phone|info|information|contact|option)", re.I),
    ),
    (
        KIND_MFA_CHANGED,
        re.compile(
            r"(2-step|two-step|2-factor|two-factor|multi-factor|2fa|mfa|"
            r"security info|passkey|authenticator)",
            re.I,
        ),
    ),
    (
        KIND_PASSWORD_CHANGED,
        re.compile(r"password (was )?(has been )?(changed|updated)", re.I),
    ),
    (
        KIND_NEW_SIGN_IN,
        re.compile(
            r"(new sign-?in|new device|sign-?in (attempt|from)|signed in|used to sign.?in|"
            r"security alert|unusual sign-?in)",
            re.I,
        ),
    ),
)

# Organizational domain each provider signs and authenticates as; a DKIM pass or DMARC
# alignment only counts for this domain or a subdomain on a label boundary.
_PROVIDER_SIGNING_DOMAINS: dict[str, str] = {
    "google": "google.com",
    "microsoft": "microsoft.com",
    "apple": "apple.com",
}

_COMMENT_RE = re.compile(r"\([^()]*\)")
_CLAUSE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9-]*)\s*=\s*([A-Za-z]+)(.*)$", re.S)
_PROP_RE = re.compile(r"(?<![\w.-])((?:header|smtp|policy)\.[a-z]+)\s*=\s*\"?([^\s\";]+)", re.I)
_FAIL_RESULTS = frozenset({"fail", "softfail", "permerror"})


@dataclass(frozen=True)
class AccountSecurityClassification:
    """Typed verdict. Carries no message text beyond the fixed vocabularies."""

    kind: str
    provider: str
    provider_domain: str
    sender_verification: str


def _address_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1]


def _in_domain(candidate: str, domain: str) -> bool:
    """Strict label-boundary check: ``candidate`` is ``domain`` or a subdomain of it."""
    candidate = candidate.strip().lower().rstrip(".")
    return candidate == domain or candidate.endswith("." + domain)


def _clauses(auth_results: str) -> list[tuple[str, str, dict[str, str]]]:
    """Split one Authentication-Results value into (method, result, properties) clauses.

    The first ``;`` segment is the authserv-id and is skipped. A method keyword only
    counts at the start of its own clause, so ``x-dkim=pass`` never reads as ``dkim``.
    """
    parts = _COMMENT_RE.sub(" ", auth_results).split(";")[1:]
    clauses = []
    for part in parts:
        match = _CLAUSE_RE.match(part)
        if match is None:
            continue
        props = {k.lower(): v for k, v in _PROP_RE.findall(match.group(3))}
        clauses.append((match.group(1).lower(), match.group(2).lower(), props))
    return clauses


def _property_domain(value: str) -> str:
    return value.rsplit("@", 1)[-1]


def _verify(auth_results: str | None, provider: str) -> str | None:
    """Return a verification label, or ``None`` when the header reports a spoof.

    Only the topmost ``Authentication-Results`` header (the trusted receiver's) is
    passed in. ``authenticated`` needs a clause-local proof: a ``dkim=pass`` whose own
    ``header.d`` (or ``header.i`` domain) is the provider's signing domain, or a
    ``dmarc=pass`` whose own ``header.from`` is. A fail or permerror on dmarc, or on an
    aligned dkim, rejects; temperror, none and everything else stay ``unverified``.
    """
    if not auth_results or not auth_results.strip():
        return VERIFICATION_UNVERIFIED
    signing = _PROVIDER_SIGNING_DOMAINS[provider]
    authenticated = False
    for method, result, props in _clauses(auth_results):
        if method == "dmarc":
            aligned = _in_domain(_property_domain(props.get("header.from", "")), signing)
        elif method == "dkim":
            domains = [props.get("header.d", ""), _property_domain(props.get("header.i", ""))]
            aligned = any(d and _in_domain(d, signing) for d in domains)
        else:
            continue
        if result in _FAIL_RESULTS and (method == "dmarc" or aligned):
            return None
        if result == "pass" and aligned:
            authenticated = True
    return VERIFICATION_AUTHENTICATED if authenticated else VERIFICATION_UNVERIFIED


def is_account_security_sender(sender_address: str) -> bool:
    """True when *sender_address* is an exact first-party alert sender."""
    return sender_address.strip().lower() in _FIRST_PARTY_SENDERS


def _header(headers: Mapping[str, str | Sequence[str]], name: str) -> str | None:
    """First (topmost) value of *name*; later or injected duplicates never count."""
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            if isinstance(value, str):
                return value
            return str(value[0]) if value else None
    return None


def classify_account_security(
    sender_address: str,
    subject: str,
    *,
    headers: Mapping[str, str | Sequence[str]] | None = None,
) -> AccountSecurityClassification | None:
    """Classify one email as an account-security alert, or ``None``.

    ``None`` covers: sender not on the allowlist, no subject phrase matched, or
    authentication results reporting a failure for the claimed domain.
    """
    sender = sender_address.strip().lower()
    provider = _FIRST_PARTY_SENDERS.get(sender)
    if provider is None or not subject:
        return None
    kind = next((k for k, pattern in _SUBJECT_PATTERNS if pattern.search(subject)), None)
    if kind is None:
        return None
    provider_domain = _address_domain(sender)
    verification = _verify(_header(headers or {}, "Authentication-Results"), provider)
    if verification is None:
        return None
    return AccountSecurityClassification(
        kind=kind,
        provider=provider,
        provider_domain=provider_domain,
        sender_verification=verification,
    )

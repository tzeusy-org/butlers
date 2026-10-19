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
from collections.abc import Callable, Mapping, Sequence
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
VERIFICATION_FAILED = "failed"

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

_METHOD_RESULT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)=([A-Za-z]+)$")
_PROPERTY_TOKEN_RE = re.compile(r"^([A-Za-z][A-Za-z0-9._-]*)=(.*)$", re.S)
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
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


def _tokenize(auth_results: str) -> list[list[str]] | None:
    """Split an Authentication-Results value into top-level segments of tokens.

    Receivers echo sender-chosen text (a quoted MAIL FROM, a HELO literal, the ``header.i``
    local part) into this header, and RFC 8601 lets such text hold ``;`` and ``)``. One scan
    therefore tracks quoted-strings (with ``\\`` escapes), nested comments (whose own
    quoted-strings may hold ``)``) and ``[...]`` literals, and splits on ``;`` and
    whitespace only at top level. Comments act as whitespace and are dropped. Anything
    unbalanced returns ``None`` so the caller fails closed.
    """
    segments: list[list[str]] = [[]]
    token: list[str] = []
    depth = 0
    in_quote = in_bracket = escaped = False

    def flush() -> None:
        if token:
            segments[-1].append("".join(token))
            token.clear()

    for char in auth_results:
        if depth:
            if in_quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_quote = False
            elif char == "\\":
                escaped = True
            elif escaped:
                escaped = False
            elif char == '"':
                in_quote = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            continue
        if in_quote:
            token.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_quote = False
            continue
        if in_bracket:
            token.append(char)
            in_bracket = char != "]"
            continue
        if char == "(":
            flush()
            depth = 1
        elif char in ")]":
            return None
        elif char == '"':
            token.append(char)
            in_quote = True
        elif char == "[":
            token.append(char)
            in_bracket = True
        elif char == ";":
            flush()
            segments.append([])
        elif char.isspace():
            flush()
        else:
            token.append(char)
    if depth or in_quote or in_bracket:
        return None
    flush()
    return segments


def _join_equals(tokens: list[str]) -> list[str]:
    """Rejoin ``key = value`` written with whitespace (or a comment) around ``=``."""
    joined: list[str] = []
    for token in tokens:
        if joined and (joined[-1].endswith("=") or token.startswith("=")):
            joined[-1] += token
        else:
            joined.append(token)
    return joined


def _clauses(auth_results: str) -> list[tuple[str, str, dict[str, str]]] | None:
    """Parse one Authentication-Results value into (method, result, properties) clauses.

    The first top-level segment is the authserv-id and is skipped. A clause is
    ``method=result`` followed by whitespace-separated ``key=value`` tokens; a method only
    counts at the start of its own clause, so ``x-dkim=pass`` never reads as ``dkim``.
    Returns ``None`` when the value is unbalanced or holds anything that is not a clause,
    so a malformed or injected header can only ever read ``unverified``.
    """
    segments = _tokenize(auth_results)
    if segments is None:
        return None
    clauses = []
    for raw_tokens in segments[1:]:
        tokens = _join_equals(raw_tokens)
        if not tokens or tokens == ["none"]:
            continue
        head = _METHOD_RESULT_RE.match(tokens[0])
        if head is None:
            return None
        props: dict[str, str] = {}
        for token in tokens[1:]:
            match = _PROPERTY_TOKEN_RE.match(token)
            if match is None:
                return None
            props.setdefault(match.group(1).lower(), match.group(2))
        clauses.append((head.group(1).lower(), head.group(2).lower(), props))
    return clauses


def _property_domain(value: str) -> str:
    """Domain of a property value (``domain`` or ``local@domain``), or ``""`` if not a domain."""
    value = value.strip()
    if value.startswith('"'):
        end = 1
        while end < len(value) and value[end] != '"':
            end += 2 if value[end] == "\\" else 1
        rest = value[end + 1 :]
        domain = rest[1:] if rest.startswith("@") else ""
    else:
        domain = value.rsplit("@", 1)[-1]
    return domain if _DOMAIN_RE.match(domain) else ""


def _aligned_with(candidate: str, domain: str) -> bool:
    """Same domain, or either one a label-boundary subdomain of the other.

    No public-suffix list is available, so sibling subdomains (``a.example.com`` and
    ``b.example.com``) are deliberately not aligned: that can only under-claim, never let
    one tenant of a shared suffix (``x.github.io``) vouch for another. A parent signer must
    have at least two labels, so a bare TLD never vouches for everything under it.
    """
    candidate = candidate.strip().lower().rstrip(".")
    if not candidate:
        return False
    return _in_domain(candidate, domain) or ("." in candidate and _in_domain(domain, candidate))


_LENIENT_DMARC_FAIL_RE = re.compile(r"(?<![\w-])dmarc\s*=\s*(?:fail|permerror)(?![\w-])", re.I)
_LENIENT_DKIM_FAIL_RE = re.compile(
    r"(?<![\w-])dkim\s*=\s*(?:fail|softfail|permerror)(?![\w-])([^;]*)", re.I
)
_LENIENT_SIGNER_RE = re.compile(r"header\.d\s*=\s*\"?([^\s\";]+)", re.I)


def _lenient_failure(auth_results: str, domain: str, aligns: Callable[[str, str], bool]) -> bool:
    """Whether an unreadable header still carries a genuine failure for *domain*.

    Used only when the strict parse refuses the header. It searches the flat text for a
    ``dmarc`` fail or permerror, or a fail on a ``dkim`` signature whose ``header.d`` aligns.
    A stray character in echoed sender text must not hide a real failure; a false match can
    only come from text the sender wrote into their own message, and only ever fails it.
    """
    if _LENIENT_DMARC_FAIL_RE.search(auth_results):
        return True
    for match in _LENIENT_DKIM_FAIL_RE.finditer(auth_results):
        signer = _LENIENT_SIGNER_RE.search(match.group(1))
        if signer and aligns(signer.group(1), domain):
            return True
    return False


def _verify_against(
    auth_results: str | None,
    domain: str,
    aligns: Callable[[str, str], bool],
    *,
    identity_aligns: bool,
) -> str:
    """Clause-local verdict on *domain*; *aligns* decides whether a signer vouches for it.

    A header the strict parser refuses is never trusted: it reads ``failed`` when a lenient
    scan finds a genuine failure for the domain and ``unverified`` otherwise, never
    ``authenticated``. *identity_aligns* also lets a dkim ``header.i`` domain align.
    """
    if not domain or not auth_results or not auth_results.strip():
        return VERIFICATION_UNVERIFIED
    clauses = _clauses(auth_results)
    if clauses is None:
        if _lenient_failure(auth_results, domain, aligns):
            return VERIFICATION_FAILED
        return VERIFICATION_UNVERIFIED
    authenticated = False
    for method, result, props in clauses:
        if method == "dmarc":
            aligned = aligns(_property_domain(props.get("header.from", "")), domain)
        elif method == "dkim":
            domains = [props.get("header.d", "")]
            if identity_aligns:
                domains.append(_property_domain(props.get("header.i", "")))
            aligned = any(d and aligns(d, domain) for d in domains)
        else:
            continue
        if result in _FAIL_RESULTS and (method == "dmarc" or aligned):
            return VERIFICATION_FAILED
        if result == "pass" and aligned:
            authenticated = True
    return VERIFICATION_AUTHENTICATED if authenticated else VERIFICATION_UNVERIFIED


def verify_sender_domain(auth_results: str | None, sender_domain: str) -> str:
    """Deterministic verdict on *sender_domain* from one ``Authentication-Results`` value.

    Returns ``authenticated`` for a clause-local ``dkim=pass`` whose own ``header.d`` aligns
    with the sender domain, or a ``dmarc=pass`` whose own ``header.from`` does (DMARC aligns on
    ``d=`` only, so ``header.i`` never counts here). A ``dmarc`` fail or permerror, or a fail on
    an aligned dkim, is ``failed``. A missing header, temperror, none and unaligned passes
    stay ``unverified``. A header the strict parser refuses reads ``failed`` if it still
    carries such a failure and ``unverified`` otherwise.
    Only the topmost header (the trusted receiver's) may be passed in; the caller picks it.
    """
    domain = sender_domain.strip().lower().rstrip(".")
    return _verify_against(auth_results, domain, _aligned_with, identity_aligns=False)


def _verify(auth_results: str | None, provider: str) -> str | None:
    """Provider-alert view of :func:`verify_sender_domain`; ``None`` on a spoof.

    Provider alignment stays one-directional (the signer must be the provider's domain or a
    subdomain of it), so provider-alert behavior is unchanged by the shared verdict.
    """
    signing = _PROVIDER_SIGNING_DOMAINS[provider]
    verdict = _verify_against(auth_results, signing, _in_domain, identity_aligns=True)
    return None if verdict == VERIFICATION_FAILED else verdict


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

"""Dependency-inversion hooks for the approvals module.

``core_tools`` (notify, route.execute) need to invoke email-recipient
approval checks -- and park a PENDING action for owner review -- without
importing ``modules.approvals`` directly.  ``core_tools`` is a core layer:
``tests/contracts/test_dependency_direction.py`` enforces that
``butlers.core_tools.*`` never imports ``butlers.modules.*``, so this module
is the only sanctioned crossing point.

This module provides:

1. ``EmailGuardDecision`` — a dataclass that mirrors the shape of
   ``modules.approvals.email_guard.EmailGuardDecision`` so that callers in
   core can type-check against it without importing the module package.

2. A pool-scoped hook-registration API that ``modules.approvals`` calls during
   startup to wire up its concrete implementation without leaking that module
   into other butlers hosted in the same process.

3. Thin stubs (``check_email_recipient``, ``check_recipient``,
   ``park_pending_action``) that delegate to the registered hooks.  The
   recipient-guard stubs fail open (return an "allowed" decision) when the
   approvals module is not loaded, since butlers without approvals must
   remain functional.  ``park_pending_action`` cannot fail open the same way
   -- there is no safe default for "park this action" -- so it logs loudly
   and no-ops when unregistered (mirroring the reality that a butler with no
   approvals module has no ``pending_actions`` table to park into).

Design rationale
----------------
Core defines the *interface*; the approvals module supplies the *implementation*.
The registration call in ``modules.approvals`` is the only place the two layers
are coupled, and it runs inside ``on_startup``, safely after core is initialised.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision-dossier boundary (owned by core)
# ---------------------------------------------------------------------------


BLAST_RADIUS_VALUES = ("none", "self", "contact", "external")
REVERSIBILITY_VALUES = ("reversible", "compensable", "irreversible")
EVIDENCE_TYPES = ("fact", "entity", "url", "text")
_WHY_MAX_CHARS = 2000
_EVIDENCE_MAX_ITEMS = 50
_EVIDENCE_VALUE_MAX_CHARS = 2000

EvidenceReference = dict[str, str]


@dataclass(frozen=True, slots=True)
class DecisionDossier:
    """Strictly validated approval context persisted with a pending action."""

    why: str | None
    evidence: list[EvidenceReference]
    blast_radius: str | None
    reversibility: str | None


def _dossier_error(
    *,
    field: str,
    code: str,
    message: str,
    allowed_values: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return a retryable boundary error without parking an action."""
    error: dict[str, Any] = {
        "code": code,
        "field": field,
        "message": message,
        "retryable": True,
    }
    if allowed_values is not None:
        error["allowed_values"] = list(allowed_values)
    return {"status": "error", "error": error, "retryable": True}


def _validate_optional_enum(
    value: Any,
    *,
    field: str,
    allowed_values: tuple[str, ...],
) -> str | None | dict[str, Any]:
    """Validate a nullable dossier enum without coercing malformed values."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed_values:
        return _dossier_error(
            field=field,
            code="invalid_dossier_value",
            message=(f"{field} must be one of {', '.join(allowed_values)} when provided."),
            allowed_values=allowed_values,
        )
    return value


def _validate_dossier(
    *,
    raw_why: Any,
    raw_evidence: Any,
    raw_blast_radius: Any,
    raw_reversibility: Any,
    require_why: bool,
) -> DecisionDossier | dict[str, Any]:
    """Strictly validate supplied dossier metadata without coercing it."""
    if raw_why is None:
        if require_why:
            return _dossier_error(
                field="why",
                code="missing_required_dossier_field",
                message="why is required for a gated non-owner action; retry with _why.",
            )
        why: str | None = None
    elif not isinstance(raw_why, str) or not raw_why.strip():
        return _dossier_error(
            field="why",
            code="invalid_dossier_value",
            message="why must be a non-empty human-readable string.",
        )
    elif len(raw_why) > _WHY_MAX_CHARS:
        return _dossier_error(
            field="why",
            code="invalid_dossier_value",
            message=f"why must not exceed {_WHY_MAX_CHARS} characters.",
        )
    else:
        why = raw_why

    blast_radius = _validate_optional_enum(
        raw_blast_radius,
        field="blast_radius",
        allowed_values=BLAST_RADIUS_VALUES,
    )
    if isinstance(blast_radius, dict):
        return blast_radius

    reversibility = _validate_optional_enum(
        raw_reversibility,
        field="reversibility",
        allowed_values=REVERSIBILITY_VALUES,
    )
    if isinstance(reversibility, dict):
        return reversibility

    if raw_evidence is None:
        evidence: list[EvidenceReference] = []
    elif not isinstance(raw_evidence, list):
        return _dossier_error(
            field="evidence",
            code="invalid_dossier_value",
            message="evidence must be a list of typed evidence references.",
        )
    elif len(raw_evidence) > _EVIDENCE_MAX_ITEMS:
        return _dossier_error(
            field="evidence",
            code="invalid_dossier_value",
            message=f"evidence may contain at most {_EVIDENCE_MAX_ITEMS} entries.",
        )
    else:
        evidence = []
        for index, item in enumerate(raw_evidence):
            field = f"evidence[{index}]"
            if not isinstance(item, dict):
                return _dossier_error(
                    field=field,
                    code="invalid_dossier_value",
                    message=(
                        f"{field} must be an object with type, ref, and note; "
                        "plain-string evidence is no longer accepted."
                    ),
                )
            if set(item) != {"type", "ref", "note"}:
                return _dossier_error(
                    field=field,
                    code="invalid_dossier_value",
                    message=f"{field} must contain exactly type, ref, and note.",
                )
            evidence_type = item["type"]
            ref = item["ref"]
            note = item["note"]
            if not isinstance(evidence_type, str) or evidence_type not in EVIDENCE_TYPES:
                return _dossier_error(
                    field=f"{field}.type",
                    code="invalid_dossier_value",
                    message=f"{field}.type must be one of {', '.join(EVIDENCE_TYPES)}.",
                    allowed_values=EVIDENCE_TYPES,
                )
            if not isinstance(ref, str) or not ref:
                return _dossier_error(
                    field=f"{field}.ref",
                    code="invalid_dossier_value",
                    message=f"{field}.ref must be a non-empty string.",
                )
            if not isinstance(note, str):
                return _dossier_error(
                    field=f"{field}.note",
                    code="invalid_dossier_value",
                    message=f"{field}.note must be a string.",
                )
            if len(ref) > _EVIDENCE_VALUE_MAX_CHARS or len(note) > _EVIDENCE_VALUE_MAX_CHARS:
                return _dossier_error(
                    field=field,
                    code="invalid_dossier_value",
                    message=(
                        f"{field}.ref and {field}.note must not exceed "
                        f"{_EVIDENCE_VALUE_MAX_CHARS} characters."
                    ),
                )
            evidence.append({"type": evidence_type, "ref": ref, "note": note})

    return DecisionDossier(
        why=why,
        evidence=evidence,
        blast_radius=blast_radius,
        reversibility=reversibility,
    )


def validate_non_owner_dossier(
    *,
    raw_why: Any,
    raw_evidence: Any,
    raw_blast_radius: Any,
    raw_reversibility: Any,
) -> DecisionDossier | dict[str, Any]:
    """Require a typed dossier before a non-owner action can proceed."""
    return _validate_dossier(
        raw_why=raw_why,
        raw_evidence=raw_evidence,
        raw_blast_radius=raw_blast_radius,
        raw_reversibility=raw_reversibility,
        require_why=True,
    )


def validate_owner_dossier(
    *,
    raw_why: Any,
    raw_evidence: Any,
    raw_blast_radius: Any,
    raw_reversibility: Any,
) -> DecisionDossier | dict[str, Any]:
    """Validate supplied owner metadata without making any field mandatory."""
    return _validate_dossier(
        raw_why=raw_why,
        raw_evidence=raw_evidence,
        raw_blast_radius=raw_blast_radius,
        raw_reversibility=raw_reversibility,
        require_why=False,
    )


# ---------------------------------------------------------------------------
# Recipient-guard interface types (owned by core)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmailGuardDecision:
    """Result of an email-recipient approval check.

    Mirrors ``modules.approvals.email_guard.EmailGuardDecision`` so that
    core_tools can use this type without importing the approvals package.
    """

    allowed: bool
    reason: str  # "owner" | "rule" | "parked" | "dossier_error"
    action_id: uuid.UUID | None = None
    rule_id: uuid.UUID | None = None
    contact_desc: str | None = None
    dossier_error: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Hook slot
# ---------------------------------------------------------------------------

#: Registered by modules.approvals during on_startup.
#: Signature: ``async (pool, *, email_target, ...) -> EmailGuardDecision``
_email_guard_hook: Callable[..., Coroutine[Any, Any, EmailGuardDecision]] | None = None

#: Registered by modules.approvals during on_startup.
#: Channel-general outbound recipient guard (telegram and any non-email channel).
#: Signature: ``async (pool, *, channel, target, ...) -> EmailGuardDecision``
_recipient_guard_hook: Callable[..., Coroutine[Any, Any, EmailGuardDecision]] | None = None

# ``butlers up`` hosts every butler daemon in one Python process.  The optional
# approvals module is enabled per butler, and every butler has a distinct pool
# whose connections carry that butler's schema search path.  Keep production
# registrations keyed by pool identity so enabling approvals on (for example)
# Messenger cannot make Finance query approvals tables that do not exist in the
# Finance schema.  The singular slots above remain as compatibility injection
# points for focused tests and non-daemon callers that explicitly opt into a
# process-wide implementation.
_email_guard_hooks_by_pool: dict[
    int,
    tuple[Any, Callable[..., Coroutine[Any, Any, EmailGuardDecision]]],
] = {}
_recipient_guard_hooks_by_pool: dict[
    int,
    tuple[Any, Callable[..., Coroutine[Any, Any, EmailGuardDecision]]],
] = {}


def _register_pool_hook(
    registry: dict[int, tuple[Any, Callable[..., Coroutine[Any, Any, Any]]]],
    pool: Any,
    fn: Callable[..., Coroutine[Any, Any, Any]],
) -> None:
    """Register *fn* for exactly *pool*, retaining identity for id safety."""
    registry[id(pool)] = (pool, fn)


def _resolve_pool_hook(
    registry: dict[int, tuple[Any, Callable[..., Coroutine[Any, Any, Any]]]],
    pool: Any,
    fallback: Callable[..., Coroutine[Any, Any, Any]] | None,
) -> Callable[..., Coroutine[Any, Any, Any]] | None:
    """Resolve a scoped hook, falling back only to explicit legacy injection."""
    registered = registry.get(id(pool))
    if registered is not None and registered[0] is pool:
        return registered[1]
    return fallback


# ---------------------------------------------------------------------------
# Registration API (called by modules.approvals)
# ---------------------------------------------------------------------------


def register_email_guard(
    fn: Callable[..., Coroutine[Any, Any, Any]],
    *,
    pool: Any | None = None,
) -> None:
    """Register the email-guard implementation from ``modules.approvals``.

    The registered callable must have the same keyword-argument signature as
    ``modules.approvals.email_guard.check_email_recipient``.  The return value
    must be compatible with ``EmailGuardDecision`` (allowed, reason, action_id,
    rule_id, contact_desc, dossier_error attributes).

    Args:
        fn: Async callable implementing the email-guard check.
        pool: Owning butler pool.  When omitted, installs the legacy explicit
            process-wide test/integration hook.
    """
    if pool is not None:
        _register_pool_hook(_email_guard_hooks_by_pool, pool, fn)
        return

    global _email_guard_hook
    _email_guard_hook = fn


def register_recipient_guard(
    fn: Callable[..., Coroutine[Any, Any, Any]],
    *,
    pool: Any | None = None,
) -> None:
    """Register the channel-general recipient guard from ``modules.approvals``.

    The registered callable must have the same keyword-argument signature as
    ``modules.approvals.email_guard.check_recipient``.  The return value must be
    compatible with ``EmailGuardDecision``.

    Args:
        fn: Async callable implementing the channel-general recipient check.
        pool: Owning butler pool.  When omitted, installs the legacy explicit
            process-wide test/integration hook.
    """
    if pool is not None:
        _register_pool_hook(_recipient_guard_hooks_by_pool, pool, fn)
        return

    global _recipient_guard_hook
    _recipient_guard_hook = fn


def unregister_approval_hooks(pool: Any) -> None:
    """Remove every approvals implementation registered for *pool*."""
    for registry in (
        _email_guard_hooks_by_pool,
        _recipient_guard_hooks_by_pool,
        _park_pending_action_hooks_by_pool,
    ):
        registered = registry.get(id(pool))
        if registered is not None and registered[0] is pool:
            del registry[id(pool)]


# ---------------------------------------------------------------------------
# Core-callable stub
# ---------------------------------------------------------------------------


async def check_email_recipient(
    pool: Any,
    *,
    email_target: str,
    rule_tool_name: str,
    rule_match_args: dict[str, Any],
    park_tool_name: str,
    park_tool_args: dict[str, Any],
    park_summary: str = "",
    session_id: str | uuid.UUID | None = None,
    expiry_hours: int = 72,
    msg_context: str | None = None,
    butler_name: str | None = None,
    why: Any = None,
    evidence: Any = None,
    blast_radius: Any = None,
    reversibility: Any = None,
    enforce_dossier: bool = False,
    approval_push_runtime: Any = None,
) -> EmailGuardDecision:
    """Check whether an outbound email to *email_target* is permitted.

    Delegates to the hook registered by ``modules.approvals``.  When no hook
    is registered (approvals module not loaded), returns an
    ``allowed=True`` decision so butlers without approvals remain functional.

    Parameters mirror ``modules.approvals.email_guard.check_email_recipient``.
    ``approval_push_runtime`` (an
    ``modules.approvals.notifications.ApprovalPushRuntime`` or ``None``) is
    passed through untyped here to avoid importing the approvals module from
    core; the registered hook applies it when parking an action so the owner
    is actually notified (bu-mda0r).
    """
    hook = _resolve_pool_hook(_email_guard_hooks_by_pool, pool, _email_guard_hook)
    if hook is None:
        # Approvals module not loaded — fail open.
        return EmailGuardDecision(allowed=True, reason="no_approvals_module")

    result = await hook(
        pool,
        email_target=email_target,
        rule_tool_name=rule_tool_name,
        rule_match_args=rule_match_args,
        park_tool_name=park_tool_name,
        park_tool_args=park_tool_args,
        park_summary=park_summary,
        session_id=session_id,
        expiry_hours=expiry_hours,
        msg_context=msg_context,
        butler_name=butler_name,
        why=why,
        evidence=evidence,
        blast_radius=blast_radius,
        reversibility=reversibility,
        enforce_dossier=enforce_dossier,
        approval_push_runtime=approval_push_runtime,
    )
    # Coerce to core's EmailGuardDecision (modules returns the approvals-local type).
    return EmailGuardDecision(
        allowed=result.allowed,
        reason=result.reason,
        action_id=result.action_id,
        rule_id=result.rule_id,
        contact_desc=result.contact_desc,
        dossier_error=getattr(result, "dossier_error", None),
    )


async def check_recipient(
    pool: Any,
    *,
    channel: str,
    target: str,
    rule_tool_name: str,
    rule_match_args: dict[str, Any],
    park_tool_name: str,
    park_tool_args: dict[str, Any],
    park_summary: str = "",
    session_id: str | uuid.UUID | None = None,
    expiry_hours: int = 72,
    butler_name: str | None = None,
    why: Any = None,
    evidence: Any = None,
    blast_radius: Any = None,
    reversibility: Any = None,
    enforce_dossier: bool = False,
    approval_push_runtime: Any = None,
) -> EmailGuardDecision:
    """Channel-general outbound recipient guard for ``notify()``.

    Applies role-based approval gating to ANY supported channel (telegram and
    beyond), mirroring :func:`check_email_recipient` for the email channel:
    owner-directed sends auto-approve on any active verified owner channel,
    while non-owner recipients require a standing rule or are parked.

    Delegates to the hook registered by ``modules.approvals``.  When no hook is
    registered (approvals module not loaded), returns an ``allowed=True``
    decision so butlers without approvals remain functional.

    Parameters mirror ``modules.approvals.email_guard.check_recipient``.
    ``approval_push_runtime`` is forwarded untyped (see
    :func:`check_email_recipient`) so a parked action is actually pushed to
    the owner (bu-mda0r).
    """
    hook = _resolve_pool_hook(_recipient_guard_hooks_by_pool, pool, _recipient_guard_hook)
    if hook is None:
        # Approvals module not loaded — fail open.
        return EmailGuardDecision(allowed=True, reason="no_approvals_module")

    result = await hook(
        pool,
        channel=channel,
        target=target,
        rule_tool_name=rule_tool_name,
        rule_match_args=rule_match_args,
        park_tool_name=park_tool_name,
        park_tool_args=park_tool_args,
        park_summary=park_summary,
        session_id=session_id,
        expiry_hours=expiry_hours,
        butler_name=butler_name,
        why=why,
        evidence=evidence,
        blast_radius=blast_radius,
        reversibility=reversibility,
        enforce_dossier=enforce_dossier,
        approval_push_runtime=approval_push_runtime,
    )
    # Coerce to core's EmailGuardDecision (modules returns the approvals-local type).
    return EmailGuardDecision(
        allowed=result.allowed,
        reason=result.reason,
        action_id=result.action_id,
        rule_id=result.rule_id,
        contact_desc=result.contact_desc,
        dossier_error=getattr(result, "dossier_error", None),
    )


# ---------------------------------------------------------------------------
# Park hook slot
# ---------------------------------------------------------------------------

#: Registered by modules.approvals during on_startup.
#: Signature: modules.approvals.park.park_pending_action (same keyword args).
_park_pending_action_hook: Callable[..., Coroutine[Any, Any, Any]] | None = None
_park_pending_action_hooks_by_pool: dict[
    int,
    tuple[Any, Callable[..., Coroutine[Any, Any, Any]]],
] = {}


def register_park_pending_action(
    fn: Callable[..., Coroutine[Any, Any, Any]],
    *,
    pool: Any | None = None,
) -> None:
    """Register the park_pending_action implementation from ``modules.approvals``.

    The registered callable must have the same keyword-argument signature as
    ``modules.approvals.park.park_pending_action`` -- it IS that function; this
    only exists so core_tools reaches it through this hook instead of an
    import, keeping ``butlers.core_tools.*`` free of ``butlers.modules.*``
    imports (bu-mda0r; enforced by
    ``tests/contracts/test_dependency_direction.py``).

    Args:
        fn: Async callable implementing the park-and-push choke point.
        pool: Owning butler pool.  When omitted, installs the legacy explicit
            process-wide test/integration hook.
    """
    if pool is not None:
        _register_pool_hook(_park_pending_action_hooks_by_pool, pool, fn)
        return

    global _park_pending_action_hook
    _park_pending_action_hook = fn


async def park_pending_action(
    pool: Any,
    *,
    action_id: uuid.UUID,
    tool_name: str,
    tool_args: dict[str, Any],
    agent_summary: str | None,
    requested_at: datetime,
    expires_at: datetime | None,
    session_id: uuid.UUID | None = None,
    why: str | None = None,
    evidence: Any = None,
    blast_radius: str | None = None,
    reversibility: str | None = None,
    origin_butler: str | None = None,
    approval_push_runtime: Any = None,
    deduplication_key: str | None = None,
) -> Any | None:
    """Insert one PENDING ``pending_actions`` row and push it to the owner.

    Delegates to the hook registered by ``modules.approvals``
    (``modules.approvals.park.park_pending_action``, the single choke point
    every PENDING park path in this codebase routes through -- bu-mda0r).

    Unlike :func:`check_email_recipient` / :func:`check_recipient`, this
    cannot fail open: there is no safe default for "park this action" when no
    hook is registered.  A butler with no approvals module also has no
    ``pending_actions`` table to park into, so this logs a loud warning and
    returns ``None`` (no row written, no push attempted) rather than
    fabricating a park that never happened.
    """
    hook = _resolve_pool_hook(
        _park_pending_action_hooks_by_pool,
        pool,
        _park_pending_action_hook,
    )
    if hook is None:
        logger.warning(
            "park_pending_action called but no approvals module is registered on "
            "this butler; action %s (tool=%r) was NOT parked",
            action_id,
            tool_name,
        )
        return None

    kwargs: dict[str, Any] = {
        "action_id": action_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "agent_summary": agent_summary,
        "requested_at": requested_at,
        "expires_at": expires_at,
        "session_id": session_id,
        "why": why,
        "evidence": evidence,
        "blast_radius": blast_radius,
        "reversibility": reversibility,
        "origin_butler": origin_butler,
        "approval_push_runtime": approval_push_runtime,
    }
    if deduplication_key is not None:
        kwargs["deduplication_key"] = deduplication_key
    return await hook(pool, **kwargs)

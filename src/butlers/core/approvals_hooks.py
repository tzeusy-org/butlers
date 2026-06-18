"""Dependency-inversion hooks for the approvals module.

``core_tools`` (notify, route.execute) need to invoke email-recipient
approval checks without importing ``modules.approvals`` directly.

This module provides:

1. ``EmailGuardDecision`` — a dataclass that mirrors the shape of
   ``modules.approvals.email_guard.EmailGuardDecision`` so that callers in
   core can type-check against it without importing the module package.

2. A hook-registration API that ``modules.approvals`` calls during startup
   to wire up its concrete implementation.

3. A thin ``check_email_recipient`` stub that delegates to the registered hook
   or returns an "allowed" decision when the approvals module is not loaded
   (fail-open for butlers that don't enable approvals).

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
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Interface types (owned by core)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmailGuardDecision:
    """Result of an email-recipient approval check.

    Mirrors ``modules.approvals.email_guard.EmailGuardDecision`` so that
    core_tools can use this type without importing the approvals package.
    """

    allowed: bool
    reason: str  # "owner" | "rule" | "parked"
    action_id: uuid.UUID | None = None
    rule_id: uuid.UUID | None = None
    contact_desc: str | None = None


# ---------------------------------------------------------------------------
# Hook slot
# ---------------------------------------------------------------------------

#: Registered by modules.approvals during on_startup.
#: Signature: ``async (pool, *, email_target, ...) -> EmailGuardDecision``
_email_guard_hook: Callable[..., Coroutine[Any, Any, EmailGuardDecision]] | None = None


# ---------------------------------------------------------------------------
# Registration API (called by modules.approvals)
# ---------------------------------------------------------------------------


def register_email_guard(
    fn: Callable[..., Coroutine[Any, Any, Any]],
) -> None:
    """Register the email-guard implementation from ``modules.approvals``.

    The registered callable must have the same keyword-argument signature as
    ``modules.approvals.email_guard.check_email_recipient``.  The return value
    must be compatible with ``EmailGuardDecision`` (allowed, reason, action_id,
    rule_id, contact_desc attributes).

    Args:
        fn: Async callable implementing the email-guard check.
    """
    global _email_guard_hook
    _email_guard_hook = fn


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
) -> EmailGuardDecision:
    """Check whether an outbound email to *email_target* is permitted.

    Delegates to the hook registered by ``modules.approvals``.  When no hook
    is registered (approvals module not loaded), returns an
    ``allowed=True`` decision so butlers without approvals remain functional.

    Parameters mirror ``modules.approvals.email_guard.check_email_recipient``.
    """
    if _email_guard_hook is None:
        # Approvals module not loaded — fail open.
        return EmailGuardDecision(allowed=True, reason="no_approvals_module")

    result = await _email_guard_hook(
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
    )
    # Coerce to core's EmailGuardDecision (modules returns the approvals-local type).
    return EmailGuardDecision(
        allowed=result.allowed,
        reason=result.reason,
        action_id=result.action_id,
        rule_id=result.rule_id,
        contact_desc=result.contact_desc,
    )

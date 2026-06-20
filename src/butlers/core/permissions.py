"""Runtime reader / enforcer for the ``public.permissions`` matrix.

The permissions matrix (``public.permissions``, one row per ``(butler,
permission)``) is owned and mutated by the dashboard Settings → Permissions page
(``PUT /api/permissions/{butler}/{perm}``).  Each row records whether a butler is
``granted`` a named permission, plus a mandatory audit reason.

Before this module, the matrix was **decorative**: every reference to
``public.permissions`` in ``src/`` was a reader (the GET display endpoint and the
data-ops wipe-target list) — nothing on the runtime side ever consulted a grant
before letting a butler act.  This module adds the missing enforcement primitive:

* :func:`check_permission` — non-raising lookup returning a :class:`PermissionStatus`.
* :func:`require_permission` — raising variant for call sites that prefer an
  exception (raises :class:`PermissionDenied`).

Semantics
---------
The matrix is **opt-in deny**: a butler is allowed by default, and the owner
revokes a capability by flipping a cell to ``granted=false`` (which writes an
explicit row).  Therefore:

* No row for ``(butler, permission)``  → **allowed** (default-allow; the owner
  has not expressed an opinion).
* Row with ``granted = true``          → **allowed**.
* Row with ``granted = false``         → **denied** (explicit revocation).

Fail-open
---------
If the lookup query fails (DB unreachable, switchboard pool missing, malformed
row), the check returns ``allowed=True`` and logs a warning.  This mirrors the
spend-ceiling (:func:`butlers.core.model_routing.check_monthly_ceiling`) and
token-quota guardrails: a permission lookup must never become a single point of
failure that wedges every spawn town-wide.  A revocation is a deliberate owner
action surfaced in the matrix; it should not be enforced by an unreliable read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg

logger = logging.getLogger(__name__)

# Permission a butler must hold to spawn an ephemeral runtime session at all.
# This is the primary capability the matrix governs at the spawn choke point.
SPAWN_PERMISSION = "spawn"

# The remaining seeded vocabulary (core_121). Each names a governed capability
# enforced at its own call site (mirroring the spawn gate):
#   cross_butler   — invoking another butler via the Switchboard (route_to_butler).
#   notify         — sending an owner-facing notification (notify() core tool).
#   email.send     — sending email on the owner's behalf (email module _send_email).
#   calendar.write — creating/modifying/deleting calendar events (calendar module).
CROSS_BUTLER_PERMISSION = "cross_butler"
NOTIFY_PERMISSION = "notify"
EMAIL_SEND_PERMISSION = "email.send"
CALENDAR_WRITE_PERMISSION = "calendar.write"

# ---------------------------------------------------------------------------
# Canonical enforced permission vocabulary (SINGLE source of truth).
# The dashboard matrix and its dense-matrix builder import from here.
# ---------------------------------------------------------------------------

#: Full set of enforced permissions shown in the dashboard matrix.
ENFORCED_PERMISSIONS: tuple[str, ...] = (
    CALENDAR_WRITE_PERMISSION,
    CROSS_BUTLER_PERMISSION,
    EMAIL_SEND_PERMISSION,
    NOTIFY_PERMISSION,
    SPAWN_PERMISSION,
)

#: Default granted value for a (butler, permission) pair with no explicit row.
#: Reflects opt-in-deny semantics: no row → allowed.
PERMISSION_DEFAULT_GRANTED: bool = True

_PERMISSION_SELECT_SQL = """
SELECT granted, reason
FROM public.permissions
WHERE butler = $1 AND permission = $2
"""


@dataclass(frozen=True)
class PermissionStatus:
    """Result of a permission lookup against ``public.permissions``.

    Attributes
    ----------
    allowed:
        ``True`` when the butler may perform the action (granted, or no explicit
        row exists, or the lookup failed and we failed open).
    explicit:
        ``True`` when a concrete ``public.permissions`` row drove the decision.
        ``False`` for the default-allow path (no row) and the fail-open path.
    reason:
        The audit reason recorded on the row, when one exists.
    """

    allowed: bool
    explicit: bool = False
    reason: str | None = None


class PermissionDenied(Exception):
    """Raised by :func:`require_permission` when a butler lacks a permission."""

    def __init__(self, butler: str, permission: str, reason: str | None = None) -> None:
        self.butler = butler
        self.permission = permission
        self.reason = reason
        detail = f"Permission denied: butler '{butler}' is not granted '{permission}'"
        if reason:
            detail += f" (reason: {reason})"
        super().__init__(detail)


async def check_permission(
    pool: asyncpg.Pool | None,
    butler: str,
    permission: str,
) -> PermissionStatus:
    """Return whether ``butler`` is granted ``permission`` per ``public.permissions``.

    Non-raising. See the module docstring for the opt-in-deny semantics and the
    fail-open contract.

    Parameters
    ----------
    pool:
        asyncpg pool connected to a database that can read ``public.permissions``
        (the switchboard pool).  ``None`` is treated as "no opinion available" and
        fails open (``allowed=True``).
    butler:
        Butler name (matrix row key).
    permission:
        Permission name (matrix column key, e.g. ``"spawn"`` or ``"email.read"``).
    """
    if pool is None:
        return PermissionStatus(allowed=True)

    try:
        row = await pool.fetchrow(_PERMISSION_SELECT_SQL, butler, permission)
    except Exception:
        logger.warning(
            "check_permission failed for butler=%s permission=%s; failing open (allowed=True)",
            butler,
            permission,
            exc_info=True,
        )
        return PermissionStatus(allowed=True)

    if row is None:
        # No explicit grant/revoke — default allow.
        return PermissionStatus(allowed=True)

    granted = bool(row["granted"])
    return PermissionStatus(
        allowed=granted,
        explicit=True,
        reason=row["reason"],
    )


async def require_permission(
    pool: asyncpg.Pool | None,
    butler: str,
    permission: str,
) -> None:
    """Raise :class:`PermissionDenied` when ``butler`` lacks ``permission``.

    Thin raising wrapper over :func:`check_permission` for call sites that prefer
    exception control flow. Shares the same opt-in-deny and fail-open semantics.
    """
    status = await check_permission(pool, butler, permission)
    if not status.allowed:
        raise PermissionDenied(butler, permission, status.reason)

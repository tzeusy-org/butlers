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

Store availability
------------------
Effect sites opt into ``fail_closed=True``: a missing pool, failed read or malformed
row refuses before the effect with :class:`PermissionUnavailable`. A readable
missing row still allows. Availability failures are logged without row contents
or raw exception text.

The default shared lookup temporarily retains legacy fail-open behavior for
spawn, cross-butler routing and core notify admission. The staged correction
bu-30sah4 protects effects first; dependent bu-svqpzo.3 replaces the temporary
split with one fail-closed policy for every caller, including spawn.
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
        The audit reason recorded on the row, or a fixed availability reason.
    unavailable:
        ``True`` when an opted-in effect check could not read valid permission state.
    """

    allowed: bool
    explicit: bool = False
    reason: str | None = None
    unavailable: bool = False


class PermissionUnavailable(Exception):
    """Permission state could not be read; no effect was attempted."""

    def __init__(self, butler: str, permission: str) -> None:
        self.butler = butler
        self.permission = permission
        self.reason = "permission store unavailable"
        super().__init__("Permission store unavailable; retry when permission state is readable.")


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
    *,
    fail_closed: bool = False,
) -> PermissionStatus:
    """Return whether ``butler`` is granted ``permission`` per ``public.permissions``.

    Non-raising. See the module docstring for the opt-in-deny semantics and the
    staged availability contract.

    Parameters
    ----------
    pool:
        asyncpg pool connected to a database that can read ``public.permissions``
        (the switchboard pool). A missing pool is unavailable, not a missing row.
    butler:
        Butler name (matrix row key).
    permission:
        Permission name (matrix column key, e.g. ``"spawn"`` or ``"email.send"``).
    fail_closed:
        Refuse unavailable state at effect sites. The temporary default for remaining
        shared callers is removed by bu-svqpzo.3.
    """
    if pool is None:
        return _store_unavailable(butler, permission, fail_closed, "MissingPool")

    try:
        row = await pool.fetchrow(_PERMISSION_SELECT_SQL, butler, permission)
    except Exception as exc:
        return _store_unavailable(butler, permission, fail_closed, type(exc).__name__)

    if row is None:
        # No explicit grant/revoke is different from an unreadable store.
        return PermissionStatus(allowed=True)

    try:
        granted = row["granted"]
        reason = row["reason"]
        if fail_closed and (
            not isinstance(granted, bool) or (reason is not None and not isinstance(reason, str))
        ):
            raise ValueError("Invalid permission row")
    except Exception as exc:
        if not fail_closed:
            raise
        return _store_unavailable(butler, permission, fail_closed, type(exc).__name__)
    return PermissionStatus(allowed=bool(granted), explicit=True, reason=reason)


def _store_unavailable(
    butler: str, permission: str, fail_closed: bool, error_class: str
) -> PermissionStatus:
    # Exception messages and row contents can contain private data. Keep diagnostics fixed.
    logger.log(
        logging.ERROR if fail_closed else logging.WARNING,
        "Permission store unavailable: butler=%s permission=%s error_class=%s fail_closed=%s",
        butler,
        permission,
        error_class,
        fail_closed,
    )
    return PermissionStatus(
        allowed=not fail_closed,
        reason="permission store unavailable" if fail_closed else None,
        unavailable=fail_closed,
    )


async def require_permission(
    pool: asyncpg.Pool | None,
    butler: str,
    permission: str,
    *,
    fail_closed: bool = False,
) -> None:
    """Raise :class:`PermissionDenied` when ``butler`` lacks ``permission``.

    Thin raising wrapper over :func:`check_permission` for call sites that prefer
    exception control flow. Effects opt into unavailable-state refusal with
    ``fail_closed=True``; a readable missing row always remains default-allow.
    """
    status = await check_permission(pool, butler, permission, fail_closed=fail_closed)
    if status.unavailable:
        raise PermissionUnavailable(butler, permission)
    if not status.allowed:
        raise PermissionDenied(butler, permission, status.reason)

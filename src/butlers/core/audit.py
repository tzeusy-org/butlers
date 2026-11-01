"""Audit logging primitives: the ``public.audit_log`` writer and the daemon-side helper.

:func:`append` is the single funnel every audit writer goes through (API routers,
jobs, the scheduler and the daemon). :func:`write_audit_entry` is the daemon-level
counterpart of :func:`butlers.api.routers.audit.log_audit_entry` (which requires a
:class:`DatabaseManager`).

``write_audit_entry`` is fire-and-forget: exceptions are logged and swallowed so
that audit logging never blocks or breaks the primary operation.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import asyncpg
from asyncpg.exceptions import UndefinedTableError

from butlers.metrics_registry import get_or_create_counter

logger = logging.getLogger(__name__)

#: The closed vocabulary a credential failure may be described by.
#:
#: Owner Option C: a credential failure is published as a *selection* out of
#: this tuple, never as anything derived from an input string.  A new provider
#: message, a new ``probe_status`` token, or a new HTTP status code therefore
#: cannot widen what escapes -- the worst an unclassifiable failure can do is
#: land on ``other``.
#:
#: It lives here rather than in ``routers/secrets_v2`` (its historical home,
#: which still re-exports it) because two surfaces now need it and they must
#: not drift: the secrets routes publish it as ``TestResult.message``
#: (bu-nz4sn), and ``public.audit_log.failure_category`` stores it at rest
#: (bu-vhie6, migration ``core_202``, whose CHECK constraint mirrors this exact
#: set).
PROBE_FAILURE_VOCABULARY: tuple[str, ...] = (
    "not_set",  # no value is stored for this credential
    "expired",  # the stored value is past a known expiry
    "rejected",  # the provider refused the credential (HTTP 401/403)
    "rate_limited",  # the provider throttled the probe (HTTP 429)
    "provider_error",  # the provider answered, but not with success
    "malformed",  # a value is present but fails this system's format check
    "unverified",  # no live signal this time; an earlier live probe had failed
    "other",
)


def clamp_failure_category(value: str | None) -> str | None:
    """Clamp an already-categorised failure to :data:`PROBE_FAILURE_VOCABULARY`.

    ``None`` passes through (no failure, or a producer that has no category to
    give).  Anything else that is not a vocabulary member collapses to
    ``"other"`` rather than riding along, so a raw ``probe_status`` token, an
    HTTP status code, or a provider string can never reach a wire field or the
    ``failure_category`` column even by mistake.

    Collapsing rather than raising is deliberate: every caller writes audit
    rows fire-and-forget, so raising would drop the whole row to punish a
    mislabelled category, and ``"other"`` is already the honest bucket for a
    failure this system cannot name.
    """
    if value is None:
        return None
    return value if value in PROBE_FAILURE_VOCABULARY else "other"


class AuditTableNotAvailableError(Exception):
    """Raised by ``append()`` when required audit storage cannot produce a receipt.

    Callers (HTTP endpoints) should propagate this as HTTP 503 so that
    missing-table conditions surface explicitly rather than silently failing.
    """


# Prometheus counter -- incremented per successful append to public.audit_log.
audit_log_appended_total = get_or_create_counter(
    "audit_log_appended_total",
    "Number of rows appended to public.audit_log, partitioned by action.",
    labelnames=["action"],
)

_error_row_listeners: list[Callable[[dict[str, Any]], None]] = []


def on_error_row(listener: Callable[[dict[str, Any]], None]) -> None:
    """Register a best-effort callback fired after an error-result row is appended.

    The dashboard API registers one that fans an ``issue`` event onto its live
    event bus (the ``/api/issues`` feed groups exactly these rows). Core knows
    nothing about the bus, so a daemon process with no API loaded simply has no
    listeners.
    """
    if listener not in _error_row_listeners:
        _error_row_listeners.append(listener)


def _native_storage_unavailable(storage: Any) -> bool:
    """Classify driver lifetime state, never InterfaceError message text."""
    if isinstance(storage, asyncpg.Pool):
        return storage.is_closing()
    if isinstance(storage, asyncpg.pool.PoolConnectionProxy) and storage._con is None:
        # A released proxy is an invalid caller reference, not closed storage.
        return False
    if isinstance(storage, asyncpg.Connection):
        return storage.is_closed()
    return False


async def append(
    pool: asyncpg.Pool | asyncpg.Connection | None,
    actor: str,
    action: str,
    *,
    target: str | None = None,
    note: str | None = None,
    ip: str | None = None,
    request_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    result: str | None = None,
    error: str | None = None,
    failure_category: str | None = None,
) -> int:
    """Append one row to ``public.audit_log`` and return the new row id.

    Increments ``audit_log_appended_total{action}`` on success.

    Accepts either an asyncpg pool or an already-acquired connection so that
    callers can run the audit insert inside the same SQL transaction as the
    state change being audited (§D17 atomicity requirement).

    Parameters
    ----------
    pool:
        asyncpg connection pool **or** an existing asyncpg connection.  Pass a
        connection when the caller needs the audit insert to participate in the
        same open transaction (atomicity).
    actor:
        Identity of the actor that triggered the change (e.g. ``"owner"``
        or a butler name).
    action:
        Short, machine-readable verb describing the change
        (e.g. ``"model_priority_change"``).
    target:
        Optional fully-qualified name of the affected resource
        (e.g. ``"butler:qa"`` or ``"rule:42"``).
    note:
        Optional human-readable free-text description of the change.
    ip:
        Optional source IP address as a string (e.g. ``"1.2.3.4"``).
    request_id:
        Optional UUID correlating the audit entry to an HTTP request.
    metadata:
        Optional structured context dict persisted to the ``metadata`` JSONB
        column (core_122).  Non-JSON-safe values (UUID, datetime, …) are
        coerced to strings before storage via a ``json.dumps``/``json.loads``
        round-trip (the resulting dict is bound directly, not the
        intermediate JSON string — see the implementation note below).
        ``None`` stores SQL ``NULL``.
    result:
        Optional outcome label persisted to the ``result`` column (core_122),
        e.g. ``"success"`` or ``"error"``.
    error:
        Optional error message persisted to the ``error`` column (core_122);
        only meaningful when *result* denotes a failure.
    failure_category:
        Optional cause of the failure, persisted to the ``failure_category``
        column (core_202).  Clamped to :data:`PROBE_FAILURE_VOCABULARY` on the
        way in, so a raw ``probe_status`` token, a provider HTTP code, or any
        free text collapses to ``"other"`` rather than being stored.  This is
        the only per-row signal a credential-target audit *group* may be
        identified by, because every free-text column on such a row is withheld
        on read (bu-ove06/bu-uqipv) -- deriving the cause at read time would
        mean parsing exactly that withheld text.  Leave ``None`` for a success
        row and for any producer that cannot name its cause out of the
        vocabulary.

    The three core_122 parameters and *failure_category* are keyword-only and
    default to ``None`` so every existing caller is unaffected.

    Returns
    -------
    int
        The ``id`` of the newly-inserted row.

    Raises
    ------
    AuditTableNotAvailableError
        When required storage is unavailable or cannot return an audit receipt.
        Callers should propagate this as HTTP 503.
    """
    if pool is None or _native_storage_unavailable(pool):
        raise AuditTableNotAvailableError("Required audit storage is not available")

    # Sanitize metadata into a fully JSON-safe dict (UUID/datetime -> str) via
    # a json.dumps/loads round-trip. Bind the resulting DICT directly (no
    # json.dumps, no ::jsonb cast): every asyncpg pool in this codebase
    # registers register_jsonb_codec() (src/butlers/db.py), whose encoder
    # expects a Python object and calls json.dumps() on it itself.
    # Passing an ALREADY-serialized JSON string with an explicit ::jsonb cast
    # makes that encoder fire a SECOND time, double-encoding the value into a
    # jsonb-typed STRING instead of an OBJECT — this codebase has hit the exact
    # same anti-pattern twice before (bu-qki26, bu-aaacv; see
    # tests/relationship/test_jsonb_codec.py). None stays None -> SQL NULL.
    safe_metadata = json.loads(json.dumps(metadata, default=str)) if metadata is not None else None

    # Clamped here rather than trusted from the caller: this is the single
    # funnel every audit writer goes through, and core_202's CHECK constraint
    # would otherwise reject the whole row (audit writes are fire-and-forget,
    # so a rejected row is a silently lost row).
    safe_failure_category = clamp_failure_category(failure_category)
    if failure_category is not None and safe_failure_category != failure_category:
        # The rejected value is deliberately NOT logged: the only way a
        # non-member reaches here is a producer handing over free text, and a
        # log line is one more place that text would land.
        logger.warning(
            "audit.append: failure_category for action=%s was not a vocabulary "
            "member; stored as 'other'",
            action,
        )

    try:
        row_id: int = await pool.fetchval(
            "INSERT INTO public.audit_log "
            "(actor, action, target, note, ip, request_id, metadata, result, error, "
            "failure_category, writer_role) "
            "VALUES ($1, $2, $3, $4, $5::inet, $6, $7, $8, $9, $10, CURRENT_USER) "
            "RETURNING id",
            actor,
            action,
            target,
            note,
            ip,
            request_id,
            safe_metadata,
            result,
            error,
            safe_failure_category,
        )
    except asyncpg.InterfaceError as exc:
        # Native close can race the initial lifetime check. Other InterfaceError
        # states (uninitialized pools, released proxies, query misuse) retain
        # their programming-error identity instead of becoming HTTP 503.
        if not _native_storage_unavailable(pool):
            raise
        raise AuditTableNotAvailableError("Required audit storage is not available") from exc
    except (
        UndefinedTableError,
        asyncpg.UndefinedColumnError,
        asyncpg.InsufficientPrivilegeError,
        asyncpg.PostgresConnectionError,
    ) as exc:
        raise AuditTableNotAvailableError(
            "Required public.audit_log storage is unavailable or not migrated"
        ) from exc
    if type(row_id) is not int or row_id <= 0:
        raise AuditTableNotAvailableError("Required audit storage returned no receipt")
    audit_log_appended_total.labels(action=action).inc()

    # Notify listeners (the dashboard's live "issue" event) whenever an
    # error-result row lands. Best-effort: never let a listener block or fail
    # the primary audit write.
    if result == "error":
        for listener in _error_row_listeners:
            try:
                listener(
                    {"actor": actor, "action": action, "target": target, "error": error},
                )
            except Exception:
                logger.debug("audit error-row listener failed (non-fatal)", exc_info=True)

    return row_id


async def write_audit_entry(
    pool: asyncpg.Pool | None,
    butler: str,
    operation: str,
    request_summary: dict,
    result: str = "success",
    error: str | None = None,
) -> None:
    """Append an audit log entry to the canonical ``public.audit_log`` table.

    Daemon-side compatibility shim that maps the legacy ``dashboard_audit_log``
    field shape onto :func:`append` (bu-h47nm):

    - ``butler``        -> ``actor``
    - ``operation``     -> ``action``
    - ``request_summary.path`` -> ``target`` (when present)
    - ``request_summary`` -> ``metadata`` JSONB
    - ``result`` / ``error`` -> the ``result`` / ``error`` columns

    Writes ONLY to ``public.audit_log``; the read surface UNIONs the legacy
    table so readers still see every row.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  The insert is fully schema-qualified
        (``public.audit_log``) so any reachable pool works.  If ``None``, the
        call is a silent no-op.
    butler:
        Butler name that produced the activity.
    operation:
        Activity type (e.g. ``"session"``).
    request_summary:
        Arbitrary dict stored on the ``metadata`` JSONB column.  Non-JSON-safe
        values are coerced to strings.
    result:
        ``"success"`` or ``"error"``.
    error:
        Error message (only meaningful when *result* is ``"error"``).
    """
    if pool is None:
        return

    # Coerce non-JSON-safe values (e.g. UUID, datetime) to strings before they
    # land on the ``metadata`` JSONB column.
    safe_summary = json.loads(json.dumps(request_summary, default=str))

    target = safe_summary.get("path")
    target_str = str(target) if target else None

    metadata: dict[str, Any] = {"request_summary": safe_summary}

    try:
        await append(
            pool,
            butler,
            operation,
            target=target_str,
            metadata=metadata,
            result=result,
            error=error,
        )
    except AuditTableNotAvailableError:
        logger.warning(
            "Audit table unavailable, dropping daemon audit entry: butler=%s operation=%s",
            butler,
            operation,
        )
    except Exception:
        logger.warning(
            "Failed to write daemon audit entry: butler=%s operation=%s",
            butler,
            operation,
            exc_info=True,
        )

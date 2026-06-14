"""backfill switchboard.dashboard_audit_log history into public.audit_log.

Issue: bu-j26e8.  Parent epic: bu-t141i (audit-unify) — final step (.4).

Context
-------
The audit-unify epic consolidated all audit writes onto the canonical
``public.audit_log`` primitive:

  * .1 (bu-a3jtj) extended ``public.audit_log`` with ``metadata``/``result``/
    ``error`` columns (core_122) and added the ``append()`` helper.
  * .2 (bu-fyal7) made the read surfaces UNION the legacy Switchboard
    ``dashboard_audit_log`` table with ``public.audit_log`` so old rows stayed
    visible.
  * .3 (bu-h47nm) re-routed every writer onto ``public.audit_log`` ONLY — zero
    new rows land in the legacy table.

This migration is .4(a): it copies the *historical* legacy rows (everything
written before the .3 cutover) into ``public.audit_log`` so the canonical table
becomes the single source of truth.  Once the data lives in the canonical table,
.4(b) removes the legacy UNION read arms (a code change, not part of this
migration), and reads come solely from ``public.audit_log``.  The legacy table
itself is NOT dropped here (deferred cleanup).

Column mapping (mirrors .3's ``log_audit_entry`` writer)
--------------------------------------------------------
    dashboard_audit_log            public.audit_log
    ------------------------------ ----------------------------------------
    butler                      -> actor
    operation                   -> action
    request_summary->>'path'    -> target              (when present)
    created_at                  -> ts                  (preserve history)
    result                      -> result
    error                       -> error
    request_summary             -> metadata.request_summary
    user_context                -> metadata.user_context   (when non-empty)
                                -> metadata.backfill_source = 'core_124:dashboard_audit_log'
                                -> metadata.legacy_id       = <legacy uuid> (dedup key)

Cross-chain / topology guard (cross-chain-migration-drop-hazard)
----------------------------------------------------------------
The legacy table is owned by the Switchboard chain.  In the schema-isolated
production topology it lives at ``switchboard.dashboard_audit_log``; in the
flat-public test/single-DB topology it lives at ``public.dashboard_audit_log``.
``_legacy_table()`` resolves the real location with ``to_regclass`` and the
migration is a complete **no-op** when neither exists (fresh DB / sibling chain
that has not provisioned the table).  Every reference is guarded.

Idempotency
-----------
Re-running must never duplicate rows.  Each backfilled row stamps
``metadata->>'legacy_id'`` with the source row's UUID; the INSERT skips any
legacy row whose ``legacy_id`` is already present in ``public.audit_log``.  So a
second run inserts nothing.

Reversibility
-------------
``downgrade()`` is intentionally a guarded no-op: ``public.audit_log`` is
append-only by policy (enforced by a static-check test) and the backfilled rows
are *real audit history*.  Deleting them on downgrade would silently destroy the
historical record we just preserved; the legacy table still holds the originals
should a true rollback ever be required.  See module docstring of core_092.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision = "core_124"
down_revision = "core_123"
branch_labels = None
depends_on = None

_BACKFILL_SOURCE = "core_124:dashboard_audit_log"

# Candidate locations for the legacy table, most-specific first.  Production is
# schema-isolated (``switchboard``); the flat single-DB / test topology lands it
# in ``public``.
_LEGACY_CANDIDATES = ("switchboard.dashboard_audit_log", "public.dashboard_audit_log")


def _legacy_table(bind) -> str | None:
    """Resolve the fully-qualified legacy table name, or None when absent.

    Guards every reference with ``to_regclass`` so the migration is a complete
    no-op on databases where the Switchboard chain's ``dashboard_audit_log`` was
    never provisioned (fresh DB / sibling chain).
    """
    for candidate in _LEGACY_CANDIDATES:
        if bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": candidate}).scalar() is not None:
            return candidate
    return None


# Backfill: copy every legacy row whose UUID is not already represented in
# public.audit_log (keyed on metadata->>'legacy_id'), preserving created_at as
# ts.  The metadata JSONB mirrors the shape .3's log_audit_entry writer stores
# ({"request_summary": ..., "user_context": ...}) plus a stable source marker +
# legacy_id dedup key.  ``{table}`` is interpolated from the to_regclass-verified
# allow-list above (never user input), so it is injection-safe.
_BACKFILL_TEMPLATE = """
    INSERT INTO public.audit_log
        (ts, actor, action, target, result, error, metadata)
    SELECT
        src.created_at,
        src.butler,
        src.operation,
        NULLIF(src.request_summary->>'path', ''),
        src.result,
        src.error,
        (
            jsonb_build_object(
                'request_summary', COALESCE(src.request_summary, '{{}}'::jsonb),
                'backfill_source', '%(source)s',
                'legacy_id', src.id::text
            )
            || CASE
                 WHEN src.user_context IS NOT NULL
                      AND src.user_context <> '{{}}'::jsonb
                 THEN jsonb_build_object('user_context', src.user_context)
                 ELSE '{{}}'::jsonb
               END
        ) AS metadata
    FROM {table} AS src
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.audit_log al
        WHERE al.metadata->>'legacy_id' = src.id::text
    )
"""


def _backfill_sql(table: str) -> str:
    return _BACKFILL_TEMPLATE.format(table=table) % {"source": _BACKFILL_SOURCE}


def upgrade() -> None:
    bind = op.get_bind()

    # public.audit_log is the backfill destination; if it is somehow absent
    # (core_092 not applied) there is nothing we can do — bail safely.
    if bind.execute(sa.text("SELECT to_regclass('public.audit_log')")).scalar() is None:
        logger.warning("core_124: public.audit_log absent — skipping backfill")
        return

    legacy = _legacy_table(bind)
    if legacy is None:
        # Legacy table never provisioned in this database (fresh DB / sibling
        # chain): nothing to backfill.  No-op.
        logger.info("core_124: dashboard_audit_log absent — nothing to backfill")
        return

    result = bind.execute(sa.text(_backfill_sql(legacy)))
    copied = result.rowcount if result.rowcount is not None else -1
    logger.info(
        "core_124: backfilled %s legacy audit row(s) from %s into public.audit_log",
        copied,
        legacy,
    )


def downgrade() -> None:
    # Intentional no-op: the backfilled rows are real, append-only audit history.
    # Deleting them would silently destroy the record this migration preserved;
    # the legacy dashboard_audit_log table still holds the originals if a genuine
    # rollback is ever required.  (public.audit_log is append-only by policy.)
    logger.info(
        "core_124 downgrade: no-op by design — backfilled audit history is "
        "append-only and is not deleted on downgrade."
    )

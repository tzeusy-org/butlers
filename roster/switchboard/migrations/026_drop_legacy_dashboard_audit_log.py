"""Drop the legacy switchboard.dashboard_audit_log table.

Revision ID: sw_026
Revises: sw_025
Create Date: 2026-07-15 00:00:00.000000

bu-o699b: retire the last artifact of the dashboard-audit unification. The
legacy ``dashboard_audit_log`` table (created by switchboard migration 001) was
fully superseded by ``public.audit_log``:

  * bu-a3jtj extended ``public.audit_log`` with metadata/result/error columns.
  * bu-h47nm re-routed every writer (``emit_dashboard_audit`` / the dashboard
    audit middleware) onto ``public.audit_log`` ONLY — no code writes the legacy
    table any more.
  * bu-j26e8 removed the live UNION read arm; ``/api/audit-log`` and the audit
    grouping/egress endpoints read ``public.audit_log`` exclusively.
  * core_124 backfilled every historical legacy row into ``public.audit_log``,
    stamping ``metadata->>'legacy_id'`` (source UUID) and
    ``metadata->>'backfill_source' = 'core_124:dashboard_audit_log'``.

Live verification on butlers-db-dev (2026-07-15, read-only) before this drop:
740,904 legacy rows; 740,904 rows in public.audit_log carrying the core_124
backfill_source; 0 unparitied legacy rows; newest legacy row 2026-06-14 (~1
month stale, write-orphaned). So dropping the table loses no data.

Self-guarding destructive drop (cross-chain drop hazard doctrine):

  1. ``to_regclass`` resolves the table in either topology -- production runs the
     switchboard chain with ``search_path=switchboard`` so it lives at
     ``switchboard.dashboard_audit_log``; the flat-public single-DB/test topology
     puts it at ``public.dashboard_audit_log``. Absent -> clean no-op.
  2. PRE-DROP PARITY RE-ASSERT: if the legacy table still holds rows, every row's
     id must already be present as a core_124-stamped ``legacy_id`` in
     ``public.audit_log``; otherwise RAISE and abort (never drop unparitied
     history). An empty legacy table (fresh test DB) skips straight to the drop.
  3. Row counts are logged (RAISE NOTICE) before the drop as the snapshot.

Switchboard migration 001 (which creates the table) is intentionally left
untouched -- migration history is immutable; the ``to_regclass`` guard handles
fresh-DB ordering (001 creates, 026 drops).

``downgrade()`` is a documented no-op: the dropped rows are preserved verbatim in
``public.audit_log`` (core_124), so re-creating an empty legacy stub would
restore nothing and re-introduce the cruft this migration removes.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_026"
down_revision = "sw_025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            legacy_tbl   regclass;
            legacy_n     bigint;
            unparitied_n bigint;
        BEGIN
            -- 1. Resolve the legacy table in either topology (switchboard schema
            --    in production, public in flat/test); clean no-op if absent.
            legacy_tbl := COALESCE(
                to_regclass('switchboard.dashboard_audit_log'),
                to_regclass('public.dashboard_audit_log')
            );
            IF legacy_tbl IS NULL THEN
                RAISE NOTICE
                    'sw_026: dashboard_audit_log absent; nothing to drop (no-op)';
                RETURN;
            END IF;

            EXECUTE format('SELECT count(*) FROM %s', legacy_tbl) INTO legacy_n;

            -- 2. Pre-drop parity re-assert: never drop unbackfilled history.
            IF legacy_n > 0 THEN
                IF to_regclass('public.audit_log') IS NULL THEN
                    RAISE EXCEPTION
                        'sw_026: legacy dashboard_audit_log has % rows but '
                        'public.audit_log is absent; refusing to drop', legacy_n;
                END IF;
                EXECUTE format(
                    'SELECT count(*) FROM %s d WHERE NOT EXISTS ('
                    '  SELECT 1 FROM public.audit_log a'
                    '  WHERE a.metadata->>''backfill_source'' ='
                    '        ''core_124:dashboard_audit_log'''
                    '    AND a.metadata->>''legacy_id'' = d.id::text)',
                    legacy_tbl
                ) INTO unparitied_n;
                IF unparitied_n > 0 THEN
                    RAISE EXCEPTION
                        'sw_026: % of % legacy dashboard_audit_log rows are NOT '
                        'backfilled into public.audit_log; refusing to drop (run '
                        'the core_124 backfill first)', unparitied_n, legacy_n;
                END IF;
            END IF;

            -- 3. Snapshot + drop.
            RAISE NOTICE
                'sw_026: dropping % (legacy_rows=%, all backfilled into '
                'public.audit_log)', legacy_tbl, legacy_n;
            EXECUTE format('DROP TABLE %s', legacy_tbl);
        END $$;
        """
    )


def downgrade() -> None:
    # Intentional no-op: the dropped rows survive verbatim in public.audit_log
    # (core_124 backfill), so re-creating an empty legacy stub would restore no
    # data and merely re-introduce the retired cruft.
    pass

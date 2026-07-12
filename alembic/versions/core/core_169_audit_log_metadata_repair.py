"""audit_log: batched repair of string-typed metadata + dead ack cleanup.

Revision ID: core_169
Revises: core_168
Create Date: 2026-07-12 00:00:00.000000

Note (bu-hmdqz.4): originally authored as core_168, off core_167. Renumbered
to core_169 (down_revision core_168) because PR #3171 (bu-hmdqz.3, attention
ledger 'failed' outcome) claimed core_168 off core_167 first. If #3171 has
not yet merged when this PR is opened, core_168 will not exist on main yet;
this migration is still declared against it and goes green once #3171 lands.

bu-hmdqz.4 (2026-07-12 JARVIS pursuit, move 4 "repair the audit/issues drill
spine") -- a since-fixed write path double-JSON-encoded ``metadata`` for a
contiguous band of ``public.audit_log`` rows between 2026-06-14 and 07-05:
instead of storing a JSONB object, it stored the JSON *text* of that object
as a JSONB string (``jsonb_typeof(metadata) = 'string'``). Live-confirmed:
349,113 affected rows, 29% of the table. asyncpg decodes a JSONB string
scalar as a Python ``str``, so ``AuditLogEntry`` (a strict
``dict[str, Any] | None`` field) rejected every one of those rows with a
pydantic ``ValidationError`` -- surfaced as an HTTP 500 that took the entire
audit page down for any query touching a poisoned row (e.g.
``GET /api/audit-log?actor=memory``). The API-level fix (this PR,
``AuditLogEntry._coerce_metadata``) is a tolerant, permanent safety net; THIS
migration is the one-shot structural repair so future scans of the table
stop paying the string-decode tax on almost a third of its rows and any
downstream (non-Python) reader of the column sees the correct shape too.

Append-only exception (openspec/specs/dashboard-audit-log/spec.md)
--------------------------------------------------------------------
``public.audit_log`` is append-only by policy: no row is ever deleted, and
(per the Retention requirement) no row is updated in place. This migration
is a deliberate, narrow, documented exception to the second half of that
policy -- see the spec amendment shipped in the same PR ("One-shot
structural metadata repair is not a retention violation"). It:
  - touches ONLY the ``metadata`` column (never ``ts``/``actor``/``action``/
    ``target``/``result``/``error`` -- the actual historical record),
  - only rewrites rows already proven poisoned (``jsonb_typeof = 'string'``),
  - is idempotent (re-running finds nothing left to repair and no-ops),
  - preserves the original content losslessly (parses the inner JSON text
    back to an object when possible; otherwise wraps it under ``_raw`` --
    the exact same fallback shape ``AuditLogEntry._coerce_metadata`` uses
    live, so a row renders identically whether or not this migration has
    reached it yet).
This is a data-integrity repair of a poisoned write path, not an ordinary
edit, and is not a precedent for updating audit_log rows for any other
reason.

Batching
--------
349k rows is small enough that a single UPDATE would very likely be fine,
but batches by primary-key range anyway (id BETWEEN start AND end, walking
the poisoned band's [MIN(id), MAX(id)]) so no single statement's plan/WAL
record covers the whole poisoned band at once against a table that is also
receiving live concurrent INSERTs from every butler in the fleet.

Note this repo's ``env.py`` wraps an entire migration run in one
``context.begin_transaction()`` (see ``run_migrations_online``), and a
``DO`` block cannot issue ``COMMIT`` -- so batching here does NOT release
row-level locks between batches or provide partial-progress checkpoints;
all locks acquired by every batch (across this SQL block and the
``dismissed_issues`` cleanup below) are held until the migration's single
outer transaction commits at the end, and a failure at any point rolls the
whole repair back atomically, not just the in-flight batch. What batching
still buys: each individual UPDATE statement only plans/touches one 5000-row
slice rather than the full 349k-row band, keeping any one statement's
working set and WAL burst small. Concurrent appenders are never blocked
regardless of transaction length, since they only ever INSERT new rows and
never touch the existing ones being repaired here (row-level locks from an
UPDATE do not contend with unrelated INSERTs).

The batching loop itself runs entirely inside a single PL/pgSQL ``DO``
block (not a Python-level loop calling out per batch) -- this repo's
migration test harness invokes migrations by capturing each
``op.execute()`` call's literal SQL text and replaying it against a real
asyncpg pool (see
``tests/migrations/test_healing_breaker_reset_backfill_migration.py`` for
the established pattern this follows), so all control flow needed at
migration-apply time must live in the SQL text itself.

Ack cleanup (dismissed_issues)
-------------------------------
This PR also re-keys audit-derived Issue groups (``audit_grouping.py``) from
a composite ``{80-char-truncated-slug}::{butler-or-"multiple"}`` key to a
hash of the FULL normalized error message alone -- see that module's
docstring for the two live bugs the composite form had (truncation
collisions, window-dependent butler-set drift). This orphans every
existing ``public.dismissed_issues`` row for the audit-derived lane (any
``issue_key`` of the shape ``audit_error_group:...::...`` or
``scheduled_task_failure:...::...``): the new key format never contains
``::``, so those old rows can structurally never match again.

[decision] Accept the orphaning; do not attempt an automated remap, for two
reasons. First, the old key embeds a *lossy* 80-char-truncated slug of the
error message -- there is no way to algorithmically recover the full,
untruncated ``error_summary`` from the slug alone, so a mechanical remap
could only cover groups that happen to still be live in ``audit_log`` at
migration-run time (by recomputing both the old- and new-format key for
each such group and rewriting any matching ack row), which is incomplete
coverage dressed up as a real fix. Second, duplicating the grouping/hash
logic in raw migration SQL -- separate from the Python source of truth in
``audit_grouping.py`` -- is itself a drift risk this repo's migrations
avoid by convention (no migration in this chain imports application code).
The blast radius of accepting the orphaning is small and self-healing:
acknowledge-until-recurrence (bu-86c4c.15) already treats a stale/
inapplicable ack as fail-open, so the worst case is a previously-acked
group reappearing once in the active feed, requiring a single re-ack click
-- not silent data loss, not a security issue. Given that, this migration
does delete the now-permanently-dead rows themselves (a plain DELETE
against ``dismissed_issues``, NOT ``audit_log`` -- the append-only policy
above does not apply to this table) rather than leaving inert cruft behind
forever. The DELETE predicate matches ONLY the two audit-derived type
prefixes followed by ``::``, so reachability acks (``unreachable::<butler>``,
which do not use this prefix) are untouched.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_169"
down_revision = "core_168"
branch_labels = None
depends_on = None

# Session-local helper: attempts to parse `input` as jsonb, returning NULL
# (instead of raising) when it isn't valid JSON. pg_temp-schema functions are
# automatically dropped when the session/connection closes, so this never
# leaks into the permanent schema. Created unconditionally (cheap, and the
# repair DO block below self-guards on public.audit_log's existence).
CREATE_TRY_PARSE_JSONB_SQL = """
    CREATE OR REPLACE FUNCTION pg_temp.core_169_try_parse_jsonb(input text)
    RETURNS jsonb
    LANGUAGE plpgsql
    AS $fn$
    BEGIN
        RETURN input::jsonb;
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END;
    $fn$;
"""

# Batched repair: walks the poisoned band [MIN(id), MAX(id)] where
# jsonb_typeof(metadata) = 'string' in fixed-size id-range batches, decoding
# each row's inner JSON text back into an object when possible (else
# wrapping it losslessly under `_raw`, mirroring AuditLogEntry._coerce_metadata).
REPAIR_METADATA_SQL = """
    DO $$
    DECLARE
        v_batch_size CONSTANT BIGINT := 5000;
        v_min_id BIGINT;
        v_max_id BIGINT;
        v_batch_start BIGINT;
        v_batch_end BIGINT;
        v_total_repaired BIGINT := 0;
        v_batch_repaired BIGINT;
        v_remaining BIGINT;
    BEGIN
        IF to_regclass('public.audit_log') IS NULL THEN
            RETURN;
        END IF;

        SELECT MIN(id), MAX(id) INTO v_min_id, v_max_id
        FROM public.audit_log
        WHERE jsonb_typeof(metadata) = 'string';

        IF v_min_id IS NULL THEN
            RETURN;
        END IF;

        v_batch_start := v_min_id;
        WHILE v_batch_start <= v_max_id LOOP
            v_batch_end := LEAST(v_batch_start + v_batch_size - 1, v_max_id);

            WITH parsed AS (
                SELECT id, pg_temp.core_169_try_parse_jsonb(metadata #>> '{}') AS val
                FROM public.audit_log
                WHERE id BETWEEN v_batch_start AND v_batch_end
                  AND jsonb_typeof(metadata) = 'string'
            ),
            repaired AS (
                UPDATE public.audit_log
                SET metadata = CASE
                    WHEN jsonb_typeof(parsed.val) = 'object' THEN parsed.val
                    ELSE jsonb_build_object('_raw', audit_log.metadata #>> '{}')
                END
                FROM parsed
                WHERE audit_log.id = parsed.id
                RETURNING 1
            )
            SELECT count(*) INTO v_batch_repaired FROM repaired;

            v_total_repaired := v_total_repaired + v_batch_repaired;
            v_batch_start := v_batch_end + 1;
        END LOOP;

        SELECT count(*) INTO v_remaining
        FROM public.audit_log
        WHERE jsonb_typeof(metadata) = 'string';

        RAISE NOTICE
            'core_169: repaired % row(s) (id range %-%); % still string-typed',
            v_total_repaired, v_min_id, v_max_id, v_remaining;
    END
    $$;
"""

# Cleanup: the re-key (audit_grouping.audit_group_key) permanently orphans
# every dismissed_issues row using the old composite key format for the
# audit-derived lane. See module docstring [decision] for why this is a
# plain DELETE rather than an attempted remap. Reachability acks
# ("unreachable::<butler>") do not match either LIKE pattern and survive.
CLEANUP_DEAD_ACKS_SQL = """
    DO $$
    DECLARE
        v_deleted BIGINT;
    BEGIN
        IF to_regclass('public.dismissed_issues') IS NULL THEN
            RETURN;
        END IF;

        WITH deleted AS (
            DELETE FROM public.dismissed_issues
            WHERE issue_key LIKE 'audit_error_group:%::%'
               OR issue_key LIKE 'scheduled_task_failure:%::%'
            RETURNING 1
        )
        SELECT count(*) INTO v_deleted FROM deleted;

        RAISE NOTICE 'core_169: deleted % dead audit-derived ack row(s)', v_deleted;
    END
    $$;
"""


def upgrade() -> None:
    op.execute(CREATE_TRY_PARSE_JSONB_SQL)
    op.execute(REPAIR_METADATA_SQL)
    op.execute(CLEANUP_DEAD_ACKS_SQL)


def downgrade() -> None:
    # Non-reversible repair, same rationale as core_124/core_166: the
    # original poisoned bytes (a JSON-encoded string masquerading as the
    # metadata object) are not meaningful content worth restoring, and the
    # deleted dismissed_issues rows referenced a key format the application
    # can no longer produce or look up. Nothing to undo.
    pass

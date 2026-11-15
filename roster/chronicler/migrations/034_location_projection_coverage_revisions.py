"""Exact coverage: content digest, config revision, attempt record (bu-s11n0s.7, slice 2).

Revision ID: chronicler_034
Revises: chronicler_033

A coverage record must name exactly what was decided, so a later change can make it stale
without editing it (``butlers.chronicler.projection_coverage``):

* ``location_projection_coverage`` gains ``content_digest`` (the raw row's ``v1:<sha256 hex>``,
  computed by Postgres over every stored column) and ``config_revision`` (the adapter's
  ``v1:<sha256 hex>`` settings digest; for SSID presence it includes the owner's network
  mapping). The primary key becomes ``(adapter_name, raw_source_id, config_revision,
  content_digest)``: a new revision or new row content gets its own record, and older records
  stay as they were. The append-only trigger is unchanged.
* ``location_projection_open_members`` gains ``content_digest``, so a member closing in a later
  run is covered for the content it was read with.
* ``location_projection_attempts`` keeps one row per adapter: the last outcome, success and
  failure times, the failure's exception class, consecutive failures and the last discovery
  count of uncovered settled rows.

Records written before this revision get ``'legacy'`` in both new columns (the column default
fills them; no ``UPDATE`` runs, so the append-only trigger is not involved). ``'legacy'`` never
equals a real digest or revision, so such a record never counts as current coverage: those rows
need a genuine re-evaluation, which is fail-closed. Nothing is deleted.

The tables live in the Chronicler schema only and this chain runs once, in that schema, so no
other schema's catalog is touched. Each step checks the catalog first, so a re-run is a no-op.

Grants: ``butler_chronicler_rw`` keeps SELECT, INSERT on coverage and SELECT, INSERT, DELETE on
open members, and gets SELECT, INSERT, UPDATE on attempts (one row per adapter is updated in
place).

Downgrade restores chronicler_033's shape. It keeps one coverage record per
``(adapter_name, raw_source_id)``, the earliest, which is the record chronicler_033's
first-record-wins key would have kept; the append-only trigger is disabled for that delete only.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "chronicler_034"
down_revision = "chronicler_033"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_COVERAGE = "location_projection_coverage"
_OPEN = "location_projection_open_members"
_ATTEMPTS = "location_projection_attempts"
_ADAPTERS = "('owntracks.points', 'owntracks.place_cluster', 'owntracks.ssid_presence')"
_DIGEST_CHECK = "({col} = 'legacy' OR {col} ~ '^v1:[0-9a-f]{{64}}$')"
_NEW_KEY = "adapter_name, raw_source_id, config_revision, content_digest"


def _guarded(table: str, column: str, ddl: str) -> str:
    # One block, so the column never exists without its check; a re-run only reads the catalog.
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = '{table}'
                  AND column_name = '{column}'
            ) THEN
                {ddl}
            END IF;
        END
        $$;
        """


def upgrade() -> None:
    # The default fills existing rows, then is dropped: new writers must supply the value.
    op.execute(
        _guarded(
            _COVERAGE,
            "config_revision",
            f"""
            ALTER TABLE {_COVERAGE} ADD COLUMN config_revision TEXT NOT NULL DEFAULT 'legacy';
            ALTER TABLE {_COVERAGE} ALTER COLUMN config_revision DROP DEFAULT;
            ALTER TABLE {_COVERAGE} ADD CONSTRAINT {_COVERAGE}_config_revision_check
                CHECK {_DIGEST_CHECK.format(col="config_revision")};
            """,
        )
    )
    op.execute(
        _guarded(
            _COVERAGE,
            "content_digest",
            f"""
            ALTER TABLE {_COVERAGE} ADD COLUMN content_digest TEXT NOT NULL DEFAULT 'legacy';
            ALTER TABLE {_COVERAGE} ALTER COLUMN content_digest DROP DEFAULT;
            ALTER TABLE {_COVERAGE} ADD CONSTRAINT {_COVERAGE}_content_digest_check
                CHECK {_DIGEST_CHECK.format(col="content_digest")};
            """,
        )
    )
    op.execute(
        _guarded(
            _OPEN,
            "content_digest",
            f"""
            ALTER TABLE {_OPEN} ADD COLUMN content_digest TEXT NOT NULL DEFAULT 'legacy';
            ALTER TABLE {_OPEN} ALTER COLUMN content_digest DROP DEFAULT;
            ALTER TABLE {_OPEN} ADD CONSTRAINT {_OPEN}_content_digest_check
                CHECK {_DIGEST_CHECK.format(col="content_digest")};
            """,
        )
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF (
                SELECT array_agg(a.attname::text ORDER BY k.ord)
                FROM pg_constraint c
                CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                WHERE c.conrelid = '{_COVERAGE}'::regclass AND c.contype = 'p'
            ) IS DISTINCT FROM ARRAY['adapter_name', 'raw_source_id', 'config_revision',
                                     'content_digest'] THEN
                ALTER TABLE {_COVERAGE} DROP CONSTRAINT IF EXISTS {_COVERAGE}_pkey;
                ALTER TABLE {_COVERAGE} ADD CONSTRAINT {_COVERAGE}_pkey PRIMARY KEY ({_NEW_KEY});
            END IF;
        END
        $$;
        """
    )
    if op.get_bind().execute(text(f"SELECT to_regclass('{_ATTEMPTS}')")).scalar() is not None:
        return
    op.execute(
        f"""
        CREATE TABLE {_ATTEMPTS} (
            adapter_name TEXT PRIMARY KEY CHECK (adapter_name IN {_ADAPTERS}),
            config_revision TEXT NOT NULL CHECK {_DIGEST_CHECK.format(col="config_revision")},
            last_outcome TEXT NOT NULL CHECK (last_outcome IN ('succeeded', 'failed')),
            last_attempt_at TIMESTAMPTZ NOT NULL,
            last_success_at TIMESTAMPTZ,
            last_failure_at TIMESTAMPTZ,
            last_error_class TEXT CHECK (char_length(last_error_class) BETWEEN 1 AND 200),
            consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
            uncovered_rows INTEGER CHECK (uncovered_rows >= 0),
            uncovered_capped BOOLEAN,
            uncovered_checked_at TIMESTAMPTZ,
            CHECK (last_outcome <> 'failed' OR last_error_class IS NOT NULL)
        )
        """
    )
    op.execute(f"REVOKE ALL ON {_ATTEMPTS} FROM PUBLIC, {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_ATTEMPTS} TO {_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_ATTEMPTS}")
    op.execute(f"ALTER TABLE {_COVERAGE} DISABLE TRIGGER {_COVERAGE}_append_only")
    op.execute(
        f"""
        DELETE FROM {_COVERAGE} AS c
        USING (
            SELECT ctid, row_number() OVER (
                PARTITION BY adapter_name, raw_source_id ORDER BY covered_at, ctid
            ) AS rank
            FROM {_COVERAGE}
        ) AS ranked
        WHERE c.ctid = ranked.ctid AND ranked.rank > 1
        """
    )
    op.execute(f"ALTER TABLE {_COVERAGE} ENABLE TRIGGER {_COVERAGE}_append_only")
    op.execute(f"ALTER TABLE {_COVERAGE} DROP CONSTRAINT IF EXISTS {_COVERAGE}_pkey")
    op.execute(
        f"ALTER TABLE {_COVERAGE} ADD CONSTRAINT {_COVERAGE}_pkey "
        "PRIMARY KEY (adapter_name, raw_source_id)"
    )
    op.execute(f"ALTER TABLE {_COVERAGE} DROP COLUMN IF EXISTS config_revision")
    op.execute(f"ALTER TABLE {_COVERAGE} DROP COLUMN IF EXISTS content_digest")
    op.execute(f"ALTER TABLE {_OPEN} DROP COLUMN IF EXISTS content_digest")

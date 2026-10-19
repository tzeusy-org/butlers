"""Record wake-recovery provenance and protocol states on parked holds (bu-kqnum.3.6.1).

Revision ID: core_272
Revises: core_268
Create Date: 2026-10-19 00:00:00.000000

Slice A of the strict owner Telegram wake recovery
(openspec/changes/strict-owner-telegram-wake-recovery, tasks 1.1, 3.1, 3.3).
``deferred_notifications`` (origin schema) gains:

* seven nullable ``wake_*`` provenance columns: hold kind, policy-window key, an
  origin-local admission sequence, the policy timezone, the resolved Telegram target, the
  resolved-envelope digest and the original stored ``deliver_at``.  A legacy row, and any
  explicit-target, priority, retry or context-only hold, carries all of them NULL.
  ``chk_deferred_notifications_wake_provenance`` makes the set all-or-nothing, and
  ``deferred_notifications_wake_provenance_guard`` (BEFORE UPDATE) refuses any later change
  to a ``wake_*`` column, so provenance is written with the hold and never edited or added
  afterwards.
* ``deferred_notifications_admission_seq``, the origin-local admission sequence the writer
  draws from in the same INSERT as the row.
* the ``release_*`` protocol states in ``chk_deferred_notifications_status``.  A later slice
  moves rows into them; the ordinary scheduler only ever reads ``pending``.
  ``chk_deferred_notifications_protocol_state`` allows them only on a row that carries
  provenance, so a legacy row can never be in a protocol state.

The downgrade refuses while any row is in a protocol state (those rows have no pre-protocol
status to map back to), then drops the guard, constraints, columns and sequence.  Code that
predates this revision leaves the nullable columns untouched.

The core chain runs once per butler schema, so every statement targets ``current_schema()``
through the search path (core_082 pattern) and is skipped where the table does not exist.
"""

from __future__ import annotations

from alembic import op

revision = "core_272"
down_revision = "core_268"
branch_labels = None
depends_on = None

_TABLE = "deferred_notifications"
_SEQUENCE = "deferred_notifications_admission_seq"
_GUARD = "deferred_notifications_wake_provenance_guard"
_STATUS_CHECK = "chk_deferred_notifications_status"
_PROVENANCE_CHECK = "chk_deferred_notifications_wake_provenance"
_PROTOCOL_CHECK = "chk_deferred_notifications_protocol_state"
_SEQUENCE_INDEX = "uq_deferred_notifications_wake_admission_sequence"

_ORDINARY_STATUSES = "'pending', 'delivered', 'expired', 'cancelled'"
_PROTOCOL_STATUSES = (
    "'release_prepared', 'release_committed', 'release_retained_dnd', "
    "'release_retained_unavailable', 'release_retained_oversize', "
    "'release_retained_mismatch', 'release_retained_commit_error'"
)
_WAKE_COLUMNS = (
    "wake_hold_kind",
    "wake_policy_window_key",
    "wake_admission_sequence",
    "wake_timezone",
    "wake_target",
    "wake_envelope_digest",
    "wake_original_deliver_at",
)
_ALL_NULL = " AND ".join(f"{column} IS NULL" for column in _WAKE_COLUMNS)
_ALL_SET = " AND ".join(f"{column} IS NOT NULL" for column in _WAKE_COLUMNS)
_CHANGED = " OR ".join(f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in _WAKE_COLUMNS)


def upgrade() -> None:
    op.execute(f"""
        DO $$
        DECLARE
            runtime_role TEXT := 'butler_' || current_schema() || '_rw';
        BEGIN
            IF to_regclass('{_TABLE}') IS NULL THEN
                RETURN;
            END IF;

            CREATE SEQUENCE IF NOT EXISTS {_SEQUENCE};

            ALTER TABLE {_TABLE}
                ADD COLUMN IF NOT EXISTS wake_hold_kind TEXT,
                ADD COLUMN IF NOT EXISTS wake_policy_window_key TEXT,
                ADD COLUMN IF NOT EXISTS wake_admission_sequence BIGINT,
                ADD COLUMN IF NOT EXISTS wake_timezone TEXT,
                ADD COLUMN IF NOT EXISTS wake_target JSONB,
                ADD COLUMN IF NOT EXISTS wake_envelope_digest TEXT,
                ADD COLUMN IF NOT EXISTS wake_original_deliver_at TIMESTAMPTZ;

            ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_STATUS_CHECK};
            ALTER TABLE {_TABLE} ADD CONSTRAINT {_STATUS_CHECK}
                CHECK (status IN ({_ORDINARY_STATUSES}, {_PROTOCOL_STATUSES}));

            ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_PROVENANCE_CHECK};
            ALTER TABLE {_TABLE} ADD CONSTRAINT {_PROVENANCE_CHECK}
                CHECK (
                    ({_ALL_NULL})
                    OR (wake_hold_kind = 'owner_attention_quiet_hours' AND {_ALL_SET})
                );

            ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_PROTOCOL_CHECK};
            ALTER TABLE {_TABLE} ADD CONSTRAINT {_PROTOCOL_CHECK}
                CHECK (status IN ({_ORDINARY_STATUSES}) OR wake_hold_kind IS NOT NULL);

            CREATE UNIQUE INDEX IF NOT EXISTS {_SEQUENCE_INDEX}
                ON {_TABLE} (wake_admission_sequence)
                WHERE wake_admission_sequence IS NOT NULL;

            CREATE OR REPLACE FUNCTION {_GUARD}() RETURNS trigger
            LANGUAGE plpgsql AS $fn$
            BEGIN
                IF {_CHANGED} THEN
                    RAISE EXCEPTION 'deferred_notifications: wake provenance is immutable'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END
            $fn$;

            DROP TRIGGER IF EXISTS {_GUARD} ON {_TABLE};
            CREATE TRIGGER {_GUARD}
                BEFORE UPDATE ON {_TABLE}
                FOR EACH ROW EXECUTE FUNCTION {_GUARD}();

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_role) THEN
                EXECUTE format(
                    'GRANT USAGE, SELECT ON SEQUENCE %I.%I TO %I',
                    current_schema(), '{_SEQUENCE}', runtime_role
                );
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NULL THEN
                RETURN;
            END IF;

            IF EXISTS (
                SELECT 1 FROM {_TABLE} WHERE status IN ({_PROTOCOL_STATUSES})
            ) THEN
                RAISE EXCEPTION
                    'core_272 downgrade refused: deferred_notifications rows are in a '
                    'wake-recovery protocol state and have no pre-protocol status to map back to';
            END IF;

            DROP TRIGGER IF EXISTS {_GUARD} ON {_TABLE};
            DROP FUNCTION IF EXISTS {_GUARD}();
            DROP INDEX IF EXISTS {_SEQUENCE_INDEX};
            ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_PROTOCOL_CHECK};
            ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_PROVENANCE_CHECK};
            ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_STATUS_CHECK};
            ALTER TABLE {_TABLE} ADD CONSTRAINT {_STATUS_CHECK}
                CHECK (status IN ({_ORDINARY_STATUSES}));
            ALTER TABLE {_TABLE}
                DROP COLUMN IF EXISTS wake_hold_kind,
                DROP COLUMN IF EXISTS wake_policy_window_key,
                DROP COLUMN IF EXISTS wake_admission_sequence,
                DROP COLUMN IF EXISTS wake_timezone,
                DROP COLUMN IF EXISTS wake_target,
                DROP COLUMN IF EXISTS wake_envelope_digest,
                DROP COLUMN IF EXISTS wake_original_deliver_at;
            DROP SEQUENCE IF EXISTS {_SEQUENCE};
        END
        $$;
    """)

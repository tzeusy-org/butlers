"""Per-row projection coverage for raw OwnTracks fixes (bu-s11n0s.7, slice 2).

Revision ID: chronicler_032
Revises: chronicler_031

Raw OwnTracks fixes may expire only once every Chronicler projection that reads them has
genuinely finished with them. This revision records that fact per raw row and per adapter. It
deletes nothing and enforces nothing: ``location_retention.ENFORCEMENT_STATUS`` stays
``not_enforced``.

``location_projection_coverage`` holds one terminal record per ``(adapter_name, raw_source_id)``:

* ``raw_source_id`` is ``connectors.owntracks_points.id`` (no cross-schema foreign key: the
  raw row is meant to be deleted later while this receipt stays);
* ``logical_source_key`` is the row's ``idempotency_key`` and ``retention_at`` is the row's
  immutable retention clock (core_340), both copied as the adapter read them;
* ``disposition`` is ``projected`` (at least one output id) or ``evaluated_no_output`` (the
  adapter finished the row and deliberately emitted nothing);
* ``output_event_ids`` / ``output_episode_ids`` name the ``point_events`` / ``episodes`` rows
  the adapter itself wrote for the row; ``projection_batch_id`` is the adapter run transaction.

A record is written once and never weakened: writers insert with ``ON CONFLICT DO NOTHING``, so
the first terminal record wins, and a trigger refuses ``UPDATE`` and ``DELETE``. A later replay
or correction that re-projects a covered row does not append outputs here: the row was already
complete, and replay is idempotent by ``source_ref``. Invalidating coverage after an owner
correction is a later slice's concern.

``location_projection_open_members`` holds raw rows absorbed into a still-open carry, such as
the open movement segment ``owntracks.points`` keeps in checkpoint carryover. ``carry_key``
names the carry, and ``output_*_ids`` accumulate what the row has produced so far. When the
carry closes its members move to coverage in the same transaction and leave this table. A row
in an open carry is never covered; a row that is both covered (by an earlier terminal record)
and re-absorbed by a replay stays listed here, so a deleter must require coverage AND absence
from this table.

The adapter writes both tables on its projection transaction, inside the projection savepoint,
so coverage commits or rolls back with its outputs and its cursor.

Grants follow chronicler_030: ``REVOKE ALL ... FROM PUBLIC`` then explicit grants to
``butler_chronicler_rw`` only (coverage: SELECT, INSERT; open members: SELECT, INSERT, DELETE).
The creator's default privileges would otherwise grant the runtime role full DML, so it is
revoked from that role first. An init-db replay re-grants schema-wide DML, which is why the
coverage table's append-only rule is a trigger, not a grant.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_032"
down_revision = "chronicler_031"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_ADAPTERS = "('owntracks.points', 'owntracks.place_cluster', 'owntracks.ssid_presence')"
_GUARD = "chronicler.location_projection_coverage_append_only()"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS location_projection_coverage (
            adapter_name TEXT NOT NULL CHECK (adapter_name IN {_ADAPTERS}),
            raw_source_id UUID NOT NULL,
            logical_source_key TEXT NOT NULL,
            retention_at TIMESTAMPTZ NOT NULL,
            disposition TEXT NOT NULL CHECK (disposition IN ('projected', 'evaluated_no_output')),
            output_event_ids UUID[] NOT NULL DEFAULT '{{}}',
            output_episode_ids UUID[] NOT NULL DEFAULT '{{}}',
            projection_batch_id UUID NOT NULL,
            covered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (adapter_name, raw_source_id),
            CHECK (
                disposition <> 'projected'
                OR cardinality(output_event_ids) + cardinality(output_episode_ids) > 0
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS location_projection_coverage_retention_idx
        ON location_projection_coverage (retention_at)
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS location_projection_open_members (
            adapter_name TEXT NOT NULL CHECK (adapter_name IN {_ADAPTERS}),
            carry_key TEXT NOT NULL CHECK (carry_key <> ''),
            raw_source_id UUID NOT NULL,
            logical_source_key TEXT NOT NULL,
            retention_at TIMESTAMPTZ NOT NULL,
            output_event_ids UUID[] NOT NULL DEFAULT '{{}}',
            output_episode_ids UUID[] NOT NULL DEFAULT '{{}}',
            joined_batch_id UUID NOT NULL,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (adapter_name, raw_source_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS location_projection_open_members_carry_idx
        ON location_projection_open_members (adapter_name, carry_key)
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD}
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            RAISE EXCEPTION 'location projection coverage is append-only'
                USING ERRCODE = '55000';
        END;
        $fn$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS location_projection_coverage_append_only "
        "ON location_projection_coverage"
    )
    op.execute(
        f"""
        CREATE TRIGGER location_projection_coverage_append_only
        BEFORE UPDATE OR DELETE ON location_projection_coverage
        FOR EACH ROW EXECUTE FUNCTION {_GUARD}
        """
    )
    op.execute(f"REVOKE ALL ON location_projection_coverage FROM PUBLIC, {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON location_projection_coverage TO {_ROLE}")
    op.execute(f"REVOKE ALL ON location_projection_open_members FROM PUBLIC, {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON location_projection_open_members TO {_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS location_projection_open_members")
    op.execute("DROP TABLE IF EXISTS location_projection_coverage")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}")

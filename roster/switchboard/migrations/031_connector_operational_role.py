"""connector_registry: persist operational_role + parent_endpoint_identity.

Revision ID: sw_031
Revises: sw_030
Create Date: 2026-08-22 00:00:00.000000

bu-6jv4m.11. ``connector_registry`` has two independent producers: the
``connector.heartbeat`` tool (which registers an executing process) and
``cursor_store.save_cursor`` (which persists a restart-safe checkpoint). A
connector whose streams advance independently — Google Health keeps one cursor
per account *and* per resource — therefore accumulates registry rows that never
receive a heartbeat.

Nothing in the schema recorded which kind of row was which, so every read path
had to infer it. ``sw_028`` inferred it from column nullability for the QA
liveness view; the ingestion roster inferred nothing at all and presented every
non-deleted row as a connector, so Google Health's activity/sleep/HRV cursors
appeared as separate OFFLINE listening connectors beside the single genuinely
online account, dragging fleet attention with them.

operational_role
----------------
``TEXT NOT NULL DEFAULT 'unknown'``, CHECK-constrained to
``runtime_instance | checkpoint | unknown``. Written by whichever producer owns
the row (see :mod:`butlers.connectors.registry_roles` for the semantics):

- ``runtime_instance`` — an executable connector process. The ONLY role with
  runtime-health authority: rosters, attention, KPIs and fleet liveness count
  these and nothing else.
- ``checkpoint`` — storage state for one stream of a parent runtime instance.
  No heartbeat, therefore no liveness and no status authority.
- ``unknown`` — role not established. A named unavailable state, never inferred
  into active or healthy.

parent_endpoint_identity
------------------------
``TEXT NULL`` — for a ``checkpoint`` row, the ``endpoint_identity`` of the
``runtime_instance`` it belongs to, within the same ``connector_type``. NULL on
runtime instances, and NULL on a checkpoint whose parent is not (yet) known.
This is the runtime-instance relationship the read paths use to keep checkpoint
history inspectable under its parent.

Backfill
--------
Deterministic and idempotent, from persisted evidence only — never from the
shape of the opaque ``endpoint_identity`` string:

1. A row that carries a process identity or has ever heartbeated is a
   ``runtime_instance``. Both are facts only the heartbeat producer can write.
2. A remaining row with no process identity, no heartbeat, and a persisted
   cursor is a ``checkpoint`` — exactly ``sw_028``'s predicate, promoted from a
   per-query inference to a stored fact.
3. Anything else stays ``unknown`` and surfaces as unavailable.

Parent attachment then links each ``checkpoint`` to the LONGEST
``runtime_instance`` identity of the same ``connector_type`` that its identity
extends by a ``:``-delimited suffix. This reads the registry's own runtime rows
rather than pattern-matching a hardcoded connector shape, so Google Health's
``google_health:user:<email>:<account_uuid>:<resource>`` cursors attach to the
``google_health:user:<email>`` account that owns them, per-account, with no
connector-specific SQL. Longest-match keeps a cursor attached to the most
specific real parent when several runtime identities nest. The comparison uses
``left(...) = parent || ':'`` rather than ``LIKE`` so an identity containing
``%`` or ``_`` cannot match the wrong parent.

Checkpoint rows that never advanced (``checkpoint_updated_at IS NULL``) are
matched by the same rules; the classification keys on the cursor's presence,
not its freshness.

v_qa_connector_state
--------------------
Re-pointed at the persisted column: the view now excludes rows by
``operational_role <> 'checkpoint'`` instead of re-deriving ``sw_028``'s
nullability heuristic. The column list is unchanged, so
``InfraStateSource`` is unaffected. A process identity still appears before its
first heartbeat (the heartbeat producer stamps ``runtime_instance`` on the
insert), preserving the registration grace-window semantics, and an ``unknown``
row stays visible so it can be investigated rather than silently dropped.

Downgrade
---------
Restores ``sw_028``'s inferred-predicate view, then drops the indexes and both
columns. Role and parent assignments are lost, which is acceptable because
downgrade is only used in dev/test environments — and the backfill above
reconstructs both from the same evidence on the next upgrade.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_031"
down_revision = "sw_030"
branch_labels = None
depends_on = None


_CONNECTOR_VIEW_FQN = "public.v_qa_connector_state"

#: sw_028's view body, minus its trailing predicate.
_BASE_CONNECTOR_VIEW_SQL = """
    SELECT
        connector_type,
        endpoint_identity,
        state,
        error_message,
        last_heartbeat_at,
        first_seen_at
    FROM connector_registry
    WHERE deleted_at IS NULL
      AND archived_at IS NULL
"""

#: sw_028's inferred predicate — restored on downgrade only.
_INFERRED_CHECKPOINT_PREDICATE = """
      AND NOT (
          instance_id IS NULL
          AND last_heartbeat_at IS NULL
          AND checkpoint_cursor IS NOT NULL
      )
"""

#: The persisted replacement: storage rows are excluded because they SAY they
#: are storage rows, not because their columns happen to look that way.
_PERSISTED_CHECKPOINT_PREDICATE = """
      AND operational_role <> 'checkpoint'
"""

#: Idempotent backfill. Each statement narrows on ``operational_role`` state
#: that only an earlier statement in this list can have produced, so re-running
#: the migration re-derives the same assignment and changes nothing else.
_BACKFILL_SQL = [
    # 1. Process identity or a recorded heartbeat — only the heartbeat producer
    #    writes either, so both are positive evidence of a runtime instance.
    """
    UPDATE connector_registry
       SET operational_role = 'runtime_instance'
     WHERE operational_role = 'unknown'
       AND (instance_id IS NOT NULL OR last_heartbeat_at IS NOT NULL)
    """,
    # 2. No process, no heartbeat, but a persisted cursor: storage state.
    #    This is sw_028's predicate, stored once instead of inferred per query.
    """
    UPDATE connector_registry
       SET operational_role = 'checkpoint'
     WHERE operational_role = 'unknown'
       AND instance_id IS NULL
       AND last_heartbeat_at IS NULL
       AND checkpoint_cursor IS NOT NULL
    """,
    # 3. Attach each checkpoint to the longest runtime-instance identity of the
    #    same connector_type that it extends by a ':'-delimited suffix. Reads
    #    the registry's own runtime rows — no connector-specific pattern.
    """
    UPDATE connector_registry c
       SET parent_endpoint_identity = (
            SELECT p.endpoint_identity
              FROM connector_registry p
             WHERE p.connector_type = c.connector_type
               AND p.operational_role = 'runtime_instance'
               AND p.endpoint_identity <> c.endpoint_identity
               AND left(c.endpoint_identity, length(p.endpoint_identity) + 1)
                   = p.endpoint_identity || ':'
             ORDER BY length(p.endpoint_identity) DESC, p.endpoint_identity
             LIMIT 1
       )
     WHERE c.operational_role = 'checkpoint'
    """,
]


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE connector_registry
            ADD COLUMN IF NOT EXISTS operational_role TEXT NOT NULL DEFAULT 'unknown'
        """
    )
    op.execute(
        """
        ALTER TABLE connector_registry
            ADD COLUMN IF NOT EXISTS parent_endpoint_identity TEXT NULL
        """
    )

    # Backfill BEFORE the CHECK constraint so the constraint validates the
    # already-classified rows rather than racing them.
    for stmt in _BACKFILL_SQL:
        op.execute(stmt)

    op.execute(
        """
        ALTER TABLE connector_registry
            DROP CONSTRAINT IF EXISTS valid_operational_role
        """
    )
    op.execute(
        """
        ALTER TABLE connector_registry
            ADD CONSTRAINT valid_operational_role CHECK (
                operational_role IN ('runtime_instance', 'checkpoint', 'unknown')
            )
        """
    )

    op.execute(
        f"CREATE OR REPLACE VIEW {_CONNECTOR_VIEW_FQN} AS "
        f"{_BASE_CONNECTOR_VIEW_SQL}{_PERSISTED_CHECKPOINT_PREDICATE}"
    )


def downgrade() -> None:
    # Restore sw_028's inferred predicate first — the view references
    # operational_role, so it must stop doing so before the column is dropped.
    op.execute(
        f"CREATE OR REPLACE VIEW {_CONNECTOR_VIEW_FQN} AS "
        f"{_BASE_CONNECTOR_VIEW_SQL}{_INFERRED_CHECKPOINT_PREDICATE}"
    )
    op.execute("ALTER TABLE connector_registry DROP CONSTRAINT IF EXISTS valid_operational_role")
    op.execute("ALTER TABLE connector_registry DROP COLUMN IF EXISTS parent_endpoint_identity")
    op.execute("ALTER TABLE connector_registry DROP COLUMN IF EXISTS operational_role")

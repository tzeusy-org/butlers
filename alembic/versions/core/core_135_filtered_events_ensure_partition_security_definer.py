"""core_135 — SECURITY DEFINER for connectors_filtered_events_ensure_partition.

Revision ID: core_135
Revises: core_134
Create Date: 2026-06-21 00:00:00.000000

Root cause (bu-0qmj7, confirmed 2026-06-20)
-------------------------------------------
``connectors.connectors_filtered_events_ensure_partition()`` (and its unqualified
public-schema wrapper) were created as ``SECURITY INVOKER`` (the Postgres default).
The connector runtime role (``connector_writer``) is not the owner of
``connectors.filtered_events``, so calling the function as ``connector_writer``
raised::

    InsufficientPrivilegeError: must be owner of table filtered_events

The June 2026 partition was never created, causing every
``FilteredEventBuffer.flush()`` call to fail and drop all buffered events.

Fix
---
1. Recreate ``connectors.connectors_filtered_events_ensure_partition`` and its
   unqualified public-schema wrapper as ``SECURITY DEFINER``, with a pinned
   ``search_path`` to prevent search-path-injection attacks (the idiomatic
   Postgres pattern; see ``switchboard_message_inbox_ensure_partition`` for
   precedent in this codebase).

2. Pre-create the missing 2026-06 and 2026-07 partitions immediately so that
   buffered events can flush without waiting for the next connector poll.

Downgrade restores ``SECURITY INVOKER`` (the broken state); partitions created
during upgrade are intentionally preserved.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_135"
down_revision = "core_134"
branch_labels = None
depends_on = None

# Function body shared by both overloads.  Internal DDL is fully schema-qualified
# (connectors.%I) so neither overload depends on the runtime search_path for
# correctness; SET search_path is only a SECURITY DEFINER safety guard.
_FUNCTION_BODY = """
DECLARE
    month_start    TIMESTAMPTZ;
    month_end      TIMESTAMPTZ;
    partition_name TEXT;
    next_start     TIMESTAMPTZ;
    next_end       TIMESTAMPTZ;
    next_name      TEXT;
BEGIN
    -- Current month partition
    month_start    := date_trunc('month', reference_ts);
    month_end      := month_start + INTERVAL '1 month';
    partition_name := format('filtered_events_%s', to_char(month_start, 'YYYYMM'));

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS connectors.%I '
        'PARTITION OF connectors.filtered_events '
        'FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        month_start,
        month_end
    );

    -- Next month partition (proactive)
    next_start := month_end;
    next_end   := next_start + INTERVAL '1 month';
    next_name  := format('filtered_events_%s', to_char(next_start, 'YYYYMM'));

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS connectors.%I '
        'PARTITION OF connectors.filtered_events '
        'FOR VALUES FROM (%L) TO (%L)',
        next_name,
        next_start,
        next_end
    );

    RETURN partition_name;
END;
"""


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Schema-qualified function (connectors schema) — SECURITY DEFINER
    # ------------------------------------------------------------------
    op.execute(f"""
        CREATE OR REPLACE FUNCTION connectors.connectors_filtered_events_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = connectors, pg_temp
        AS $${_FUNCTION_BODY}$$
    """)

    # ------------------------------------------------------------------
    # 2. Unqualified backward-compat wrapper (public schema) — SECURITY DEFINER
    # ------------------------------------------------------------------
    op.execute(f"""
        CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = connectors, public, pg_temp
        AS $${_FUNCTION_BODY}$$
    """)

    # ------------------------------------------------------------------
    # 3. Pre-create missing partitions (2026-06 and 2026-07)
    #    Idempotent: CREATE TABLE IF NOT EXISTS inside the function means
    #    this is safe even if the partitions already exist.
    # ------------------------------------------------------------------
    op.execute(
        "SELECT connectors.connectors_filtered_events_ensure_partition"
        "('2026-06-01 00:00:00+00'::TIMESTAMPTZ)"
    )
    op.execute(
        "SELECT connectors.connectors_filtered_events_ensure_partition"
        "('2026-07-01 00:00:00+00'::TIMESTAMPTZ)"
    )


def downgrade() -> None:
    # Revert both functions to SECURITY INVOKER (the original broken state).
    # Partitions are intentionally preserved — dropping them would lose data.
    op.execute(f"""
        CREATE OR REPLACE FUNCTION connectors.connectors_filtered_events_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $${_FUNCTION_BODY}$$
    """)

    op.execute(f"""
        CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $${_FUNCTION_BODY}$$
    """)

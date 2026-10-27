"""Effective-time expand stage for relationship.entity_facts.

Revision ID: rel_035
Revises: rel_034
Create Date: 2026-09-29 00:00:00.000000

Phase: relationship-fact-effective-time, expand stage (bu-h3b7t.1).

``relationship.entity_facts`` records when an assertion was created
(``created_at``), when its source observed it (``observed_at``/``last_seen``),
how certain it is (``conf``) and whether this assertion version is current
(``validity``). None of those says when the asserted relationship was *true*.
This migration adds that fifth, independent axis without deriving it from any
of the other four.

New ``relationship.entity_facts`` columns (all nullable, no backfill)
--------------------------------------------------------------------
effective_period_id      UUID  stable occurrence id; NULL = the default occurrence
effective_from           TIMESTAMPTZ  inclusive lower bound (UTC)
effective_from_precision TEXT  instant | day | month | year | unbounded
effective_to             TIMESTAMPTZ  exclusive upper bound (UTC)
effective_to_precision   TEXT  instant | day | month | year | unbounded

The interval is half-open ``[effective_from, effective_to)``. For each bound a
NULL timestamp with NULL precision means *unknown*; a NULL timestamp with
``unbounded`` precision means the caller explicitly asserted *no* boundary in
that direction. Unknown is epistemic, never "for all time". Existing rows keep
all five columns NULL: the default occurrence with unspecified effective time.
Nothing is synthesized from ``created_at``, ``observed_at`` or ``last_seen``.

Named checks reject every second encoding of one bound state:
``ck_ef_effective_{from,to}_shape`` (timestamp/precision pairing and the
vocabulary), ``ck_ef_effective_{from,to}_canonical`` (UTC unit-start alignment of
coarse bounds), ``ck_ef_effective_range`` (strict ``from < to``) and
``ck_ef_effective_period_nonzero`` (the all-zero UUID is the reserved
default-occurrence sentinel of the occurrence index).

Two unique indexes during the transition
----------------------------------------
``uq_ef_spo_occurrence_active`` scopes active uniqueness to the occurrence. The
legacy ``uq_ef_spo_active`` is deliberately RETAINED unchanged: the deployed
writer names it as its ``ON CONFLICT`` inference target, and dropping it while
that writer is live would make every old-writer insert fail at plan time. While
both exist, the legacy index still forbids a second active occurrence, and the
transition writer treats its presence as the ``temporal_cutover_pending``
capability state -- it refuses every temporal assertion, correction and
explicit period id before approval parking or any write. Dropping the legacy
index is a separate, later cutover migration that is NOT part of this chain.

``relationship.fact_approval_context`` columns
----------------------------------------------
temporal_request_mode  TEXT NULL  ordinary | explicit | correction
temporal_base_fact_id  UUID NULL  the exact active version the parked request saw

Approved replay verifies this frozen mode/base so an ordinary reassertion that
preserved a known packet can never replay as an unknown create or a correction.
Rows written before this migration keep both NULL: pre-temporal ordinary
semantics.

Rollback
--------
Before cutover, rollback is: drain/disable the transition writer, restore the
old writer, then optionally downgrade this revision. Downgrade removes only the
objects added here and keeps every fact, rel_034 evidence, coverage receipt and
approval-context row. The temporal column values are necessarily lost, which is
why downgrade refuses (fails closed) when the legacy index is gone or any row
carries a temporal value: once temporal writes were admitted, recovery must roll
forward or follow a separately reviewed data-preserving plan.
"""

from __future__ import annotations

from alembic import op

revision = "rel_035"
down_revision = "rel_034"
branch_labels = None
depends_on = None

#: Stored precision tokens. SQL NULL is the only encoding of an unknown bound.
CONCRETE_PRECISIONS = ("instant", "day", "month", "year")
UNBOUNDED_PRECISION = "unbounded"
COARSE_PRECISIONS = ("day", "month", "year")

#: Reserved sentinel mapping the NULL default occurrence into the unique index.
DEFAULT_OCCURRENCE_SENTINEL = "00000000-0000-0000-0000-000000000000"

LEGACY_SPO_INDEX = "uq_ef_spo_active"
OCCURRENCE_INDEX = "uq_ef_spo_occurrence_active"

TEMPORAL_REQUEST_MODES = ("ordinary", "explicit", "correction")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _bound_checks(bound: str) -> dict[str, str]:
    column = f"effective_{bound}"
    precision = f"{column}_precision"
    return {
        f"ck_ef_{column}_shape": f"""
            CASE
                WHEN {column} IS NULL
                    THEN {precision} IS NULL OR {precision} = '{UNBOUNDED_PRECISION}'
                ELSE COALESCE({precision} IN ({_sql_list(CONCRETE_PRECISIONS)}), false)
            END
        """,
        # ``AT TIME ZONE 'UTC'`` makes the alignment independent of the session
        # TimeZone; a coarse bound must sit exactly at 00:00:00 UTC on the first
        # day of its unit (an upper bound is the first instant AFTER its unit).
        f"ck_ef_{column}_canonical": f"""
            {precision} IS NULL
            OR {precision} NOT IN ({_sql_list(COARSE_PRECISIONS)})
            OR date_trunc({precision}, {column} AT TIME ZONE 'UTC')
               = {column} AT TIME ZONE 'UTC'
        """,
    }


#: Every named CHECK this revision owns, in creation order.
CHECKS: dict[str, str] = {
    **_bound_checks("from"),
    **_bound_checks("to"),
    "ck_ef_effective_range": (
        "effective_from IS NULL OR effective_to IS NULL OR effective_from < effective_to"
    ),
    "ck_ef_effective_period_nonzero": (
        f"effective_period_id IS NULL OR effective_period_id <> '{DEFAULT_OCCURRENCE_SENTINEL}'::uuid"
    ),
}

ENTITY_FACTS_COLUMNS = """
    ALTER TABLE relationship.entity_facts
        ADD COLUMN IF NOT EXISTS effective_period_id      UUID,
        ADD COLUMN IF NOT EXISTS effective_from           TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS effective_from_precision TEXT,
        ADD COLUMN IF NOT EXISTS effective_to             TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS effective_to_precision   TEXT
"""

OCCURRENCE_INDEX_DDL = f"""
    CREATE UNIQUE INDEX IF NOT EXISTS {OCCURRENCE_INDEX}
        ON relationship.entity_facts (
            subject,
            predicate,
            object,
            COALESCE(effective_period_id, '{DEFAULT_OCCURRENCE_SENTINEL}'::uuid)
        )
        WHERE validity = 'active'
"""

APPROVAL_CONTEXT_COLUMNS = f"""
    ALTER TABLE relationship.fact_approval_context
        ADD COLUMN IF NOT EXISTS temporal_request_mode TEXT
            CONSTRAINT ck_fact_approval_context_temporal_mode
            CHECK (temporal_request_mode IN ({_sql_list(TEMPORAL_REQUEST_MODES)})),
        ADD COLUMN IF NOT EXISTS temporal_base_fact_id UUID
"""


def _add_check(name: str, expression: str) -> str:
    return f"""
        DO $$
        BEGIN
            ALTER TABLE relationship.entity_facts
                ADD CONSTRAINT {name} CHECK ({expression});
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
    """


def upgrade_statements() -> list[str]:
    """The upgrade DDL, shared with the hand-rolled test schema helper."""
    return [
        ENTITY_FACTS_COLUMNS,
        *(_add_check(name, expression) for name, expression in CHECKS.items()),
        OCCURRENCE_INDEX_DDL,
        APPROVAL_CONTEXT_COLUMNS,
    ]


def upgrade() -> None:
    for statement in upgrade_statements():
        op.execute(statement)


def downgrade() -> None:
    # Fail closed once the transition is no longer reversible: after cutover the
    # legacy index is gone, and a temporal value in any row would be silently
    # destroyed by dropping its column.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('relationship.{LEGACY_SPO_INDEX}') IS NULL THEN
                RAISE EXCEPTION
                    'rel_035 downgrade refused: {LEGACY_SPO_INDEX} is absent (post-cutover)';
            END IF;
            IF EXISTS (
                SELECT 1 FROM relationship.entity_facts
                WHERE effective_period_id IS NOT NULL
                   OR effective_from IS NOT NULL
                   OR effective_from_precision IS NOT NULL
                   OR effective_to IS NOT NULL
                   OR effective_to_precision IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'rel_035 downgrade refused: temporal values exist and would be lost';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE relationship.fact_approval_context
            DROP CONSTRAINT IF EXISTS ck_fact_approval_context_temporal_mode,
            DROP COLUMN IF EXISTS temporal_request_mode,
            DROP COLUMN IF EXISTS temporal_base_fact_id
        """
    )
    op.execute(f"DROP INDEX IF EXISTS relationship.{OCCURRENCE_INDEX}")
    drops = ",\n".join(f"DROP CONSTRAINT IF EXISTS {name}" for name in CHECKS)
    op.execute(
        f"""
        ALTER TABLE relationship.entity_facts
            {drops},
            DROP COLUMN IF EXISTS effective_period_id,
            DROP COLUMN IF EXISTS effective_from,
            DROP COLUMN IF EXISTS effective_from_precision,
            DROP COLUMN IF EXISTS effective_to,
            DROP COLUMN IF EXISTS effective_to_precision
        """
    )

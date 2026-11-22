"""Owner-delivery subjects carry a derived urgency class and its cause (bu-dhloi1.22).

Revision ID: core_357
Revises: core_356
Create Date: 2026-11-23 00:00:00.000000

core_317 left ``urgency_class`` as a typed slot that admitted no value. This gives it the closed
vocabulary and a content-free cause, both derived by the daemon at the moment the subject is
opened, never chosen by a model:

* ``urgency_class``: ``routine`` or ``interrupt``. NULL (a row from before this revision) reads
  as routine.
* ``urgency_reason``: required for ``interrupt`` and forbidden for ``routine``. It is one of the
  verified causes the pre-switch interruption authority already records: ``owner_creation`` (an
  obligation the owner flagged), ``owner_clause`` (an owner-authored break-through rule) and
  ``home_safety`` (an authenticated deterministic safety producer).

The pair is written once, with the row. The subject guard now refuses any UPDATE that changes
either column, so a retry, a new generation, a callback or a replay can neither raise nor lower
the urgency a subject opened with. The guard function is the core_317 body plus that rule.

The core chain runs once per butler schema over these shared objects, so every statement is
idempotent (``IF NOT EXISTS`` and DROP-then-ADD). The downgrade is cumulative on purpose, as in
core_355: another schema's chain may still hold rows that use the vocabulary.
"""

from __future__ import annotations

from alembic import op

revision = "core_357"
down_revision = "core_356"
branch_labels = None
depends_on = None

_CLASSES = ("routine", "interrupt")
_REASONS = ("owner_creation", "owner_clause", "home_safety")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


# The key core_317 takes for the same ledger objects: every per-schema chain replays this
# revision, and the guard is one shared pg_proc row.
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_317:owner-delivery', 0))"

_GUARD = r"""
CREATE OR REPLACE FUNCTION public.owner_delivery_subject_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'owner-delivery subjects are durable' USING ERRCODE = '42501';
    END IF;
    IF ROW(NEW.id, NEW.kind, NEW.owning_schema, NEW.source_ref, NEW.goal_effect_id,
           NEW.owner_entity, NEW.content_digest, NEW.action_digest, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.kind, OLD.owning_schema, OLD.source_ref, OLD.goal_effect_id,
           OLD.owner_entity, OLD.content_digest, OLD.action_digest, OLD.created_at) THEN
        RAISE EXCEPTION 'owner-delivery subject identity is immutable' USING ERRCODE = '42501';
    END IF;
    IF OLD.state IN ('decided', 'cancelled', 'expired') AND
       ROW(NEW.state, NEW.generation, NEW.decision, NEW.decided_surface, NEW.resolved_at,
           NEW.urgency_class, NEW.urgency_reason, NEW.holding_reason)
       IS DISTINCT FROM
       ROW(OLD.state, OLD.generation, OLD.decision, OLD.decided_surface, OLD.resolved_at,
           OLD.urgency_class, OLD.urgency_reason, OLD.holding_reason) THEN
        RAISE EXCEPTION 'a resolved owner-delivery subject is frozen' USING ERRCODE = '42501';
    END IF;
    IF ROW(NEW.urgency_class, NEW.urgency_reason)
       IS DISTINCT FROM ROW(OLD.urgency_class, OLD.urgency_reason) THEN
        RAISE EXCEPTION 'owner-delivery urgency is set when the subject opens and never changes'
            USING ERRCODE = '42501';
    END IF;
    IF NEW.generation < OLD.generation THEN
        RAISE EXCEPTION 'owner-delivery generation never decreases' USING ERRCODE = '42501';
    END IF;
    IF NEW.state <> OLD.state AND NOT (
        (OLD.state = 'pending' AND NEW.state IN
            ('delivered', 'acknowledged', 'decided', 'cancelled', 'expired', 'unknown'))
        OR (OLD.state IN ('delivered', 'unknown') AND NEW.state IN
            ('delivered', 'acknowledged', 'decided', 'cancelled', 'expired'))
        OR (OLD.state = 'acknowledged' AND NEW.state IN ('decided', 'cancelled', 'expired'))
    ) THEN
        RAISE EXCEPTION 'owner-delivery subject cannot move from % to %', OLD.state, NEW.state
            USING ERRCODE = '42501';
    END IF;
    IF OLD.decided_surface IS NOT NULL AND NEW.decided_surface IS DISTINCT FROM OLD.decided_surface
       AND OLD.state = NEW.state THEN
        RAISE EXCEPTION 'owner-delivery winning surface is set once' USING ERRCODE = '42501';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;
"""


def _apply() -> None:
    op.execute(_LOCK)
    op.execute(
        "ALTER TABLE public.owner_delivery_subjects ADD COLUMN IF NOT EXISTS urgency_reason TEXT"
    )
    op.execute(
        "ALTER TABLE public.owner_delivery_subjects "
        "DROP CONSTRAINT IF EXISTS owner_delivery_subjects_urgency_class_check"
    )
    op.execute(
        "ALTER TABLE public.owner_delivery_subjects "
        "DROP CONSTRAINT IF EXISTS ck_owner_delivery_urgency"
    )
    op.execute(
        "ALTER TABLE public.owner_delivery_subjects "
        "ADD CONSTRAINT ck_owner_delivery_urgency CHECK ("
        "(urgency_class IS NULL AND urgency_reason IS NULL) "
        "OR (urgency_class = 'routine' AND urgency_reason IS NULL) "
        f"OR (urgency_class = 'interrupt' AND urgency_reason IN ({_quoted(_REASONS)})))"
    )
    op.execute(_GUARD)


def upgrade() -> None:
    _apply()


def downgrade() -> None:
    # Cumulative on purpose: see the module docstring.
    _apply()

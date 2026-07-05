"""Rule promotion suggestion schema: switchboard.rule_promotion_suggestions.

Revision ID: sw_020
Revises: sw_019
Create Date: 2026-07-06 00:00:00.000000

bu-h26o9 (rule-promotion bead 2 of 7). Per
``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 2 and
the merged openspec change ``switchboard-rule-promotion``
(``openspec/changes/switchboard-rule-promotion/specs/switchboard-rule-promotion/spec.md``,
"Requirement: Rule Promotion Suggestion Data Model" / "Requirement: Rule
Provenance"). Bead 1 (sw_019, merged in #2983) built the
``routing_verdict_log`` mining substrate; this bead is schema-only — no
promotion-trigger job (bead 3), no approvals API (bead 4), no spot-check
demotion logic (bead 5).

``suggestion_kind`` decision (bu-l6vbd, flagged in design.md section 4 as
"status reused, or a suggestion_kind discriminator — see spec delta"):
this migration adds an explicit ``suggestion_kind`` column
(``'promotion' | 'demotion'``) rather than overloading ``status`` with a
``'demoted'`` value. Rationale:

  - ``status`` tracks the suggestion's own lifecycle
    (``pending_review``/``confirmed``/``dismissed``/``superseded``) and that
    lifecycle is identical in shape for both promotion and demotion
    suggestions (propose -> owner confirms or dismisses). Folding a
    rule-outcome value like ``'demoted'`` into that same enum conflates "what
    happened to the suggestion" with "what kind of suggestion this is" —
    a promotion suggestion is never itself "demoted"; a *rule* gets demoted
    as the side effect of confirming a demotion-kind suggestion.
  - Beads 4/5 need to render and route these differently (promotion cards
    show a *new* rule to create; demotion cards show an *existing* rule to
    revoke) — a discriminator column makes that split a plain ``WHERE
    suggestion_kind = ...`` filter instead of inferring kind from which
    nullable columns happen to be populated.
  - A CHECK constraint (``chk_rule_promotion_suggestions_kind_shape``, below)
    ties the discriminator to column population directly at the DB layer:
    promotion rows must carry the sender/condition/action triple and no
    ``target_rule_id``; demotion rows must carry ``target_rule_id`` and none
    of the proposed-rule columns. This makes an inconsistent row a constraint
    violation, not a silent latent bug for bead 4/5 to discover later. The
    required promotion-branch text columns (``sender_key``, ``source_channel``,
    ``proposed_action``) also reject ``''`` alongside ``NULL`` — an empty
    string is NOT NULL but is just as vacuous as no value for a required
    identity/action field, and would otherwise silently satisfy the CHECK.

Column notes:
  - ``target_rule_id`` (new, not in the original design.md sketch): the
    existing ``ingestion_rules`` row a demotion suggestion proposes to
    revoke. ``created_rule_id`` (from the original sketch) remains
    promotion-only: the *new* rule minted when a promotion suggestion is
    confirmed. These are deliberately separate columns rather than one
    overloaded "the rule this suggestion is about" column, matching the
    same "column population mirrors kind" principle as the CHECK above.
  - No cross-chain FK caution applies here (contrast sw_019's
    ``ingestion_event_id -> public.ingestion_events``): every FK in this
    migration targets ``ingestion_rules``, which is intra-schema/same-chain
    and already exists as of sw_003 — no chain-ordering hazard.
  - FK delete behavior: no ``ON DELETE`` action is specified for
    ``target_rule_id``, ``created_rule_id``, or ``promoted_from_suggestion_id``
    (all default to ``NO ACTION``), mirroring sw_019's documented choice to
    keep history intact — a suggestion or rule cannot be silently orphaned by
    deleting the row on the other side of the link. Nothing in this bead (or
    the sequence so far) hard-deletes ``ingestion_rules``/
    ``rule_promotion_suggestions`` rows; both use soft-delete
    (``ingestion_rules.deleted_at``) or terminal ``status`` values instead.
  - ``ingestion_rules.promoted_from_suggestion_id`` is additive and nullable
    — no backfill, no breaking change for existing rows. ``created_by``
    already an unconstrained TEXT column (see sw_003) gains a conventional
    ``'promotion'`` value; no CHECK constraint change needed there.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_020"
down_revision = "sw_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rule_promotion_suggestions (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suggestion_kind      TEXT NOT NULL DEFAULT 'promotion',
            sender_key           TEXT,
            source_channel       TEXT,
            proposed_rule_type   TEXT,
            proposed_condition   JSONB,
            proposed_action      TEXT,
            evidence_count       INTEGER NOT NULL DEFAULT 0,
            first_evidence_at    TIMESTAMPTZ,
            last_evidence_at     TIMESTAMPTZ,
            is_clearly_automated BOOLEAN NOT NULL DEFAULT FALSE,
            status               TEXT NOT NULL DEFAULT 'pending_review',
            target_rule_id       UUID REFERENCES ingestion_rules(id),
            created_rule_id      UUID REFERENCES ingestion_rules(id),
            dismissal_reason     TEXT,
            cooldown_until       TIMESTAMPTZ,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_at           TIMESTAMPTZ,
            decided_by           TEXT,

            CONSTRAINT chk_rule_promotion_suggestions_kind
                CHECK (suggestion_kind IN ('promotion', 'demotion')),

            CONSTRAINT chk_rule_promotion_suggestions_status
                CHECK (status IN
                    ('pending_review', 'confirmed', 'dismissed', 'superseded')),

            CONSTRAINT chk_rule_promotion_suggestions_proposed_rule_type
                CHECK (proposed_rule_type IS NULL
                    OR proposed_rule_type IN ('sender_address', 'sender_domain')),

            -- Ties the suggestion_kind discriminator to column population:
            -- a promotion suggestion carries the sender/condition/action
            -- triple and no target_rule_id; a demotion suggestion carries
            -- target_rule_id and none of the proposed-rule columns. The
            -- text columns also reject '' (empty string is NOT NULL but is
            -- just as semantically absent as NULL for a required identity/
            -- action field) so the constraint cannot be satisfied by a
            -- vacuous placeholder value.
            CONSTRAINT chk_rule_promotion_suggestions_kind_shape
                CHECK (
                    (
                        suggestion_kind = 'promotion'
                        AND sender_key IS NOT NULL AND sender_key <> ''
                        AND source_channel IS NOT NULL AND source_channel <> ''
                        AND proposed_rule_type IS NOT NULL
                        AND proposed_condition IS NOT NULL
                        AND proposed_action IS NOT NULL AND proposed_action <> ''
                        AND target_rule_id IS NULL
                    )
                    OR
                    (
                        suggestion_kind = 'demotion'
                        AND target_rule_id IS NOT NULL
                        AND proposed_rule_type IS NULL
                        AND proposed_condition IS NULL
                        AND proposed_action IS NULL
                    )
                )
        )
        """
    )

    # Approvals-surface listing (bead 4): "give me suggestions in this
    # status, oldest/newest first".
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rule_promotion_suggestions_status_created
        ON rule_promotion_suggestions (status, created_at)
        """
    )

    # Promotion trigger (bead 3): at most one pending promotion suggestion
    # per sender/channel — repeated evidence bumps the existing row instead
    # of inserting a duplicate.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_rule_promotion_suggestions_pending_promotion
        ON rule_promotion_suggestions (sender_key, source_channel)
        WHERE status = 'pending_review' AND suggestion_kind = 'promotion'
        """
    )

    # Demotion spot-check (bead 5): at most one pending demotion suggestion
    # per rule under scrutiny.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_rule_promotion_suggestions_pending_demotion
        ON rule_promotion_suggestions (target_rule_id)
        WHERE status = 'pending_review' AND suggestion_kind = 'demotion'
        """
    )

    # Demotion spot-check (bead 5): "is there already a suggestion tracking
    # this rule" lookup.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rule_promotion_suggestions_target_rule
        ON rule_promotion_suggestions (target_rule_id)
        WHERE target_rule_id IS NOT NULL
        """
    )

    # Rule provenance (design.md section 2 / spec "Requirement: Rule
    # Provenance"): additive, nullable — no backfill needed for existing rows.
    op.execute(
        """
        ALTER TABLE ingestion_rules
        ADD COLUMN IF NOT EXISTS promoted_from_suggestion_id
            UUID REFERENCES rule_promotion_suggestions(id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingestion_rules_promoted_from_suggestion
        ON ingestion_rules (promoted_from_suggestion_id)
        WHERE promoted_from_suggestion_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ingestion_rules_promoted_from_suggestion")
    op.execute("ALTER TABLE ingestion_rules DROP COLUMN IF EXISTS promoted_from_suggestion_id")
    op.execute("DROP INDEX IF EXISTS ix_rule_promotion_suggestions_target_rule")
    op.execute("DROP INDEX IF EXISTS ux_rule_promotion_suggestions_pending_demotion")
    op.execute("DROP INDEX IF EXISTS ux_rule_promotion_suggestions_pending_promotion")
    op.execute("DROP INDEX IF EXISTS ix_rule_promotion_suggestions_status_created")
    op.execute("DROP TABLE IF EXISTS rule_promotion_suggestions")

"""Goal runner accounting on the sw_047 ledger (RFC 0038 D9, D10, D11, D14, D16).

Revision ID: sw_062
Revises: sw_061

Dark like the rest of the ledger: nothing here is wired to ingress.

* ``invocations`` gains the runner's per-invocation facts:

  - ``used_calls``: every broker call made with the invocation's token, charged
    in the D7 transaction. D14 allows a runtime-failure relaunch only when this
    and the effect and refusal records show no capability call.
  - ``max_calls`` and ``deadline``: a child's D9 budget share (calls and wall
    time). NULL for a lead, which draws on the goal budget alone.
  - ``model_role``, ``model_tier``, ``model_id``: the D16 resolution the
    invocation launched on. Escalation climbs from ``model_tier``.
  - ``relaunch_of``: the invocation a D14 runtime-failure relaunch replaced.
  - ``end_reason``: a closed, content-blind reason for the invocation's end.

  The launch facts are fixed at insert (``model_id`` is bound once, when the
  launch resolves its model); ``used_calls`` only grows and ``end_reason`` is
  set once.

* ``goal_outcomes`` records ``goal.complete`` (D11, D12): the lead's outcome,
  the effects it relies on and the owner reply effect, fixed when the lead
  completes; and, written once when the goal leaves ``settling``, its final
  status and, when an owner goal's reply did not reach the owner, the recorded
  delivery failure. Identifiers only (D17). Never deleted.
"""

from alembic import op

revision = "sw_062"
down_revision = "sw_061"
branch_labels = None
depends_on = None

_END_REASONS = (
    "returned",
    "completed",
    "returned_without_outcome",
    "runtime_failure",
    "launch_failed",
    "lease_lapsed",
    "stopped",
    "child_stopped",
    "escalated",
    "goal_ended",
    "budget_exhausted",
    "turns_exhausted",
    "tokens_exhausted",
    "deadline",
)


def upgrade() -> None:
    reasons = ",".join(f"'{reason}'" for reason in _END_REASONS)
    op.execute(f"""
        ALTER TABLE switchboard.invocations
            ADD COLUMN IF NOT EXISTS used_calls BIGINT NOT NULL DEFAULT 0
                CHECK (used_calls >= 0),
            ADD COLUMN IF NOT EXISTS max_calls BIGINT CHECK (max_calls IS NULL OR max_calls >= 0),
            ADD COLUMN IF NOT EXISTS deadline TIMESTAMPTZ
                CHECK (deadline IS NULL OR isfinite(deadline)),
            ADD COLUMN IF NOT EXISTS model_role TEXT
                CHECK (model_role IS NULL OR model_role IN ('lead','child','monitor')),
            ADD COLUMN IF NOT EXISTS model_tier TEXT
                CHECK (model_tier IS NULL OR model_tier ~ '^[a-z][a-z_]{{0,31}}$'),
            ADD COLUMN IF NOT EXISTS model_id TEXT
                CHECK (model_id IS NULL OR length(model_id) BETWEEN 1 AND 200),
            ADD COLUMN IF NOT EXISTS relaunch_of UUID,
            ADD COLUMN IF NOT EXISTS end_reason TEXT
                CHECK (end_reason IS NULL OR end_reason IN ({reasons}));
        ALTER TABLE switchboard.invocations DROP CONSTRAINT IF EXISTS ck_invocation_call_share;
        ALTER TABLE switchboard.invocations ADD CONSTRAINT ck_invocation_call_share
            CHECK (max_calls IS NULL OR used_calls <= max_calls);
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='switchboard.invocations'::regclass
                AND conname='fk_invocation_relaunch_of') THEN
                ALTER TABLE switchboard.invocations ADD CONSTRAINT fk_invocation_relaunch_of
                    FOREIGN KEY (goal_id,relaunch_of) REFERENCES switchboard.invocations(goal_id,id);
            END IF;
        END $$;
        CREATE INDEX IF NOT EXISTS ix_invocations_live_lease ON switchboard.invocations
            (lease_expires_at) WHERE status IN ('launching','running');

        CREATE OR REPLACE FUNCTION switchboard.goal_runner_invocation_immutable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            -- model_id is bound once, when the launch resolves the model.
            IF (NEW.max_calls,NEW.deadline,NEW.model_role,NEW.model_tier,NEW.relaunch_of)
               IS DISTINCT FROM
               (OLD.max_calls,OLD.deadline,OLD.model_role,OLD.model_tier,OLD.relaunch_of)
               OR (OLD.model_id IS NOT NULL AND NEW.model_id IS DISTINCT FROM OLD.model_id)
               OR NEW.used_calls < OLD.used_calls
               OR (OLD.end_reason IS NOT NULL AND NEW.end_reason IS DISTINCT FROM OLD.end_reason)
            THEN
                RAISE EXCEPTION 'Invocation launch facts are fixed and call counts only grow';
            END IF;
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS goal_runner_invocation_immutable ON switchboard.invocations;
        CREATE TRIGGER goal_runner_invocation_immutable BEFORE UPDATE ON switchboard.invocations
            FOR EACH ROW EXECUTE FUNCTION switchboard.goal_runner_invocation_immutable();

        CREATE TABLE IF NOT EXISTS switchboard.goal_outcomes (
            goal_id UUID PRIMARY KEY REFERENCES switchboard.goals(id),
            invocation_id UUID NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('completed','partial','failed')),
            relied_effects UUID[] NOT NULL DEFAULT '{{}}',
            reply_effect_id UUID,
            final_status TEXT CHECK (final_status IS NULL OR final_status IN (
                'completed','partial','failed')),
            delivery_failure TEXT CHECK (delivery_failure IS NULL OR delivery_failure IN (
                'reply_missing','reply_not_committed')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            resolved_at TIMESTAMPTZ,
            CONSTRAINT ck_outcome_resolution CHECK ((final_status IS NULL) = (resolved_at IS NULL)),
            CONSTRAINT ck_outcome_delivery CHECK (delivery_failure IS NULL OR final_status='partial'),
            CONSTRAINT fk_outcome_invocation FOREIGN KEY (goal_id,invocation_id)
                REFERENCES switchboard.invocations(goal_id,id),
            CONSTRAINT fk_outcome_reply FOREIGN KEY (goal_id,reply_effect_id)
                REFERENCES switchboard.effects(goal_id,id)
        );
        CREATE OR REPLACE FUNCTION switchboard.guard_goal_outcome()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'A goal outcome is durable'
                USING ERRCODE = 'insufficient_privilege';
        END $$;
        -- The lead's request is fixed; the resolution is written once, when the
        -- goal leaves settling (D11).
        CREATE OR REPLACE FUNCTION switchboard.goal_outcome_resolve_once()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF OLD.final_status IS NOT NULL
               OR (to_jsonb(NEW) - ARRAY['final_status','delivery_failure','resolved_at'])
                  IS DISTINCT FROM
                  (to_jsonb(OLD) - ARRAY['final_status','delivery_failure','resolved_at']) THEN
                RAISE EXCEPTION 'A goal outcome is durable'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END $$;
        -- Named like sw_057's lineage guard: statement level, so it also fires on
        -- TRUNCATE ... CASCADE from the goal ledger.
        DROP TRIGGER IF EXISTS durable_goal_lineage ON switchboard.goal_outcomes;
        CREATE TRIGGER durable_goal_lineage BEFORE DELETE OR TRUNCATE
            ON switchboard.goal_outcomes FOR EACH STATEMENT
            EXECUTE FUNCTION switchboard.guard_goal_outcome();
        DROP TRIGGER IF EXISTS goal_outcome_resolve_once ON switchboard.goal_outcomes;
        CREATE TRIGGER goal_outcome_resolve_once BEFORE UPDATE ON switchboard.goal_outcomes
            FOR EACH ROW EXECUTE FUNCTION switchboard.goal_outcome_resolve_once();
        ALTER TABLE switchboard.goal_outcomes ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON switchboard.goal_outcomes FROM PUBLIC, butler_switchboard_rw;
        GRANT SELECT, INSERT, UPDATE ON switchboard.goal_outcomes TO butler_switchboard_rw;
        DROP POLICY IF EXISTS goal_outcome_read ON switchboard.goal_outcomes;
        CREATE POLICY goal_outcome_read ON switchboard.goal_outcomes
            FOR SELECT TO butler_switchboard_rw USING (current_user='butler_switchboard_rw');
        DROP POLICY IF EXISTS goal_outcome_insert ON switchboard.goal_outcomes;
        CREATE POLICY goal_outcome_insert ON switchboard.goal_outcomes
            FOR INSERT TO butler_switchboard_rw WITH CHECK (current_user='butler_switchboard_rw');
        DROP POLICY IF EXISTS goal_outcome_update ON switchboard.goal_outcomes;
        CREATE POLICY goal_outcome_update ON switchboard.goal_outcomes
            FOR UPDATE TO butler_switchboard_rw USING (current_user='butler_switchboard_rw')
            WITH CHECK (current_user='butler_switchboard_rw');
    """)


def downgrade() -> None:
    op.execute("""
        LOCK TABLE switchboard.goal_outcomes IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.goal_outcomes) THEN
                RAISE EXCEPTION 'Cannot discard durable goal outcomes';
            END IF;
        END $$;
        DROP TABLE switchboard.goal_outcomes;
        DROP FUNCTION switchboard.guard_goal_outcome();
        DROP FUNCTION switchboard.goal_outcome_resolve_once();
        DROP TRIGGER IF EXISTS goal_runner_invocation_immutable ON switchboard.invocations;
        DROP FUNCTION IF EXISTS switchboard.goal_runner_invocation_immutable();
        DROP INDEX IF EXISTS switchboard.ix_invocations_live_lease;
        ALTER TABLE switchboard.invocations
            DROP CONSTRAINT IF EXISTS fk_invocation_relaunch_of,
            DROP CONSTRAINT IF EXISTS ck_invocation_call_share,
            DROP COLUMN IF EXISTS end_reason,
            DROP COLUMN IF EXISTS relaunch_of,
            DROP COLUMN IF EXISTS model_id,
            DROP COLUMN IF EXISTS model_tier,
            DROP COLUMN IF EXISTS model_role,
            DROP COLUMN IF EXISTS deadline,
            DROP COLUMN IF EXISTS max_calls,
            DROP COLUMN IF EXISTS used_calls;
    """)

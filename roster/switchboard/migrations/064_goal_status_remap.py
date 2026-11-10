"""Goal status cutover: settling, awaiting_approval and recovering become waiting and admitted.

Revision ID: sw_064
Revises: sw_063

RFC 0038 S2 disposition table (bu-dhloi1.7, PR B1; folds in the state half of bu-dhloi1.204).
sw_063 added ``waiting`` and kept the legacy states while the runner still wrote them; the runner
now writes only the target states, so this revision converts every legacy row and removes the
legacy states from ``goals_status_check``.

* ``settling`` becomes ``waiting`` on a ``settle`` envelope: the goal waits on the exact effects
  that can still change, and resolves deterministically (no LLM epoch) once they settle.
* ``awaiting_approval`` becomes ``waiting`` on an ``approval`` envelope bound to the exact parked
  effect. A goal with no parked effect to bind is held, never guessed or auto-approved: it
  waits on a ``conditions`` envelope whose predicate records ``evidence_unavailable`` (reason
  ``legacy_approval_unbound``) and which no condition is registered to fire. Only Stop or an
  explicit owner action ends it, and the upgrade never aborts for it.
* ``recovering`` becomes ``admitted``. The recovery intent is the ended lead with no successor
  (the runner's leaderless sweep already resumes it at the next epoch); no budget is reset and
  nothing is dispatched.

Every converted row keeps its original status in ``goals.legacy_status``. A legacy waiting goal
gets a v1 plan (no premises, evidence or steps), a generation-0 registration and an envelope
whose bounds are the goal's own: its label, its budget deadline and a single wake. The envelope's
grants are empty because nothing may be launched from it.

The downgrade restores the legacy states from ``legacy_status`` and refuses for any goal that
began waiting after this revision (no legacy status to return to).
"""

from alembic import op

revision = "sw_064"
down_revision = "sw_063"
branch_labels = None
depends_on = None

_EMPTY_PLAN_DIGEST = 'sha256(convert_to(\'{"evidence":[],"premise":[],"steps":[]}\', \'UTF8\'))'

_REMAP_SQL = f"""
DO $$
DECLARE
    g RECORD;
    epoch_n BIGINT;
    plan_n BIGINT;
    plan_dig TEXT;
    effect_id UUID;
    effect_args TEXT;
    envelope UUID;
    kind_n TEXT;
    pred JSONB;
    deadline_n TIMESTAMPTZ;
BEGIN
    FOR g IN SELECT * FROM switchboard.goals
             WHERE status IN ('settling','awaiting_approval') ORDER BY id FOR UPDATE LOOP
        SELECT max(epoch) INTO epoch_n FROM switchboard.goal_epochs WHERE goal_id = g.id;
        IF epoch_n IS NULL THEN
            RAISE EXCEPTION 'Goal % is % but has no lead epoch to wait from', g.id, g.status;
        END IF;
        effect_id := NULL;
        IF g.status = 'awaiting_approval' THEN
            SELECT id, args_digest INTO effect_id, effect_args FROM switchboard.effects
            WHERE goal_id = g.id AND status = 'parked' ORDER BY created_at, id LIMIT 1;
        END IF;
        plan_n := g.plan_version;
        IF plan_n IS NULL THEN
            plan_n := 1;
            INSERT INTO switchboard.goal_plans(goal_id, version, predecessor_version,
                    created_epoch, digest)
                VALUES (g.id, 1, NULL, epoch_n, encode({_EMPTY_PLAN_DIGEST}, 'hex'));
        END IF;
        SELECT digest INTO plan_dig FROM switchboard.goal_plans
            WHERE goal_id = g.id AND version = plan_n;
        kind_n := CASE WHEN g.status = 'settling' THEN 'settle'
                       WHEN effect_id IS NOT NULL THEN 'approval' ELSE 'conditions' END;
        pred := CASE WHEN g.status = 'awaiting_approval' AND effect_id IS NULL
                     THEN jsonb_build_object('kind', 'evidence_unavailable',
                                             'reason', 'legacy_approval_unbound')
                     ELSE jsonb_build_object('kind', kind_n) END;
        SELECT deadline INTO deadline_n FROM switchboard.budgets WHERE goal_id = g.id;
        envelope := gen_random_uuid();
        INSERT INTO switchboard.wait_envelopes(id, goal_id, kind, original_epoch, plan_version,
                plan_digest, predicate, predicate_digest, source_set, permitted_sources,
                grant_snapshot, grant_digest, label_version, label_instr, label_data,
                label_sens, deadline, max_wakes, approval_effect_id, approval_digest)
            VALUES (envelope, g.id, kind_n, epoch_n, plan_n, plan_dig, pred,
                encode(sha256(convert_to(pred::text, 'UTF8')), 'hex'),
                ARRAY['effects'], ARRAY['effects'], '[]'::jsonb,
                encode(sha256(convert_to('[]', 'UTF8')), 'hex'),
                g.label_version, g.label_instr, g.label_data, g.label_sens,
                COALESCE(deadline_n, clock_timestamp() + interval '1 hour'), 1, effect_id,
                CASE WHEN effect_id IS NULL THEN NULL
                     ELSE encode(sha256(convert_to(effect_args, 'UTF8')), 'hex') END);
        INSERT INTO switchboard.wait_generations(goal_id, envelope_id, generation,
                condition_instance_ids)
            VALUES (g.id, envelope, 0, ARRAY['legacy:' || g.status || ':' || g.id::text]);
        UPDATE switchboard.goals SET status = 'waiting', legacy_status = g.status,
            plan_version = plan_n, updated_at = clock_timestamp() WHERE id = g.id;
    END LOOP;
    UPDATE switchboard.goals SET status = 'admitted', legacy_status = 'recovering',
        updated_at = clock_timestamp() WHERE status = 'recovering';
END $$;
ALTER TABLE switchboard.goals DROP CONSTRAINT goals_status_check;
ALTER TABLE switchboard.goals ADD CONSTRAINT goals_status_check CHECK (status IN (
    'admitted','running','waiting','completed','partial','failed','stopped',
    'budget_exhausted'));
"""

_RESTORE_SQL = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM switchboard.goals
               WHERE status = 'waiting' AND legacy_status IS NULL) THEN
        RAISE EXCEPTION 'Cannot restore the legacy states: a goal began waiting after sw_064';
    END IF;
END $$;
ALTER TABLE switchboard.goals DROP CONSTRAINT goals_status_check;
ALTER TABLE switchboard.goals ADD CONSTRAINT goals_status_check CHECK (status IN (
    'admitted','running','settling','completed','partial','failed','stopped',
    'budget_exhausted','awaiting_approval','recovering','waiting'));
UPDATE switchboard.goals SET status = legacy_status, updated_at = clock_timestamp()
    WHERE legacy_status IN ('settling','awaiting_approval','recovering')
      AND status IN ('waiting','admitted');
UPDATE switchboard.wait_envelopes SET state = 'cancelled', updated_at = clock_timestamp()
    WHERE state IN ('open','fired')
      AND goal_id IN (SELECT id FROM switchboard.goals WHERE legacy_status IS NOT NULL);
"""


def upgrade() -> None:
    op.execute("LOCK TABLE switchboard.goals IN ACCESS EXCLUSIVE MODE")
    op.execute(_REMAP_SQL)


def downgrade() -> None:
    op.execute("LOCK TABLE switchboard.goals IN ACCESS EXCLUSIVE MODE")
    op.execute(_RESTORE_SQL)

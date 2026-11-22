"""The monthly spend ceiling carried into goal admission (RFC 0038 D10, bu-jv1moa.1).

Revision ID: sw_084
Revises: sw_083

Two additive tables and three admission refusal reasons.

``switchboard.budget_pools`` holds one row per ``(pool_key, month)``: ``fleet_month`` (every goal,
capped at the owner's ceiling) and ``background`` (non-owner goals, capped at the ceiling less the
owner reserve). ``reserved_usd`` is what live goals may still spend; ``spent_usd`` is what they
have. The cap follows ``public.spend_ceiling`` each time admission reserves.

``switchboard.pool_reservations`` is one row per goal (``goal_id`` is the key, so a duplicate
admission or a recovery epoch reuses it). It has no foreign key to ``goals``: admission reserves
before it creates the goal, in the same transaction, and a refusal leaves neither.

``charge_goal_reservation`` moves priced spend from reserved to spent in the caller's transaction,
never past the reservation. A trigger on ``goals`` settles the rest when a goal reaches a terminal
status: a goal that used tokens but recorded no priced spend keeps its full reservation charged
(unpriced usage is never counted as zero); any other goal releases its unspent remainder.

The downgrade refuses while any reservation exists, and the reason constraint then drops the new
reasons only if no decision uses them.
"""

from alembic import op

revision = "sw_084"
down_revision = "sw_083"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"
_TERMINAL = "('completed','partial','failed','stopped','budget_exhausted')"
_OLD_REASONS = (
    "source_evidence_unavailable",
    "limits_unavailable",
    "cause_invalid",
    "creator_unavailable",
    "causal_chain_limit",
    "reply_joined",
    "quota_exhausted",
    "new_goal",
    "recorded_without_goal",
    "input_joined",
    "condition_joined",
)
_NEW_REASONS = ("lane_exhausted", "ceiling_reached", "spend_state_unreadable")


_SECURE = """
    ALTER TABLE switchboard.__TABLE__ ENABLE ROW LEVEL SECURITY;
    REVOKE ALL ON switchboard.__TABLE__ FROM PUBLIC;
    GRANT SELECT, INSERT, UPDATE ON switchboard.__TABLE__ TO __ROLE__;
    CREATE POLICY __TABLE___select ON switchboard.__TABLE__ FOR SELECT TO __ROLE__
        USING (current_user = '__ROLE__');
    CREATE POLICY __TABLE___insert ON switchboard.__TABLE__ FOR INSERT TO __ROLE__
        WITH CHECK (current_user = '__ROLE__');
    CREATE POLICY __TABLE___update ON switchboard.__TABLE__ FOR UPDATE TO __ROLE__
        USING (current_user = '__ROLE__') WITH CHECK (current_user = '__ROLE__');
"""


def _quoted(values):
    return ",".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE switchboard.budget_pools (
            pool_key TEXT NOT NULL CHECK (pool_key IN ('fleet_month','background')),
            month DATE NOT NULL,
            cap_usd NUMERIC NOT NULL CHECK (cap_usd >= 0),
            reserved_usd NUMERIC NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
            spent_usd NUMERIC NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (pool_key, month)
        );
        CREATE TABLE switchboard.pool_reservations (
            goal_id UUID PRIMARY KEY,
            month DATE NOT NULL,
            origin TEXT NOT NULL CHECK (origin IN ('owner','obligation','third_party','staffer')),
            amount_usd NUMERIC NOT NULL CHECK (amount_usd >= 0),
            charged_usd NUMERIC NOT NULL DEFAULT 0
                CHECK (charged_usd >= 0 AND charged_usd <= amount_usd),
            lane BOOLEAN NOT NULL,
            settled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    for table in ("budget_pools", "pool_reservations"):
        op.execute(_SECURE.replace("__TABLE__", table).replace("__ROLE__", _RUNTIME))
    op.execute(f"""
        CREATE FUNCTION switchboard.charge_goal_reservation(p_goal UUID, p_spend NUMERIC)
        RETURNS NUMERIC LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
            r switchboard.pool_reservations;
            moved NUMERIC;
        BEGIN
            IF p_spend IS NULL OR p_spend <= 0 THEN
                RETURN 0;
            END IF;
            SELECT * INTO r FROM switchboard.pool_reservations
                WHERE goal_id = p_goal AND settled_at IS NULL FOR UPDATE;
            IF NOT FOUND THEN
                RETURN 0;
            END IF;
            moved := least(p_spend, r.amount_usd - r.charged_usd);
            IF moved <= 0 THEN
                RETURN 0;
            END IF;
            UPDATE switchboard.pool_reservations SET charged_usd = charged_usd + moved
                WHERE goal_id = p_goal;
            UPDATE switchboard.budget_pools
                SET reserved_usd = greatest(reserved_usd - moved, 0),
                    spent_usd = spent_usd + moved, updated_at = now()
                WHERE month = r.month
                  AND (pool_key = 'fleet_month' OR (pool_key = 'background' AND r.lane));
            RETURN moved;
        END $$;
        REVOKE ALL ON FUNCTION switchboard.charge_goal_reservation(UUID, NUMERIC) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION switchboard.charge_goal_reservation(UUID, NUMERIC)
            TO {_RUNTIME};

        CREATE FUNCTION switchboard.settle_goal_reservation()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
            r switchboard.pool_reservations;
            unpriced BOOLEAN;
            rest NUMERIC;
        BEGIN
            SELECT * INTO r FROM switchboard.pool_reservations
                WHERE goal_id = NEW.id AND settled_at IS NULL FOR UPDATE;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            SELECT b.used_spend = 0 AND b.used_tokens > 0 INTO unpriced
                FROM switchboard.budgets b WHERE b.goal_id = NEW.id;
            rest := r.amount_usd - r.charged_usd;
            UPDATE switchboard.budget_pools
                SET reserved_usd = greatest(reserved_usd - rest, 0),
                    spent_usd = spent_usd + CASE WHEN COALESCE(unpriced, false) THEN rest ELSE 0 END,
                    updated_at = now()
                WHERE month = r.month
                  AND (pool_key = 'fleet_month' OR (pool_key = 'background' AND r.lane));
            UPDATE switchboard.pool_reservations
                SET charged_usd = CASE WHEN COALESCE(unpriced, false)
                        THEN amount_usd ELSE charged_usd END,
                    settled_at = clock_timestamp()
                WHERE goal_id = NEW.id;
            RETURN NULL;
        END $$;
        REVOKE ALL ON FUNCTION switchboard.settle_goal_reservation() FROM PUBLIC;
        CREATE TRIGGER settle_goal_reservation AFTER UPDATE OF status ON switchboard.goals
            FOR EACH ROW
            WHEN (NEW.status IN {_TERMINAL} AND OLD.status NOT IN {_TERMINAL})
            EXECUTE FUNCTION switchboard.settle_goal_reservation();
    """)
    op.execute(f"""
        ALTER TABLE switchboard.admission_decisions
            DROP CONSTRAINT admission_decisions_reason_check;
        ALTER TABLE switchboard.admission_decisions
            ADD CONSTRAINT admission_decisions_reason_check
            CHECK (reason IN ({_quoted(_OLD_REASONS + _NEW_REASONS)}));
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.pool_reservations)
               OR EXISTS (SELECT 1 FROM switchboard.admission_decisions
                          WHERE reason IN ({_quoted(_NEW_REASONS)})) THEN
                RAISE EXCEPTION 'Cannot discard spend reservations or their refusals';
            END IF;
        END $$;
        DROP TRIGGER IF EXISTS settle_goal_reservation ON switchboard.goals;
        DROP FUNCTION IF EXISTS switchboard.settle_goal_reservation();
        DROP FUNCTION IF EXISTS switchboard.charge_goal_reservation(UUID, NUMERIC);
        DROP TABLE IF EXISTS switchboard.pool_reservations;
        DROP TABLE IF EXISTS switchboard.budget_pools;
        ALTER TABLE switchboard.admission_decisions
            DROP CONSTRAINT admission_decisions_reason_check;
        ALTER TABLE switchboard.admission_decisions
            ADD CONSTRAINT admission_decisions_reason_check
            CHECK (reason IN ({_quoted(_OLD_REASONS)}));
    """)

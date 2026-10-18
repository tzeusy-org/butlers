"""Add the unwired RFC0038 ledger under ordinary-owner, non-FORCE RLS.

Revision ID: sw_047
Revises: sw_046
"""

from alembic import op

revision = "sw_047"
down_revision = "sw_046"
branch_labels = None
depends_on = None

_TABLES = ("goals", "invocations", "effects", "budgets", "admission_decisions")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS switchboard.goals (
            id UUID PRIMARY KEY,
            source_key TEXT NOT NULL UNIQUE,
            source_kind TEXT NOT NULL CHECK (source_kind IN ('message','occurrence','feed','domain','staffer')),
            source_ref TEXT NOT NULL,
            occurrence_key TEXT,
            origin TEXT NOT NULL CHECK (origin IN ('owner','obligation','third_party','staffer')),
            admitted_exposed BOOLEAN NOT NULL,
            exposed BOOLEAN NOT NULL,
            ceiling JSONB NOT NULL CHECK (jsonb_typeof(ceiling) = 'array'),
            ceiling_digest TEXT NOT NULL,
            catalog_digest TEXT NOT NULL,
            contract_digest TEXT,
            config_digest TEXT NOT NULL,
            caused_by_goal UUID,
            caused_by_effect UUID,
            chain_depth INTEGER NOT NULL CHECK (chain_depth >= 0),
            status TEXT NOT NULL DEFAULT 'admitted' CHECK (status IN (
                'admitted','running','settling','completed','partial','failed','stopped',
                'budget_exhausted','awaiting_approval','recovering')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_goal_cause_pair CHECK (
                (caused_by_goal IS NULL) = (caused_by_effect IS NULL)),
            CONSTRAINT ck_goal_cause_depth CHECK (
                (caused_by_goal IS NULL AND chain_depth=0) OR (caused_by_goal IS NOT NULL AND chain_depth>0)),
            CONSTRAINT ck_goal_initial_exposure CHECK (NOT admitted_exposed OR exposed),
            CONSTRAINT ck_goal_occurrence CHECK (source_kind <> 'occurrence' OR occurrence_key IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS switchboard.invocations (
            id UUID PRIMARY KEY,
            goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
            parent_invocation_id UUID,
            depth SMALLINT NOT NULL CHECK (depth IN (0,1)),
            role TEXT NOT NULL CHECK (role IN ('lead','child')),
            parent_depth SMALLINT GENERATED ALWAYS AS
                (CASE WHEN parent_invocation_id IS NULL THEN NULL ELSE 0 END) STORED,
            lead_epoch BIGINT NOT NULL CHECK (lead_epoch >= 0),
            status TEXT NOT NULL CHECK (status IN (
                'launching','running','returned','failed','timed_out','stopped','escalated')),
            grant_snapshot JSONB NOT NULL CHECK (jsonb_typeof(grant_snapshot) = 'array'),
            grant_digest TEXT NOT NULL,
            max_turns BIGINT NOT NULL CHECK (max_turns >= 0),
            used_turns BIGINT NOT NULL DEFAULT 0 CHECK (used_turns >= 0 AND used_turns <= max_turns),
            max_tokens BIGINT NOT NULL CHECK (max_tokens >= 0),
            used_tokens BIGINT NOT NULL DEFAULT 0 CHECK (used_tokens >= 0 AND used_tokens <= max_tokens),
            token_hash TEXT CHECK (token_hash ~ '^[0-9a-f]{64}$'),
            lease_expires_at TIMESTAMPTZ,
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (goal_id,id),
            UNIQUE (goal_id,id,depth),
            CONSTRAINT fk_invocation_parent_lead FOREIGN KEY
                (goal_id,parent_invocation_id,parent_depth)
                REFERENCES switchboard.invocations(goal_id,id,depth),
            CONSTRAINT ck_invocation_shape CHECK (
                (role='lead' AND depth=0 AND parent_invocation_id IS NULL)
                OR (role='child' AND depth=1 AND parent_invocation_id IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_goal_active_lead ON switchboard.invocations(goal_id)
        WHERE depth=0 AND status IN ('launching','running')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS switchboard.effects (
            id UUID PRIMARY KEY,
            goal_id UUID NOT NULL,
            invocation_id UUID NOT NULL,
            capability TEXT NOT NULL,
            args_digest TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'requested','parked','approved','dispatched','committed','failed','unknown',
                'refused','rejected','expired','cancelled')),
            approval_ref TEXT,
            receipt_ref TEXT,
            delivery_ref TEXT UNIQUE,
            recipient_entity UUID,
            recipient_channel TEXT,
            prompt_accepts_input BOOLEAN NOT NULL DEFAULT false,
            prompt_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (goal_id,id),
            CONSTRAINT fk_effect_invocation FOREIGN KEY (goal_id,invocation_id)
                REFERENCES switchboard.invocations(goal_id,id),
            CONSTRAINT ck_effect_prompt_binding CHECK (NOT prompt_accepts_input OR
                (delivery_ref IS NOT NULL AND recipient_entity IS NOT NULL
                 AND recipient_channel IS NOT NULL AND prompt_expires_at IS NOT NULL
                 AND isfinite(prompt_expires_at)))
        )
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='switchboard.goals'::regclass AND conname='fk_goal_cause_effect') THEN
                ALTER TABLE switchboard.goals ADD CONSTRAINT fk_goal_cause_effect
                FOREIGN KEY (caused_by_goal,caused_by_effect)
                REFERENCES switchboard.effects(goal_id,id);
            END IF;
        END $$
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS switchboard.budgets (
            goal_id UUID PRIMARY KEY REFERENCES switchboard.goals(id),
            max_invocations BIGINT NOT NULL CHECK (max_invocations >= 0),
            reserved_invocations BIGINT NOT NULL DEFAULT 0 CHECK (reserved_invocations >= 0),
            used_invocations BIGINT NOT NULL DEFAULT 0 CHECK (used_invocations >= 0),
            max_children BIGINT NOT NULL CHECK (max_children >= 0),
            active_children BIGINT NOT NULL DEFAULT 0 CHECK (active_children >= 0 AND active_children <= max_children),
            max_calls BIGINT NOT NULL CHECK (max_calls >= 0),
            reserved_calls BIGINT NOT NULL DEFAULT 0 CHECK (reserved_calls >= 0),
            used_calls BIGINT NOT NULL DEFAULT 0 CHECK (used_calls >= 0),
            max_tokens BIGINT NOT NULL CHECK (max_tokens >= 0),
            reserved_tokens BIGINT NOT NULL DEFAULT 0 CHECK (reserved_tokens >= 0),
            used_tokens BIGINT NOT NULL DEFAULT 0 CHECK (used_tokens >= 0),
            max_spend NUMERIC NOT NULL CHECK (max_spend >= 0 AND
                max_spend NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
            reserved_spend NUMERIC NOT NULL DEFAULT 0 CHECK (reserved_spend >= 0 AND
                reserved_spend NOT IN ('NaN'::numeric,'Infinity'::numeric)),
            used_spend NUMERIC NOT NULL DEFAULT 0 CHECK (used_spend >= 0 AND
                used_spend NOT IN ('NaN'::numeric,'Infinity'::numeric)),
            deadline TIMESTAMPTZ NOT NULL CHECK (isfinite(deadline)),
            config_snapshot JSONB NOT NULL CHECK (jsonb_typeof(config_snapshot)='object'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (reserved_invocations + used_invocations <= max_invocations),
            CHECK (reserved_calls + used_calls <= max_calls),
            CHECK (reserved_tokens + used_tokens <= max_tokens),
            CHECK (reserved_spend + used_spend <= max_spend)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS switchboard.admission_decisions (
            id UUID PRIMARY KEY,
            attempt_id UUID NOT NULL UNIQUE,
            source_key TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK (source_kind IN ('message','occurrence','feed','domain','staffer')),
            goal_id UUID REFERENCES switchboard.goals(id),
            result TEXT NOT NULL CHECK (result IN ('admitted','joined','refused')),
            reason TEXT NOT NULL CHECK (reason IN (
                'source_evidence_unavailable','limits_unavailable','cause_invalid',
                'creator_unavailable','causal_chain_limit','reply_joined','quota_exhausted','new_goal')),
            origin TEXT CHECK (origin IN ('owner','obligation','third_party','staffer')),
            source_partition TEXT,
            quota_window TIMESTAMPTZ,
            retry_after TIMESTAMPTZ,
            delivery_ref TEXT,
            reply_effect_id UUID REFERENCES switchboard.effects(id),
            occurrence_key TEXT,
            caused_by_goal UUID,
            caused_by_effect UUID,
            catalog_digest TEXT,
            config_digest TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_admission_result CHECK (
                (result='refused' AND goal_id IS NULL)
                OR (result IN ('admitted','joined') AND goal_id IS NOT NULL)),
            CONSTRAINT ck_admission_quota CHECK (result <> 'admitted' OR
                (origin IS NOT NULL AND source_partition IS NOT NULL AND quota_window IS NOT NULL)),
            CONSTRAINT ck_decision_cause_pair CHECK ((caused_by_goal IS NULL) = (caused_by_effect IS NULL)),
            CONSTRAINT fk_decision_cause FOREIGN KEY (caused_by_goal,caused_by_effect)
                REFERENCES switchboard.effects(goal_id,id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_admission_source ON switchboard.admission_decisions(source_key)
        WHERE result IN ('admitted','joined')
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_admission_quota ON switchboard.admission_decisions
        (origin,quota_window,source_partition) WHERE result='admitted'
    """)
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles r
                WHERE (r.rolname LIKE 'butler_%_rw' OR r.rolname LIKE 'connector_%')
                AND (r.rolsuper OR r.rolbypassrls OR pg_has_role(r.rolname,current_user,'MEMBER'))
            ) THEN
                RAISE EXCEPTION 'Goal ledger requires isolated ordinary runtime roles';
            END IF;
        END $$
    """)
    # Ordinary owner bypass is intentional, as in sw_035: lossless pg_dump
    # continues to work with row_security=off. Bootstrap DML regrants do not
    # bypass these runtime policies. No runtime role owns these relations.
    for table in _TABLES:
        qualified = f"switchboard.{table}"
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {qualified} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO butler_switchboard_rw")
        for command in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            name = "goal_ledger_" + command.lower()
            op.execute(f"DROP POLICY IF EXISTS {name} ON {qualified}")
            policy = f"CREATE POLICY {name} ON {qualified} FOR {command} TO butler_switchboard_rw "
            predicate = "current_user='butler_switchboard_rw'"
            if table == "admission_decisions" and command in {"UPDATE", "DELETE"}:
                predicate += " AND false"
            if command != "INSERT":
                policy += f"USING ({predicate}) "
            if command in {"INSERT", "UPDATE"}:
                policy += f"WITH CHECK ({predicate})"
            op.execute(policy)
    op.execute("""
        CREATE OR REPLACE FUNCTION switchboard.goal_ledger_immutable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF TG_TABLE_NAME='admission_decisions' THEN
                RAISE EXCEPTION 'Admission history is immutable';
            ELSIF TG_TABLE_NAME='goals' THEN
                IF (to_jsonb(NEW) - ARRAY['status','exposed','updated_at']) IS DISTINCT FROM
                   (to_jsonb(OLD) - ARRAY['status','exposed','updated_at'])
                   OR (OLD.exposed AND NOT NEW.exposed) THEN
                    RAISE EXCEPTION 'Goal authority and source association are immutable';
                END IF;
            ELSIF TG_TABLE_NAME='invocations' THEN
                IF (NEW.goal_id,NEW.parent_invocation_id,NEW.depth,NEW.role,NEW.lead_epoch,
                    NEW.grant_snapshot,NEW.grant_digest,NEW.max_turns,NEW.max_tokens) IS DISTINCT FROM
                   (OLD.goal_id,OLD.parent_invocation_id,OLD.depth,OLD.role,OLD.lead_epoch,
                    OLD.grant_snapshot,OLD.grant_digest,OLD.max_turns,OLD.max_tokens) THEN
                    RAISE EXCEPTION 'Invocation lineage and grant are immutable';
                END IF;
            ELSIF TG_TABLE_NAME='effects' THEN
                IF (NEW.goal_id,NEW.invocation_id,NEW.capability,NEW.args_digest,
                    NEW.delivery_ref,NEW.recipient_entity,NEW.recipient_channel) IS DISTINCT FROM
                   (OLD.goal_id,OLD.invocation_id,OLD.capability,OLD.args_digest,
                    OLD.delivery_ref,OLD.recipient_entity,OLD.recipient_channel) THEN
                    RAISE EXCEPTION 'Effect lineage and recipient association are immutable';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in ("goals", "invocations", "effects", "admission_decisions"):
        op.execute(f"DROP TRIGGER IF EXISTS goal_ledger_immutable ON switchboard.{table}")
        event = "UPDATE OR DELETE" if table == "admission_decisions" else "UPDATE"
        op.execute(
            f"CREATE TRIGGER goal_ledger_immutable BEFORE {event} ON switchboard.{table} "
            "FOR EACH ROW EXECUTE FUNCTION switchboard.goal_ledger_immutable()"
        )


def downgrade() -> None:
    # Refuse evidence destruction even when no goal is currently running.
    for table in _TABLES:
        op.execute(f"LOCK TABLE switchboard.{table} IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.goals)
               OR EXISTS (SELECT 1 FROM switchboard.admission_decisions) THEN
                RAISE EXCEPTION 'Cannot discard goal ledger or admission history';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE switchboard.goals DROP CONSTRAINT IF EXISTS fk_goal_cause_effect")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS switchboard.{table}")
    op.execute("DROP FUNCTION IF EXISTS switchboard.goal_ledger_immutable()")

"""Owner decision receipts: the durable record of each owner approve or reject of goal effects.

Revision ID: sw_073
Revises: sw_072

RFC 0038 D11/D13 and M7 (bu-4c7nvp.1.3, PR-B). Every owner decision of parked goal effects,
whether one item or a whole decision set, goes through one shared approve-and-dispatch path
(``butlers.capability_broker.owner_decisions``). The path first commits a receipt here together
with the effects' ``parked -> approved`` (or ``-> rejected``) transitions, then mirrors the
decision to the owning domain and dispatches. The receipt is what a re-drive after a crash
trusts: an effect still ``approved`` under an approving receipt is driven to dispatch once,
never re-dispatched (D12).

Two tables:

- ``owner_decisions``: one receipt. ``decided_by`` and ``via`` name the verified owner context
  (bu-jv1moa.27 owns token redemption; the path receives a verified context only). A receipt for
  a decision set names exactly one version, and a version carries at most one receipt.
- ``owner_decision_items``: one row per item the receipt covers, binding the effect, its
  approval reference and stored-arguments digest. ``approved`` and ``rejected`` decide the
  effect; ``left`` records an item of a set the owner did not tick (bu-4c7nvp.1.3 Q3 = B: it
  stays parked for its own later decision). An effect carries at most one deciding item.

Both tables are append-only and nothing is deletable. The sw_072 guard is replaced so that a set
version reaches ``decided`` only when a receipt names it. A goal that ends (a Stop or any
terminal status) cancels its open set version in the same transaction, by trigger, whichever path
ended it. The downgrade refuses while any receipt exists, and restores the sw_072 guard.
"""

from alembic import op

revision = "sw_073"
down_revision = "sw_072"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"
_TABLES = ("owner_decisions", "owner_decision_items")
_HEX64 = "~ '^[0-9a-f]{64}$'"

_GUARD = """
    CREATE OR REPLACE FUNCTION switchboard.decision_set_immutable()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        IF TG_TABLE_NAME IN ('decision_sets','decision_set_items') THEN
            RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME;
        END IF;
        IF (to_jsonb(NEW) - ARRAY['state','updated_at'])
           IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','updated_at']) THEN
            RAISE EXCEPTION 'A decision set version is immutable except for its state';
        END IF;
        IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
            OLD.state = 'open' AND NEW.state IN ({targets})) THEN
            RAISE EXCEPTION 'Illegal decision set transition % -> %', OLD.state, NEW.state;
        END IF;
        {decided}
        RETURN NEW;
    END $$;
"""

_DECIDED_NEEDS_RECEIPT = """
        IF NEW.state = 'decided' AND OLD.state IS DISTINCT FROM 'decided' AND NOT EXISTS (
            SELECT 1 FROM switchboard.owner_decisions d
             WHERE d.set_id = NEW.set_id AND d.set_version = NEW.version) THEN
            RAISE EXCEPTION 'A decision set is decided only by an owner decision receipt';
        END IF;
"""

_SW072_COMMENT = "-- No decision path exists yet: decided arrives with the resolver's receipt."


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE switchboard.owner_decisions (
            id UUID PRIMARY KEY,
            goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
            set_id UUID,
            set_version BIGINT,
            decision TEXT NOT NULL CHECK (decision IN ('approve','reject')),
            decided_by TEXT NOT NULL CHECK (decided_by <> ''),
            via TEXT NOT NULL CHECK (via <> ''),
            decided_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK ((set_id IS NULL) = (set_version IS NULL)),
            UNIQUE (goal_id, id),
            UNIQUE (set_id, set_version),
            FOREIGN KEY (set_id, set_version, goal_id)
                REFERENCES switchboard.decision_set_versions(set_id, version, goal_id)
        );
        CREATE TABLE switchboard.owner_decision_items (
            decision_id UUID NOT NULL,
            goal_id UUID NOT NULL,
            effect_id UUID NOT NULL,
            approval_ref TEXT NOT NULL,
            args_digest TEXT NOT NULL CHECK (args_digest {_HEX64}),
            outcome TEXT NOT NULL CHECK (outcome IN ('approved','rejected','left')),
            PRIMARY KEY (decision_id, effect_id),
            FOREIGN KEY (goal_id, decision_id) REFERENCES switchboard.owner_decisions(goal_id, id),
            FOREIGN KEY (goal_id, effect_id) REFERENCES switchboard.effects(goal_id, id)
        );
        CREATE UNIQUE INDEX uq_owner_decision_effect ON switchboard.owner_decision_items(effect_id)
            WHERE outcome <> 'left';
    """)
    op.execute(
        _GUARD.format(
            targets="'superseded','cancelled','expired','decided'",
            decided=_DECIDED_NEEDS_RECEIPT,
        )
    )
    op.execute("""
        CREATE FUNCTION switchboard.owner_decision_immutable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Owner decisions are append-only';
        END $$;
        CREATE FUNCTION switchboard.owner_decision_durable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Owner decisions are durable' USING ERRCODE = 'insufficient_privilege';
        END $$;
        -- S2.2: a goal that ends (a Stop or any terminal status) cancels its open decision set
        -- in the same transaction, whichever path ended it.
        CREATE FUNCTION switchboard.decision_set_goal_ended()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            UPDATE switchboard.decision_set_versions
               SET state = 'cancelled', updated_at = clock_timestamp()
             WHERE goal_id = NEW.id AND state = 'open';
            RETURN NULL;
        END $$;
        CREATE TRIGGER decision_set_goal_ended AFTER UPDATE OF status ON switchboard.goals
            FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status AND NEW.status IN (
                'completed','partial','failed','stopped','budget_exhausted'))
            EXECUTE FUNCTION switchboard.decision_set_goal_ended();
    """)
    statements = []
    for table in _TABLES:
        qualified = f"switchboard.{table}"
        guard = f"current_user = '{_RUNTIME}'"
        statements += [
            f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {qualified} FROM PUBLIC",
            f"GRANT SELECT, INSERT ON {qualified} TO {_RUNTIME}",
            f"CREATE POLICY owner_decision_read ON {qualified} FOR SELECT TO {_RUNTIME} "
            f"USING ({guard})",
            f"CREATE POLICY owner_decision_insert ON {qualified} FOR INSERT TO {_RUNTIME} "
            f"WITH CHECK ({guard})",
            f"CREATE TRIGGER owner_decision_immutable BEFORE UPDATE ON {qualified} "
            "FOR EACH ROW EXECUTE FUNCTION switchboard.owner_decision_immutable()",
            f"CREATE TRIGGER owner_decision_durable BEFORE DELETE OR TRUNCATE ON {qualified} "
            "FOR EACH STATEMENT EXECUTE FUNCTION switchboard.owner_decision_durable()",
        ]
    op.execute(";\n".join(statements))


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('switchboard.owner_decisions') IS NOT NULL
               AND EXISTS (SELECT 1 FROM switchboard.owner_decisions) THEN
                RAISE EXCEPTION 'Cannot discard owner decisions';
            END IF;
        END $$;
        DROP TRIGGER IF EXISTS decision_set_goal_ended ON switchboard.goals;
        DROP FUNCTION IF EXISTS switchboard.decision_set_goal_ended();
        DROP TABLE IF EXISTS switchboard.owner_decision_items;
        DROP TABLE IF EXISTS switchboard.owner_decisions;
        DROP FUNCTION IF EXISTS switchboard.owner_decision_immutable();
        DROP FUNCTION IF EXISTS switchboard.owner_decision_durable();
    """)
    op.execute(_GUARD.format(targets="'superseded','cancelled','expired'", decided=_SW072_COMMENT))

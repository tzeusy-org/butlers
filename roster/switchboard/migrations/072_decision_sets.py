"""Decision sets: one owner decision over several exact parked calls of one goal.

Revision ID: sw_072
Revises: sw_071

RFC 0038 D13 and M7 (bu-4c7nvp.1.3, PR-A). A goal's lead may group effects that are already
parked for the owner's per-event approval into one decision set, so the owner can approve them
with one tap. bu-qlx055 = A (q3, q11): that tap counts as the per-event approval of each item,
because every item's exact call and recipients are shown, digest-bound and individually
untickable. A set therefore grants nothing of its own. It is a grouping of individually parked,
individually digest-bound calls; each approved item still runs through the broker as its own
stored call.

Three tables:

- ``decision_sets``: the identity of one set of one goal.
- ``decision_set_versions``: an immutable snapshot of the set's membership. A changed membership
  is a new version of the same set, and the previous open version becomes ``superseded``; it is
  never edited. The version binds the goal's lead epoch and plan version when it was proposed,
  the set digest over its ordered item digests, and an expiry no later than the goal's budget
  deadline. At most one version per goal is ``open``.
- ``decision_set_items``: one row per item of a version. Each binds the parked effect and a
  snapshot of its capability, stored-arguments digest and recipient, plus ``item_digest`` over
  exactly those facts. ``explicit_only`` marks an item the owner must tick on its own (an
  ``administer`` capability: a rule or schedule change is never pre-ticked, bu-qlx055 q3).

Items and set identities are append-only; a version may change only its state, along
``open -> superseded | cancelled | expired``. Nothing can reach ``decided`` yet: the owner's
decision path and its receipt arrive with the resolver (PR-B), which replaces this guard.
Nothing is deletable. The Switchboard runtime role may read and insert, and may update only a
version's state; the guard refuses anything else.

Dark: no listed goal tool creates a set yet (``goal.decide_together`` stays unlisted until the
resolver lands). The downgrade refuses while any set exists.
"""

from alembic import op

revision = "sw_072"
down_revision = "sw_071"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"
_MAX_ITEMS = 16  # butlers.core.decision_sets.MAX_SET_ITEMS
_TABLES = ("decision_sets", "decision_set_versions", "decision_set_items")
_HEX64 = "~ '^[0-9a-f]{64}$'"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE switchboard.decision_sets (
            id UUID PRIMARY KEY,
            goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (goal_id, id)
        );
        CREATE TABLE switchboard.decision_set_versions (
            set_id UUID NOT NULL,
            version BIGINT NOT NULL CHECK (version >= 1),
            goal_id UUID NOT NULL,
            epoch BIGINT NOT NULL,
            plan_version BIGINT NOT NULL,
            created_by UUID NOT NULL,
            item_count SMALLINT NOT NULL CHECK (item_count BETWEEN 2 AND {_MAX_ITEMS}),
            digest TEXT NOT NULL CHECK (digest {_HEX64}),
            expires_at TIMESTAMPTZ NOT NULL,
            state TEXT NOT NULL DEFAULT 'open'
                CHECK (state IN ('open','superseded','decided','cancelled','expired')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (set_id, version),
            UNIQUE (set_id, version, goal_id),
            FOREIGN KEY (goal_id, set_id) REFERENCES switchboard.decision_sets(goal_id, id),
            FOREIGN KEY (goal_id, epoch) REFERENCES switchboard.goal_epochs(goal_id, epoch),
            FOREIGN KEY (goal_id, plan_version)
                REFERENCES switchboard.goal_plans(goal_id, version),
            FOREIGN KEY (goal_id, created_by) REFERENCES switchboard.invocations(goal_id, id)
        );
        CREATE UNIQUE INDEX uq_decision_set_open ON switchboard.decision_set_versions(goal_id)
            WHERE state = 'open';
        CREATE TABLE switchboard.decision_set_items (
            set_id UUID NOT NULL,
            set_version BIGINT NOT NULL,
            position SMALLINT NOT NULL CHECK (position >= 0 AND position < {_MAX_ITEMS}),
            goal_id UUID NOT NULL,
            effect_id UUID NOT NULL,
            capability TEXT NOT NULL,
            args_digest TEXT NOT NULL,
            recipient_entity UUID,
            recipient_channel TEXT,
            explicit_only BOOLEAN NOT NULL,
            item_digest TEXT NOT NULL CHECK (item_digest {_HEX64}),
            PRIMARY KEY (set_id, set_version, position),
            UNIQUE (set_id, set_version, effect_id),
            FOREIGN KEY (set_id, set_version, goal_id)
                REFERENCES switchboard.decision_set_versions(set_id, version, goal_id),
            FOREIGN KEY (goal_id, effect_id) REFERENCES switchboard.effects(goal_id, id)
        );
        CREATE INDEX ix_decision_set_items_effect ON switchboard.decision_set_items(effect_id);
    """)
    op.execute("""
        CREATE FUNCTION switchboard.decision_set_immutable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF TG_TABLE_NAME IN ('decision_sets','decision_set_items') THEN
                RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME;
            END IF;
            IF (to_jsonb(NEW) - ARRAY['state','updated_at'])
               IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','updated_at']) THEN
                RAISE EXCEPTION 'A decision set version is immutable except for its state';
            END IF;
            -- No decision path exists yet: decided arrives with the resolver's receipt.
            IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
                OLD.state = 'open' AND NEW.state IN ('superseded','cancelled','expired')) THEN
                RAISE EXCEPTION 'Illegal decision set transition % -> %', OLD.state, NEW.state;
            END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION switchboard.decision_set_durable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Decision sets are durable' USING ERRCODE = 'insufficient_privilege';
        END $$;
    """)
    statements = []
    for table in _TABLES:
        qualified = f"switchboard.{table}"
        guard = f"current_user = '{_RUNTIME}'"
        mutable = table == "decision_set_versions"
        statements += [
            f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {qualified} FROM PUBLIC",
            f"GRANT SELECT, INSERT{', UPDATE' if mutable else ''} ON {qualified} TO {_RUNTIME}",
            f"CREATE POLICY decision_set_read ON {qualified} FOR SELECT TO {_RUNTIME} "
            f"USING ({guard})",
            f"CREATE POLICY decision_set_insert ON {qualified} FOR INSERT TO {_RUNTIME} "
            f"WITH CHECK ({guard})",
            f"CREATE TRIGGER decision_set_immutable BEFORE UPDATE ON {qualified} "
            "FOR EACH ROW EXECUTE FUNCTION switchboard.decision_set_immutable()",
            f"CREATE TRIGGER decision_set_durable BEFORE DELETE OR TRUNCATE ON {qualified} "
            "FOR EACH STATEMENT EXECUTE FUNCTION switchboard.decision_set_durable()",
        ]
        if mutable:
            statements.append(
                f"CREATE POLICY decision_set_update ON {qualified} FOR UPDATE TO {_RUNTIME} "
                f"USING ({guard}) WITH CHECK ({guard})"
            )
    op.execute(";\n".join(statements))


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('switchboard.decision_sets') IS NOT NULL
               AND EXISTS (SELECT 1 FROM switchboard.decision_sets) THEN
                RAISE EXCEPTION 'Cannot discard decision sets';
            END IF;
        END $$;
        DROP TABLE IF EXISTS switchboard.decision_set_items;
        DROP TABLE IF EXISTS switchboard.decision_set_versions;
        DROP TABLE IF EXISTS switchboard.decision_sets;
        DROP FUNCTION IF EXISTS switchboard.decision_set_immutable();
        DROP FUNCTION IF EXISTS switchboard.decision_set_durable();
    """)

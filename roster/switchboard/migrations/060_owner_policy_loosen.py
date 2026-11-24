"""Owner policy loosening under the dashboard step-up (RFC 0038 S1, bu-dhloi1.29).

Revision ID: sw_060
Revises: sw_059

sw_059 refused every loosening UPDATE of a term. The dashboard step-up now has
a ``policy_loosen`` kind (``scripts/init-db.sql``), so a loosening is admitted
exactly when a step-up for that resulting term state was consumed in the same
transaction:

* ``owner_policy_tighten_only`` keeps every other sw_059 invariant (identity,
  selector and narrowing fixed; version up by exactly one; a fresh owner
  action). A tightening needs nothing more. A loosening needs a
  ``dashboard_stepup.bindings`` row of kind ``policy_loosen``, not revoked,
  consumed by this transaction and not yet finished, whose target is the new
  row's ``id:version:term:expiry`` (expiry in integer epoch microseconds, or
  ``none``). Without the step-up store, or without the privilege to read it
  (the runtime role never has it), every loosening still refuses.
* ``owner_policy_receipts.action`` admits ``loosen`` (``to_version`` =
  ``from_version`` + 1).

The downgrade refuses while any ``loosen`` receipt exists, then restores
sw_059's tighten-only trigger and receipt check.
"""

from alembic import op

revision = "sw_060"
down_revision = "sw_059"
branch_labels = None
depends_on = None

_INVARIANTS = """
        IF (NEW.id, NEW.selector_kind, NEW.selector, NEW.resources, NEW.created_at)
               IS DISTINCT FROM
           (OLD.id, OLD.selector_kind, OLD.selector, OLD.resources, OLD.created_at)
           OR NEW.version IS DISTINCT FROM OLD.version + 1
           OR NEW.owner_action_id = OLD.owner_action_id THEN
            RAISE EXCEPTION 'Owner policy terms change one version at a time'
                USING ERRCODE = 'insufficient_privilege';
        END IF;"""

_LOOSENING = """
           (OLD.term = 'never' AND NEW.term <> 'never')
           OR (NEW.expires_at IS NOT NULL
               AND (OLD.expires_at IS NULL OR NEW.expires_at < OLD.expires_at))"""

_TRIGGER = f"""
    CREATE OR REPLACE FUNCTION switchboard.owner_policy_tighten_only()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        target text;
        bound boolean;
    BEGIN{_INVARIANTS}
        IF NOT ({_LOOSENING}) THEN
            RETURN NEW;
        END IF;
        IF to_regclass('dashboard_stepup.bindings') IS NULL THEN
            RAISE EXCEPTION 'Owner policy loosening needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NOT has_schema_privilege('dashboard_stepup', 'USAGE')
           OR NOT has_table_privilege('dashboard_stepup.bindings', 'SELECT') THEN
            RAISE EXCEPTION 'Owner policy loosening needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        target := NEW.id::text || ':' || NEW.version::text || ':' || NEW.term || ':'
            || COALESCE(((extract(epoch FROM NEW.expires_at) * 1000000)::bigint)::text, 'none');
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM dashboard_stepup.bindings WHERE kind = '
            || quote_literal('policy_loosen') || ' AND target = $1 AND NOT revoked '
            || 'AND consumed_tx = txid_current() AND result IS NULL)'
            INTO bound USING target;
        IF NOT bound THEN
            RAISE EXCEPTION 'Owner policy loosening needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$;
"""

_TIGHTEN_ONLY = """
    CREATE OR REPLACE FUNCTION switchboard.owner_policy_tighten_only()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        IF (NEW.id, NEW.selector_kind, NEW.selector, NEW.resources, NEW.created_at)
               IS DISTINCT FROM
           (OLD.id, OLD.selector_kind, OLD.selector, OLD.resources, OLD.created_at)
           OR NEW.version IS DISTINCT FROM OLD.version + 1
           OR (OLD.term = 'never' AND NEW.term <> 'never')
           OR (NEW.expires_at IS NOT NULL
               AND (OLD.expires_at IS NULL OR NEW.expires_at < OLD.expires_at))
           OR NEW.owner_action_id = OLD.owner_action_id THEN
            RAISE EXCEPTION 'Owner policy terms only tighten, one version at a time'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$;
"""


def _receipt_actions(loosen: bool) -> str:
    actions = "'create','tighten','loosen'" if loosen else "'create','tighten'"
    versions = "action IN ('tighten','loosen')" if loosen else "action = 'tighten'"
    return f"""
    ALTER TABLE switchboard.owner_policy_receipts
        DROP CONSTRAINT owner_policy_receipts_action_check,
        DROP CONSTRAINT ck_owner_policy_receipt_versions,
        ADD CONSTRAINT owner_policy_receipts_action_check CHECK (action IN ({actions})),
        ADD CONSTRAINT ck_owner_policy_receipt_versions CHECK (
            (action = 'create' AND from_version IS NULL AND to_version = 1)
            OR ({versions} AND to_version = from_version + 1));
"""


def upgrade() -> None:
    op.execute(_receipt_actions(loosen=True))
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("LOCK TABLE switchboard.owner_policy_receipts IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.owner_policy_receipts
                       WHERE action = 'loosen') THEN
                RAISE EXCEPTION 'Cannot discard owner policy loosening';
            END IF;
        END $$;
    """)
    op.execute(_TIGHTEN_ONLY)
    op.execute(_receipt_actions(loosen=False))

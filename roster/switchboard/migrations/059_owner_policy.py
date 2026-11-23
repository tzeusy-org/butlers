"""Owner tighten-only policy terms and their holds (RFC 0038 S1, bu-dhloi1.10).

Revision ID: sw_059
Revises: sw_058

The owner may hold capability calls with ``never`` or ``always_ask`` terms
selected by exact capability, region (catalog ``owner_domain``) or effect
class, optionally narrowed by resource arguments (REQ-capability-authorization-020).

* ``owner_policy_state`` is one row with a monotonic ``generation``. Every
  policy change raises it under ``FOR UPDATE``; the broker reads it ``FOR
  SHARE`` inside the call's goal-locked transaction, so a change and a call
  serialise at the effect fence. Without the row the broker refuses.
* ``owner_policy_terms`` holds the terms. Selector and resource narrowing are
  fixed at creation; every change raises ``version`` by exactly one
  (expected-version CAS) and may only tighten: ``always_ask`` to ``never``, or
  a later or no expiry. Loosening, including removal, has no path here; it
  needs the owner's dashboard step-up, which does not admit it yet.
* ``owner_policy_receipts`` is one append-only receipt per owner action, so a
  retried action returns its receipt rather than changing policy twice.
* ``owner_policy_holds`` records, content-blind, the terms and generation that
  held a call (parked effect or refusal): the holding constraint, term ids,
  versions, constraints and selectors only.
* ``broker_refusals.reason`` admits ``owner_policy_ask_on_read``: an
  ``always_ask`` read cannot park, so it refuses with its own reason.

Grants cannot be the guard (core_001 re-grants own-schema DML). RLS lets the
switchboard daemon role, where the broker and every model session's tools
run, only read terms and the generation and record holds; owner changes run on
the dashboard's owner connection. Triggers refuse every DELETE and TRUNCATE,
every UPDATE of receipts and holds, and every loosening UPDATE of a term, for
the runtime role and the owner alike. The downgrade refuses while any term,
receipt, hold or ask-on-read refusal exists.
"""

from alembic import op

revision = "sw_059"
down_revision = "sw_058"
branch_labels = None
depends_on = None

_TABLES = (
    "owner_policy_state",
    "owner_policy_terms",
    "owner_policy_receipts",
    "owner_policy_holds",
)

_SCHEMA = """
    CREATE TABLE switchboard.owner_policy_state (
        id SMALLINT PRIMARY KEY CHECK (id = 1),
        generation BIGINT NOT NULL CHECK (generation >= 0),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    INSERT INTO switchboard.owner_policy_state (id, generation) VALUES (1, 0);

    CREATE TABLE switchboard.owner_policy_terms (
        id UUID PRIMARY KEY,
        selector_kind TEXT NOT NULL CHECK (selector_kind IN ('capability','region','effect_class')),
        selector TEXT NOT NULL,
        -- {"<resource argument>": ["<id>", ...]}; empty narrows nothing.
        resources JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(resources) = 'object'),
        term TEXT NOT NULL CHECK (term IN ('never','always_ask')),
        version BIGINT NOT NULL CHECK (version >= 1),
        expires_at TIMESTAMPTZ,
        owner_action_id UUID NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_owner_policy_selector CHECK (
            (selector_kind = 'capability'
                AND selector ~ '^[a-z][a-z0-9_]{0,62}__[a-z][a-z0-9_]{0,126}$')
            OR (selector_kind = 'region' AND selector ~ '^[a-z][a-z0-9_]{0,62}$')
            OR (selector_kind = 'effect_class' AND selector IN
                ('read','record','communicate','transact','actuate','administer')))
    );

    CREATE TABLE switchboard.owner_policy_receipts (
        owner_action_id UUID PRIMARY KEY,
        term_id UUID NOT NULL REFERENCES switchboard.owner_policy_terms(id),
        action TEXT NOT NULL CHECK (action IN ('create','tighten')),
        request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
        from_version BIGINT,
        to_version BIGINT NOT NULL,
        generation BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_owner_policy_receipt_versions CHECK (
            (action = 'create' AND from_version IS NULL AND to_version = 1)
            OR (action = 'tighten' AND to_version = from_version + 1))
    );

    CREATE TABLE switchboard.owner_policy_holds (
        id UUID PRIMARY KEY,
        goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
        invocation_id UUID NOT NULL,
        capability TEXT NOT NULL
            CHECK (capability ~ '^[a-z][a-z0-9_]{0,62}__[a-z][a-z0-9_]{0,126}$'),
        disposition TEXT NOT NULL CHECK (disposition IN ('refused','parked')),
        -- The holding constraint: never if any holding term is never.
        term TEXT NOT NULL CHECK (term IN ('never','always_ask')),
        -- The parked effect, or the approved effect an owner term later refused.
        effect_id UUID,
        -- The broker refusal record of a refused call.
        refusal_id UUID REFERENCES switchboard.broker_refusals(id),
        generation BIGINT NOT NULL CHECK (generation >= 0),
        -- [{"id", "version", "term", "selector_kind", "selector"}, ...]
        terms JSONB NOT NULL CHECK (jsonb_typeof(terms) = 'array' AND jsonb_array_length(terms) > 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_owner_policy_hold_subject CHECK (
            (disposition = 'parked' AND term = 'always_ask'
                AND effect_id IS NOT NULL AND refusal_id IS NULL)
            OR (disposition = 'refused' AND (effect_id IS NOT NULL OR refusal_id IS NOT NULL))),
        CONSTRAINT fk_owner_policy_hold_invocation FOREIGN KEY (goal_id, invocation_id)
            REFERENCES switchboard.invocations(goal_id, id)
    );
    CREATE INDEX ix_owner_policy_holds_goal ON switchboard.owner_policy_holds (goal_id, created_at);
    CREATE INDEX ix_owner_policy_holds_effect ON switchboard.owner_policy_holds (effect_id)
        WHERE effect_id IS NOT NULL;
    CREATE INDEX ix_owner_policy_holds_refusal ON switchboard.owner_policy_holds (refusal_id)
        WHERE refusal_id IS NOT NULL;
"""

# An always_ask term on a read cannot park (a read has no pending action), so it
# refuses with its own closed reason the owner can tell apart from never.
_REASONS = (
    "'authentication_refused','goal_not_running','lead_epoch_revoked',"
    "'capability_not_granted','catalog_changed','resource_outside_grant',"
    "'policy_refused','budget_exhausted','effect_already_recorded','broker_unavailable'"
)
_ASK_ON_READ = "'owner_policy_ask_on_read'"


def _refusal_reasons(reasons: str) -> str:
    return (
        "ALTER TABLE switchboard.broker_refusals "
        "DROP CONSTRAINT broker_refusals_reason_check, "
        f"ADD CONSTRAINT broker_refusals_reason_check CHECK (reason IN ({reasons}))"
    )


_GUARDS = """
    CREATE FUNCTION switchboard.owner_policy_durable()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        RAISE EXCEPTION 'Owner policy history is durable'
            USING ERRCODE = 'insufficient_privilege';
    END $$;

    -- Expected-version CAS and tighten-only, on the row as finally written.
    CREATE FUNCTION switchboard.owner_policy_tighten_only()
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

    CREATE FUNCTION switchboard.owner_policy_generation()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.generation <= OLD.generation THEN
            RAISE EXCEPTION 'Owner policy generation only increases'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$;

    CREATE TRIGGER owner_policy_durable BEFORE DELETE OR TRUNCATE
        ON switchboard.owner_policy_state FOR EACH STATEMENT
        EXECUTE FUNCTION switchboard.owner_policy_durable();
    CREATE TRIGGER owner_policy_durable BEFORE DELETE OR TRUNCATE
        ON switchboard.owner_policy_terms FOR EACH STATEMENT
        EXECUTE FUNCTION switchboard.owner_policy_durable();
    CREATE TRIGGER owner_policy_durable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON switchboard.owner_policy_receipts FOR EACH STATEMENT
        EXECUTE FUNCTION switchboard.owner_policy_durable();
    CREATE TRIGGER owner_policy_durable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON switchboard.owner_policy_holds FOR EACH STATEMENT
        EXECUTE FUNCTION switchboard.owner_policy_durable();
    -- AFTER ROW constraint triggers see the final row, so a later-sorting
    -- BEFORE trigger (the runtime role holds TRIGGER) cannot launder a change.
    CREATE CONSTRAINT TRIGGER owner_policy_tighten_only AFTER UPDATE
        ON switchboard.owner_policy_terms FOR EACH ROW
        EXECUTE FUNCTION switchboard.owner_policy_tighten_only();
    CREATE CONSTRAINT TRIGGER owner_policy_generation AFTER UPDATE
        ON switchboard.owner_policy_state FOR EACH ROW
        EXECUTE FUNCTION switchboard.owner_policy_generation();
"""


# The switchboard daemon role (where the broker and every model session's tools
# run) only reads the law and records holds. It never writes a term, a receipt
# or the generation: core_001 re-grants own-schema DML, so RLS, not the grant,
# is the guard. Owner changes run on the dashboard's owner connection (the
# table owner, outside RLS) behind the verified dashboard session.
_RUNTIME = "current_user='butler_switchboard_rw'"
_RUNTIME_ACCESS = {
    "owner_policy_state": {
        "SELECT": f"USING ({_RUNTIME})",
        # FOR SHARE needs an UPDATE policy's USING; WITH CHECK refuses a write.
        "UPDATE": f"USING ({_RUNTIME}) WITH CHECK (false)",
    },
    "owner_policy_terms": {"SELECT": f"USING ({_RUNTIME})"},
    "owner_policy_receipts": {},
    "owner_policy_holds": {
        "SELECT": f"USING ({_RUNTIME})",
        "INSERT": f"WITH CHECK ({_RUNTIME})",
    },
}


def _access() -> str:
    statements = []
    for table in _TABLES:
        qualified = f"switchboard.{table}"
        policies = _RUNTIME_ACCESS[table]
        statements += [
            f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {qualified} FROM PUBLIC",
            f"REVOKE ALL ON {qualified} FROM butler_switchboard_rw",
        ]
        if policies:
            statements.append(
                f"GRANT {', '.join(policies)} ON {qualified} TO butler_switchboard_rw"
            )
        for command, clause in policies.items():
            statements.append(
                f"CREATE POLICY owner_policy_{command.lower()} ON {qualified} FOR {command} "
                f"TO butler_switchboard_rw {clause}"
            )
    return ";\n".join(statements) + ";"


def upgrade() -> None:
    op.execute(_SCHEMA)
    op.execute(_GUARDS)
    op.execute(_access())
    op.execute(_refusal_reasons(f"{_REASONS},{_ASK_ON_READ}"))


def downgrade() -> None:
    for table in (*_TABLES, "broker_refusals"):
        op.execute(f"LOCK TABLE switchboard.{table} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.owner_policy_terms)
               OR EXISTS (SELECT 1 FROM switchboard.owner_policy_receipts)
               OR EXISTS (SELECT 1 FROM switchboard.owner_policy_holds)
               OR EXISTS (SELECT 1 FROM switchboard.broker_refusals
                          WHERE reason = {_ASK_ON_READ}) THEN
                RAISE EXCEPTION 'Cannot discard durable owner policy';
            END IF;
        END $$;
        {_refusal_reasons(_REASONS)};
        DROP TABLE switchboard.owner_policy_holds;
        DROP TABLE switchboard.owner_policy_receipts;
        DROP TABLE switchboard.owner_policy_terms;
        DROP TABLE switchboard.owner_policy_state;
        DROP FUNCTION switchboard.owner_policy_durable();
        DROP FUNCTION switchboard.owner_policy_tighten_only();
        DROP FUNCTION switchboard.owner_policy_generation();
    """)

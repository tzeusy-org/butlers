"""Owner-delivery subject members: a set card holds its items' individual cards (bu-jv1moa.80).

Revision ID: core_332
Revises: core_331
Create Date: 2026-11-14 00:00:00.000000

A decision-set card (kind ``decision_set``) lists the same calls that each have their own approval
card. ``public.owner_delivery_subject_members`` records which items a live set card covers, and
``public.owner_delivery_item_held(member_schema, member_ref)`` tells a domain's approval delivery
worker whether an item's individual card should wait. A hold changes no state: the individual
presentation stays ``ready`` or ``retry_wait`` and is claimed again once the subject resolves.

* The table is append-only (statement-level guards on UPDATE, DELETE and TRUNCATE). A hold is
  released by the *subject's* state, never by deleting a row.
* ``butler_switchboard_rw`` holds SELECT and INSERT, narrowed by an own-schema row policy. No other
  role has any privilege on the table, and nobody has UPDATE or DELETE.
* The delivery worker of an owning domain runs on that domain's role and the subject rows belong to
  switchboard, so it cannot read them. It calls the SECURITY DEFINER function instead, which
  answers one boolean (pinned ``search_path``, schema-qualified names, EXECUTE only to the butler
  runtime roles). It answers *held* only for a live telegram presentation of the subject's current
  generation, whose subject is unresolved.
* EXECUTE is revoked from PUBLIC first (Postgres grants it by default), then granted by a loop over
  the ``butler_%_rw`` roles that exist when this migration runs. A butler role created later needs
  the grant re-applied (the core chain re-runs it per schema, and ``scripts/init-db.sql`` default
  privileges do not cover functions).
* Inserts are fenced to an unresolved subject and to the subject's own schema.

The core chain runs once per butler schema over these shared objects: every statement is
idempotent under an advisory lock, and only the last schema still at core_332 drops them. The
downgrade refuses while any row exists.
"""

from __future__ import annotations

from alembic import op

revision = "core_332"
down_revision = "core_331"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_332:subject-members', 0))"
_TABLE = "public.owner_delivery_subject_members"
_FUNCTION = "public.owner_delivery_item_held(text, text)"
_ROLLBACK_REFUSAL = "core_332 downgrade refused: owner-delivery subject members exist"

_CREATE = """
CREATE TABLE IF NOT EXISTS public.owner_delivery_subject_members (
    subject_id     UUID NOT NULL,
    owning_schema  TEXT NOT NULL,
    member_schema  TEXT NOT NULL CHECK (member_schema ~ '^[a-z][a-z0-9_]{0,62}$'),
    member_ref     TEXT NOT NULL CHECK (char_length(member_ref) BETWEEN 1 AND 256),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (subject_id, member_schema, member_ref),
    FOREIGN KEY (subject_id, owning_schema)
        REFERENCES public.owner_delivery_subjects (id, owning_schema)
);
CREATE INDEX IF NOT EXISTS ix_owner_delivery_subject_members_item
    ON public.owner_delivery_subject_members (member_schema, member_ref);
"""

_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION public.owner_delivery_member_fence() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE current_state TEXT;
BEGIN
    SELECT state INTO current_state FROM public.owner_delivery_subjects
     WHERE id = NEW.subject_id AND owning_schema = NEW.owning_schema;
    IF current_state IS NULL OR current_state IN ('decided', 'cancelled', 'expired') THEN
        RAISE EXCEPTION 'owner-delivery members are minted only at an open subject'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.owner_delivery_members_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;

CREATE OR REPLACE FUNCTION public.owner_delivery_item_held(p_member_schema text, p_member_ref text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1
          FROM public.owner_delivery_subject_members AS m
          JOIN public.owner_delivery_subjects AS s
            ON s.id = m.subject_id AND s.owning_schema = m.owning_schema
          JOIN public.owner_delivery_presentations AS p
            ON p.subject_id = s.id AND p.generation = s.generation
         WHERE m.member_schema = p_member_schema
           AND m.member_ref = p_member_ref
           AND s.state IN ('pending', 'delivered', 'acknowledged', 'unknown')
           AND p.surface = 'telegram'
           AND p.state IN ('sent', 'unknown')
    )
$$;
"""

_TRIGGERS = """
DROP TRIGGER IF EXISTS owner_delivery_members_fence ON public.owner_delivery_subject_members;
CREATE TRIGGER owner_delivery_members_fence BEFORE INSERT
    ON public.owner_delivery_subject_members
    FOR EACH ROW EXECUTE FUNCTION public.owner_delivery_member_fence();
DROP TRIGGER IF EXISTS owner_delivery_members_append_only ON public.owner_delivery_subject_members;
CREATE TRIGGER owner_delivery_members_append_only BEFORE UPDATE OR DELETE
    ON public.owner_delivery_subject_members
    FOR EACH STATEMENT EXECUTE FUNCTION public.owner_delivery_members_append_only();
DROP TRIGGER IF EXISTS owner_delivery_members_no_truncate ON public.owner_delivery_subject_members;
CREATE TRIGGER owner_delivery_members_no_truncate BEFORE TRUNCATE
    ON public.owner_delivery_subject_members
    FOR EACH STATEMENT EXECUTE FUNCTION public.owner_delivery_members_append_only();
"""


def _grants_and_policy() -> None:
    # init-db.sql's default privileges hand every runtime role DML on new public tables, so the
    # table starts from nothing: only switchboard writes and reads it, and nobody updates it.
    # The REVOKEs act on objects this migration owns, so they are never wrapped in a tolerant
    # block; only a grant to a role this login cannot administer may be skipped, role by role.
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {_FUNCTION} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        DECLARE role_name TEXT;
        BEGIN
            FOR role_name IN SELECT rolname FROM pg_roles
                WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\' OR rolname = 'connector_writer'
            LOOP
                EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM %I', role_name);
            END LOOP;
            FOR role_name IN SELECT rolname FROM pg_roles
                WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
            LOOP
                BEGIN
                    EXECUTE format('GRANT EXECUTE ON FUNCTION {_FUNCTION} TO %I', role_name);
                    IF role_name = 'butler_switchboard_rw' THEN
                        EXECUTE format('GRANT SELECT, INSERT ON TABLE {_TABLE} TO %I', role_name);
                    END IF;
                EXCEPTION WHEN insufficient_privilege THEN NULL;
                END;
            END LOOP;
        END $$
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS owner_delivery_subject_members_own_schema ON " + _TABLE)
    op.execute(
        f"""
        CREATE POLICY owner_delivery_subject_members_own_schema ON {_TABLE}
            USING (current_user = 'butler_' || owning_schema || '_rw')
            WITH CHECK (current_user = 'butler_' || owning_schema || '_rw')
        """
    )


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared objects."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 332
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    # A schema migrated without the core ledger (core_317) has no subject to hold for.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.owner_delivery_subjects') IS NULL THEN
                RAISE EXCEPTION 'core_332 requires core_317 owner-delivery tables';
            END IF;
        END
        $$
        """
    )
    op.execute(_CREATE)
    op.execute(_FUNCTIONS)
    op.execute(_TRIGGERS)
    _grants_and_policy()


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
    op.execute("DROP FUNCTION IF EXISTS public.owner_delivery_member_fence()")
    op.execute("DROP FUNCTION IF EXISTS public.owner_delivery_members_append_only()")

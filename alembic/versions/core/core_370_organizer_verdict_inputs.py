"""Organizer verdict inputs: two read-only definers for the calendar-hosting roles (bu-5ii4gj.6).

Revision ID: core_370
Revises: core_369
Create Date: 2026-11-24 00:00:00.000000

The calendar projection runs under each hosting butler's own role, which cannot read the two
owner-owned inputs of the organizer verdict (REQ-module-calendar-039): the identity tables and
Switchboard's ingestion rules. Two SECURITY DEFINER functions in ``public`` answer exactly those
reads and nothing more:

* ``organizer_identity_class(email)`` takes one exact, normalized address and returns only a class
  (``known_entity``, ``known_domain`` or ``none``): no names, no entity ids, no pattern matching,
  so it cannot enumerate contacts. Unidentified placeholders, merged and deleted entities and an
  ambiguous address never count as known. A read failure propagates.
* ``organizer_policy_rules()`` returns the enabled global and Google Calendar connector ingestion
  rules in the shape the evaluator loads them, so the verdict applies the same first-match engine
  as the connector instead of a second SQL copy that could drift. It returns only the columns the
  evaluator reads (no rule name or timestamp) and only the rule types a calendar envelope can
  match: ``sender_address``, ``sender_domain``, ``source_channel``, ``substring`` and
  ``source_endpoint``. A chat, channel, mic, header, label or MIME rule is never returned. A read
  failure propagates.

Both pin ``search_path = pg_catalog, pg_temp`` and converge their ACL exactly: every grantee
except the owner and the calendar-hosting runtime roles that exist (PUBLIC included) is revoked. The core chain runs once per butler schema: every run
takes one advisory lock and replaces the functions, which is idempotent and takes no table lock.
Downgrade drops them only when no other schema-local chain is still at core_370 or later.
"""

from __future__ import annotations

from alembic import op

revision = "core_370"
down_revision = "core_369"
branch_labels = None
depends_on = None

_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_370:organizer-verdict-inputs', 0))"
)
_ROLES = (
    "butler_general_rw",
    "butler_health_rw",
    "butler_lifestyle_rw",
    "butler_finance_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_travel_rw",
)
_IDENTITY = "public.organizer_identity_class(text)"
_RULES = "public.organizer_policy_rules()"

# Shared consumer mailbox domains say nothing about who the organizer is, so they never make a
# domain "known" however many contacts use them.
_IDENTITY_DDL = """
CREATE OR REPLACE FUNCTION public.organizer_identity_class(p_email text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    _email text := lower(btrim(p_email));
    _domain text;
    _exact integer;
    _freemail text[] := ARRAY[
        'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com',
        'yahoo.com', 'icloud.com', 'me.com', 'aol.com', 'proton.me', 'protonmail.com'
    ];
BEGIN
    IF _email IS NULL OR _email !~ '^[^@\\s]+@[^@\\s]+$' THEN
        RETURN 'none';
    END IF;
    _domain := split_part(_email, '@', 2);
    SELECT count(DISTINCT ef.subject) INTO _exact
    FROM relationship.entity_facts ef
    JOIN public.entities e ON e.id = ef.subject
    WHERE ef.predicate = 'has-email' AND ef.object_kind = 'literal' AND ef.validity = 'active'
      AND lower(btrim(ef.object)) = _email
      AND e.metadata ->> 'merged_into' IS NULL AND e.metadata ->> 'deleted_at' IS NULL
      AND COALESCE(e.metadata ->> 'unidentified', 'false') <> 'true';
    IF _exact = 1 THEN
        RETURN 'known_entity';
    ELSIF _exact > 1 THEN
        RETURN 'none';
    END IF;
    IF _domain = ANY (_freemail) THEN
        RETURN 'none';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM relationship.entity_facts ef
        JOIN public.entities e ON e.id = ef.subject
        WHERE ef.predicate = 'has-email' AND ef.object_kind = 'literal'
          AND ef.validity = 'active'
          AND split_part(lower(btrim(ef.object)), '@', 2) = _domain
          AND e.metadata ->> 'merged_into' IS NULL AND e.metadata ->> 'deleted_at' IS NULL
          AND COALESCE(e.metadata ->> 'unidentified', 'false') <> 'true'
    ) THEN
        RETURN 'known_domain';
    END IF;
    RETURN 'none';
END $$
"""
_RULES_DDL = """
CREATE OR REPLACE FUNCTION public.organizer_policy_rules()
RETURNS TABLE (
    scope text, id text, rule_type text, condition jsonb, action text, priority integer,
    created_by text, promoted_from_suggestion_id text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RETURN QUERY
    SELECT r.scope, r.id::text, r.rule_type, r.condition, r.action, r.priority,
           r.created_by, r.promoted_from_suggestion_id::text
    FROM switchboard.ingestion_rules r
    WHERE r.enabled = TRUE AND r.deleted_at IS NULL
      AND (r.scope = 'global' OR starts_with(r.scope, 'connector:google_calendar:'))
      AND r.rule_type IN (
          'sender_address', 'sender_domain', 'source_channel', 'substring', 'source_endpoint'
      )
    ORDER BY r.scope, r.priority ASC, r.created_at ASC, r.id ASC;
END $$
"""


def _grants(signature: str) -> list[str]:
    """Converge the ACL: revoke every grantee but the owner, then grant the existing roles."""
    roles = ", ".join(f"'{role}'" for role in _ROLES)
    statements = [
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC",
        f"""
        DO $$
        DECLARE
            _grantee name;
        BEGIN
            FOR _grantee IN
                SELECT r.rolname
                FROM pg_proc p
                CROSS JOIN LATERAL aclexplode(p.proacl) a
                JOIN pg_roles r ON r.oid = a.grantee
                WHERE p.oid = to_regprocedure('{signature}')
                  AND a.grantee <> p.proowner
                  AND r.rolname NOT IN ({roles})
            LOOP
                EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM %I', _grantee);
            END LOOP;
        END $$
        """,
    ]
    for role in _ROLES:
        statements.append(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION {signature} FROM {role};
                    GRANT EXECUTE ON FUNCTION {signature} TO {role};
                END IF;
            END $$
            """
        )
    return statements


def _installation_count() -> int:
    """Count schema-local core chains still at core_370 or later (includes this one)."""
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
                    THEN substring(version_num FROM 6)::integer >= 370
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    for ddl in (_IDENTITY_DDL, _RULES_DDL):
        op.execute(ddl)
    for statement in (*_grants(_IDENTITY), *_grants(_RULES)):
        op.execute(statement)


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(f"DROP FUNCTION IF EXISTS {_IDENTITY}")
    op.execute(f"DROP FUNCTION IF EXISTS {_RULES}")

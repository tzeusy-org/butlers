"""calendar_event_participants and the attendee identity claim read (bu-5ii4gj.3 S1).

Revision ID: core_390
Revises: core_371
Create Date: 2026-11-24 12:00:00.000000

Placeholder revision number: renumbered above main's highest core revision at the final rebase.

``calendar_event_participants`` is one row per attendee address of a provider calendar event, in
the calendar-hosting butler's own schema (like ``calendar_event_entities``). It is an "invited"
claim, never an interaction: it carries the normalized address (never shown to a reader), a display
name, a role, the RSVP status and a resolution state. Rows with an address are unique per
``(event_id, email_norm)``; a ``manual`` row (an owner-made link) has no address and is unique per
``(event_id, entity_id)``.

``public.attendee_identity_claims(text[])`` is the one cross-schema read the hosting roles lack:
exact normalized addresses in (at most 200), ``(email_norm, entity_id, is_owner)`` out, one row per
active ``has-email`` claimant, no names; merged, deleted and unidentified entities are excluded.
An address with two live claimants returns two rows and the resolver marks it ambiguous. It is
SECURITY DEFINER with a pinned ``search_path`` and its ACL converges to the butler runtime roles
(PUBLIC revoked). The core chain runs once per butler schema: every run takes one advisory lock and
replaces the function, which is idempotent; the downgrade drops the function only when no other
schema-local chain is still at core_390 or later, and always drops this schema's table.
"""

from __future__ import annotations

from alembic import op
from butlers.db_authority.roles import butler_roles

revision = "core_390"
down_revision = "core_371"
branch_labels = None
depends_on = None

_LOCK = (
    "SELECT pg_advisory_xact_lock("
    "hashtextextended('butlers:core_390:calendar-event-participants', 0))"
)
_ROLES = butler_roles()
_CLAIMS = "public.attendee_identity_claims(text[])"
MAX_ADDRESSES = 200

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS calendar_event_participants (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id         UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    email_norm       TEXT,
    display_name     TEXT,
    role             TEXT NOT NULL
        CHECK (role IN ('organizer', 'required', 'optional', 'resource')),
    response_status  TEXT NOT NULL
        CHECK (response_status IN ('needsAction', 'accepted', 'declined', 'tentative')),
    entity_id        UUID REFERENCES public.entities(id) ON DELETE CASCADE,
    resolution       TEXT NOT NULL
        CHECK (resolution IN ('exact', 'ambiguous', 'unresolved', 'owner', 'resource', 'manual')),
    resolver_version TEXT NOT NULL,
    resolved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (email_norm IS NOT NULL OR (resolution = 'manual' AND entity_id IS NOT NULL)),
    CHECK (resolution NOT IN ('exact', 'manual') OR entity_id IS NOT NULL),
    CHECK (resolution NOT IN ('ambiguous', 'unresolved', 'resource') OR entity_id IS NULL)
)
"""
_INDEXES = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_calendar_event_participants_email
    ON calendar_event_participants (event_id, email_norm) WHERE email_norm IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_calendar_event_participants_manual
    ON calendar_event_participants (event_id, entity_id) WHERE email_norm IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_calendar_event_participants_entity
    ON calendar_event_participants (entity_id) WHERE entity_id IS NOT NULL
    """,
)

_CLAIMS_DDL = f"""
CREATE OR REPLACE FUNCTION public.attendee_identity_claims(p_emails text[])
RETURNS TABLE (email_norm text, entity_id uuid, is_owner boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF p_emails IS NULL THEN
        RETURN;
    END IF;
    IF cardinality(p_emails) > {MAX_ADDRESSES} THEN
        RAISE EXCEPTION 'attendee_identity_claims accepts at most {MAX_ADDRESSES} addresses'
            USING ERRCODE = '54000';
    END IF;
    RETURN QUERY
    SELECT DISTINCT lower(btrim(ef.object)), ef.subject,
           ('owner' = ANY (COALESCE(e.roles, '{{}}'::text[])))
    FROM relationship.entity_facts ef
    JOIN public.entities e ON e.id = ef.subject
    WHERE ef.predicate = 'has-email' AND ef.object_kind = 'literal' AND ef.validity = 'active'
      AND lower(btrim(ef.object)) = ANY (
          ARRAY(SELECT lower(btrim(wanted)) FROM unnest(p_emails) AS wanted))
      AND e.metadata ->> 'merged_into' IS NULL AND e.metadata ->> 'deleted_at' IS NULL
      AND COALESCE(e.metadata ->> 'unidentified', 'false') <> 'true'
    ORDER BY 1, 2;
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
    """Count schema-local core chains still at core_390 or later (includes this one)."""
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
                    THEN substring(version_num FROM 6)::integer >= 390
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(_TABLE_DDL)
    for ddl in _INDEXES:
        op.execute(ddl)
    op.execute(_CLAIMS_DDL)
    for statement in _grants(_CLAIMS):
        op.execute(statement)


def downgrade() -> None:
    op.execute(_LOCK)
    op.execute("DROP TABLE IF EXISTS calendar_event_participants")
    if _installation_count() > 1:
        return
    op.execute(f"DROP FUNCTION IF EXISTS {_CLAIMS}")

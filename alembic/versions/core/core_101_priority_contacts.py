"""priority_contacts: FK join table + cascade-delete audit trigger.

Revision ID: core_101
Revises: core_100
Create Date: 2026-05-17 00:00:00.000000

Phase 3a (bu-1f91v.6).  Creates ``public.priority_contacts`` — the DB-backed
priority-contact table that supersedes the flat-file ``GMAIL_KNOWN_CONTACTS_PATH``
mechanism (see spec ingestion-priority-contacts §Requirement: Priority contacts data
model).

Schema
------
contact_id  UUID NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE
butler      TEXT NOT NULL
added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
added_by    TEXT
PRIMARY KEY (contact_id, butler)

Indexes
-------
idx_priority_contacts_butler  (butler)  — per-butler lookup

Cascade-delete audit trigger
-----------------------------
An AFTER DELETE row-level trigger on ``priority_contacts`` fires whenever a
cascade deletion removes a row (e.g. the underlying contact in ``public.contacts``
is deleted).  The trigger inserts one ``public.audit_log`` row per affected row
with:
  action = 'ingestion.priority_contact.cascade_remove'
  target = '<contact_id>:<butler>'
  actor  = 'system:contact_cascade'
  note   = 'contact removed from public.contacts'

The trigger uses a direct INSERT into ``public.audit_log`` rather than calling
Python application code (triggers cannot call application-layer functions).

Grants
------
SELECT, INSERT, UPDATE, DELETE on priority_contacts granted to all runtime roles.
"""

from __future__ import annotations

from alembic import op

revision = "core_101"
down_revision = "core_100"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # --- Table ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.priority_contacts (
            contact_id  UUID        NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE,
            butler      TEXT        NOT NULL,
            added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            added_by    TEXT,
            PRIMARY KEY (contact_id, butler)
        )
    """)

    # Per-butler lookup index
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_priority_contacts_butler
        ON public.priority_contacts (butler)
    """)

    # --- Cascade-delete audit trigger ---
    # The trigger function inserts one audit_log row per deleted priority_contacts
    # row so that cascaded removals (from DELETE on public.contacts) are observable.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.priority_contacts_cascade_audit()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        BEGIN
            INSERT INTO public.audit_log (actor, action, target, note)
            VALUES (
                'system:contact_cascade',
                'ingestion.priority_contact.cascade_remove',
                OLD.contact_id::text || ':' || OLD.butler,
                'contact removed from public.contacts'
            );
            RETURN OLD;
        END;
        $$
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_priority_contacts_cascade_audit
        ON public.priority_contacts
    """)

    op.execute("""
        CREATE TRIGGER trg_priority_contacts_cascade_audit
        AFTER DELETE ON public.priority_contacts
        FOR EACH ROW
        EXECUTE FUNCTION public.priority_contacts_cascade_audit()
    """)

    # --- Grants ---
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.priority_contacts", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS trg_priority_contacts_cascade_audit
        ON public.priority_contacts
    """)
    op.execute("DROP FUNCTION IF EXISTS public.priority_contacts_cascade_audit()")
    op.execute("DROP INDEX IF EXISTS public.idx_priority_contacts_butler")
    op.execute("DROP TABLE IF EXISTS public.priority_contacts")

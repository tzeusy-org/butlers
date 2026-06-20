"""consolidate_contacts_to_public

Move all contacts from relationship.contacts to public.contacts and drop the
relationship-local shadow table.  Re-point all FK references from relationship
child tables to public.contacts.

Background: The relationship butler's search_path (relationship, public) caused
unqualified ``INSERT INTO contacts`` to write to relationship.contacts, while
``public.contact_info`` has a FK to ``public.contacts``.  This mismatch causes
FK violations during Telegram contact sync.

The spec (openspec/specs/contacts-identity/spec.md) mandates a single
``public.contacts`` table for all cross-butler identity resolution.

Revision ID: rel_003
Revises: rel_002
Create Date: 2026-03-31 00:00:00.000000

"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_003"
down_revision = "rel_002"
branch_labels = None
depends_on = None

# All FK constraints from relationship tables that reference relationship.contacts.
# Format: (table, constraint_name, column, on_delete)
_CHILD_FKS = [
    ("relationships", "relationships_contact_a_fkey", "contact_a", "CASCADE"),
    ("relationships", "relationships_contact_b_fkey", "contact_b", "CASCADE"),
    ("important_dates", "important_dates_contact_id_fkey", "contact_id", "CASCADE"),
    ("notes", "notes_contact_id_fkey", "contact_id", "CASCADE"),
    ("interactions", "interactions_contact_id_fkey", "contact_id", "CASCADE"),
    (
        "contacts_source_links",
        "contacts_source_links_local_contact_id_fkey",
        "local_contact_id",
        "CASCADE",
    ),
    ("reminders", "reminders_contact_id_fkey", "contact_id", "CASCADE"),
    ("gifts", "gifts_contact_id_fkey", "contact_id", "CASCADE"),
    ("loans", "loans_contact_id_fkey", "contact_id", "CASCADE"),
    ("group_members", "group_members_contact_id_fkey", "contact_id", "CASCADE"),
    ("contact_labels", "contact_labels_contact_id_fkey", "contact_id", "CASCADE"),
    ("quick_facts", "quick_facts_contact_id_fkey", "contact_id", "CASCADE"),
    ("activity_feed", "activity_feed_contact_id_fkey", "contact_id", "CASCADE"),
]


def _table_exists(conn, table: str) -> bool:
    """Return True if ``relationship.<table>`` exists in the current database."""
    return conn.execute(text(f"SELECT to_regclass('relationship.{table}')")).scalar() is not None


def _public_contacts_has_preferred_channel(conn) -> bool:
    """Return True if ``public.contacts.preferred_channel`` still exists.

    The core chain's core_123 DROPs this write-orphaned column. alembic
    version_locations have no guaranteed ordering, so on a fresh provision
    core_123 may run before this rel_003 migration; both upgrade and downgrade
    must stay order-independent and not reference a column that may be gone.
    """
    return (
        conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = 'contacts'
                  AND column_name  = 'preferred_channel'
                """
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()

    # Guard: if relationship.contacts doesn't exist, nothing to do.
    exists = conn.execute(text("SELECT to_regclass('relationship.contacts')")).scalar()
    if exists is None:
        return

    # Cross-chain DROP guard (cross-chain-migration-drop-hazard, bu-y6o7q):
    # core_134 DROPs public.contacts as the FINAL step of the contacts-schema
    # retirement. In a fresh schema-scoped provision the core chain reaches head —
    # and that DROP runs — BEFORE this relationship chain, so public.contacts is
    # already gone when rel_003 runs. There is then nothing to consolidate INTO,
    # and (critically) the rest of the relationship chain creates child tables with
    # FKs to the UNQUALIFIED ``contacts`` table (rel_005 addresses, rel_006 groups,
    # …) which resolve to ``relationship.contacts`` via the search_path. So when
    # public.contacts is absent we must LEAVE the relationship.contacts shadow in
    # place (do NOT consolidate or drop it) so those downstream FKs keep resolving;
    # rel_030 / rel_032 later drop the contact FK constraints by name. In
    # production this branch never runs — rel_003 ran historically when
    # public.contacts still existed — so the shadow only persists in fresh
    # schema-scoped test provisions, where it is a harmless empty table.
    if conn.execute(text("SELECT to_regclass('public.contacts')")).scalar() is None:
        return

    # Step 1: Copy contacts from relationship.contacts → public.contacts.
    # No ID overlap (verified), so INSERT without conflict handling.
    #
    # Cross-chain guard (cross-chain-migration-drop-hazard, bu-1yihq): the core
    # chain's core_123 DROPs public.contacts.preferred_channel. alembic
    # version_locations have no guaranteed ordering, so on a fresh provision
    # core_123 may run BEFORE this rel_003. When the column is gone, omit it from
    # the INSERT so this migration stays order-independent (the column was
    # write-orphaned and superseded by the entity-keyed prefers-channel fact).
    pref_present = _public_contacts_has_preferred_channel(conn)
    pref_col = "preferred_channel," if pref_present else ""

    # Data-preservation guard (bu-33077): when core_123 has already dropped
    # public.contacts.preferred_channel, the copy above omits the column, so any
    # non-null preference still living in relationship.contacts (rel_002 added the
    # column) would be silently lost when Step 5 drops the table — and it cannot
    # be backfilled into a prefers-channel fact here because relationship.entity_facts
    # is not created until rel_013, which runs after this migration. Snapshot those
    # values first so they remain recoverable (mirrors core_123's snapshot-before-drop).
    if not pref_present:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS
                    public.contacts_preferred_channel_dropbak_rel_003 AS
                SELECT id, entity_id, preferred_channel
                FROM relationship.contacts
                WHERE preferred_channel IS NOT NULL
                """
            )
        )

    conn.execute(
        text(f"""
        INSERT INTO public.contacts (
            id, name, details, first_name, last_name, nickname,
            company, job_title, gender, pronouns, avatar_url,
            listed, archived_at, metadata, stay_in_touch_days,
            entity_id, {pref_col} created_at, updated_at
        )
        SELECT
            id, name, details, first_name, last_name, nickname,
            company, job_title, gender, pronouns, avatar_url,
            COALESCE(listed, true),
            archived_at, metadata, stay_in_touch_days,
            entity_id, {pref_col}
            COALESCE(created_at, now()),
            COALESCE(updated_at, now())
        FROM relationship.contacts
        ON CONFLICT (id) DO NOTHING
    """)
    )

    # Only re-point FKs on child tables that actually exist in this schema.
    # ``contacts_source_links`` is owned by the contacts MODULE chain, which the
    # daemon/test harness applies AFTER the relationship butler chain (boot order
    # is core → butler → modules; see cli.py / lifecycle.py).  When this
    # migration runs at boot that table does not yet exist, so guard every
    # child-table operation against existence (cross-chain drop hazard).
    present_fks = [fk for fk in _CHILD_FKS if _table_exists(conn, fk[0])]

    # Step 2: Drop all FK constraints referencing relationship.contacts.
    for table, constraint, _col, _on_delete in present_fks:
        conn.execute(
            text(f"ALTER TABLE relationship.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        )

    # Step 3: Recreate FK constraints pointing to public.contacts.
    for table, constraint, col, on_delete in present_fks:
        conn.execute(
            text(
                f"ALTER TABLE relationship.{table} "
                f"ADD CONSTRAINT {constraint} "
                f"FOREIGN KEY ({col}) REFERENCES public.contacts(id) "
                f"ON DELETE {on_delete}"
            )
        )

    # Step 4: Drop the entity_id FK constraint on relationship.contacts
    # (it references public.entities and would block the DROP TABLE).
    conn.execute(
        text(
            "ALTER TABLE relationship.contacts DROP CONSTRAINT IF EXISTS rel_contacts_entity_id_fkey"
        )
    )

    # Step 5: Drop relationship.contacts.
    conn.execute(text("DROP TABLE relationship.contacts"))


def downgrade() -> None:
    conn = op.get_bind()

    # Recreate relationship.contacts with the aligned schema.
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS relationship.contacts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            details JSONB DEFAULT '{}',
            archived_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            first_name VARCHAR,
            last_name VARCHAR,
            nickname VARCHAR,
            company VARCHAR,
            job_title VARCHAR,
            gender VARCHAR,
            pronouns VARCHAR,
            avatar_url VARCHAR,
            listed BOOLEAN DEFAULT true,
            metadata JSONB,
            stay_in_touch_days INTEGER,
            preferred_channel VARCHAR,
            entity_id UUID REFERENCES public.entities(id) ON DELETE SET NULL
        )
    """)
    )

    # Copy contacts back from public that originated from relationship.
    # (We can't perfectly identify which ones came from relationship,
    # so we copy all — the downgrade is best-effort.)
    #
    # Cross-chain guard (mirror of upgrade): core_123 may have already DROPped
    # public.contacts.preferred_channel, in which case the source column is gone.
    # Select NULL for the recreated relationship.contacts.preferred_channel so the
    # downgrade stays order-independent (best-effort: the canonical store is the
    # entity-keyed prefers-channel fact, not this restored column).
    # Cross-chain DROP guard (mirror of upgrade): when core_134 has already
    # dropped public.contacts there is nothing to copy back; recreate the empty
    # relationship.contacts shadow and skip the copy (best-effort downgrade).
    public_contacts_present = (
        conn.execute(text("SELECT to_regclass('public.contacts')")).scalar() is not None
    )
    if public_contacts_present:
        pref_select = (
            "preferred_channel" if _public_contacts_has_preferred_channel(conn) else "NULL"
        )
        conn.execute(
            text(f"""
            INSERT INTO relationship.contacts (
                id, name, details, first_name, last_name, nickname,
                company, job_title, gender, pronouns, avatar_url,
                listed, archived_at, metadata, stay_in_touch_days,
                entity_id, preferred_channel, created_at, updated_at
            )
            SELECT
                id, name, details, first_name, last_name, nickname,
                company, job_title, gender, pronouns, avatar_url,
                listed, archived_at, metadata, stay_in_touch_days,
                entity_id, {pref_select}, created_at, updated_at
            FROM public.contacts
            ON CONFLICT (id) DO NOTHING
        """)
        )

    # Re-point FKs back to relationship.contacts (only for tables that exist;
    # ``contacts_source_links`` may not be present depending on chain order).
    for table, constraint, col, on_delete in _CHILD_FKS:
        if not _table_exists(conn, table):
            continue
        conn.execute(
            text(f"ALTER TABLE relationship.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        )
        conn.execute(
            text(
                f"ALTER TABLE relationship.{table} "
                f"ADD CONSTRAINT {constraint} "
                f"FOREIGN KEY ({col}) REFERENCES relationship.contacts(id) "
                f"ON DELETE {on_delete}"
            )
        )

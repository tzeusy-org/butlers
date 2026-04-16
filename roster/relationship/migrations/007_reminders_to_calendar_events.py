"""reminders_to_calendar_events

Migrate existing reminder facts (predicate='reminder') to calendar_events as
native calendar entries. Rename the legacy reminders table to _reminders_backup.

Revision ID: rel_007
Revises: rel_006
Create Date: 2026-04-16 00:00:00.000000

Migration steps:
  1. Ensure a calendar_sources row exists with source_kind='internal_reminders',
     lane='butler', butler_name='relationship'.
  2. For each active reminder fact (predicate='reminder', validity='active'):
       - Derive starts_at from metadata->>'next_trigger_at' or metadata->>'due_at'.
       - Set ends_at = starts_at + interval '15 minutes'.
       - Map reminder type → RRULE:
           one_time          → NULL
           recurring_yearly  → RRULE:FREQ=YEARLY
           recurring_monthly → RRULE:FREQ=MONTHLY
       - Set status from dismissed flag ('cancelled' if dismissed else 'confirmed').
       - Set source_butler = 'relationship'.
       - INSERT into calendar_events; skip rows where starts_at is NULL.
  3. Resolve entity_id for each migrated reminder:
       - Parse contact_id from fact subject (contact:{contact_id}:reminder:{uuid}).
       - Look up public.contacts.entity_id for that contact_id.
       - If entity_id found, INSERT into calendar_event_entities.
  4. Delete only active, non-superseded facts with predicate='reminder' (same WHERE
     clause as Step 2, preserving historical/invalid rows).
  5. Rename reminders table to _reminders_backup (guards against accidental queries).

This migration is idempotent: calendar_events inserts use ON CONFLICT DO UPDATE to
return the existing id on reruns, ensuring calendar_event_entities is always populated
even when the calendar_events row already exists from a partial run.
"""

from __future__ import annotations

import json
import uuid as _uuid

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_007"
down_revision = "rel_006"
branch_labels = None
depends_on = None

# Butler name that owns these reminders.
_BUTLER_NAME = "relationship"
_SOURCE_KEY = f"internal_reminders:{_BUTLER_NAME}"


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Guard: skip if calendar_events does not exist in this schema.
    # (Core chain must have been run first for this butler.)
    # ------------------------------------------------------------------
    calendar_events_exists = conn.execute(text("SELECT to_regclass('calendar_events')")).scalar()
    if calendar_events_exists is None:
        return

    # ------------------------------------------------------------------
    # Guard: skip if calendar_event_entities does not exist yet.
    # (core_074 must be applied before this migration.)
    # ------------------------------------------------------------------
    junction_exists = conn.execute(text("SELECT to_regclass('calendar_event_entities')")).scalar()
    if junction_exists is None:
        return

    # ------------------------------------------------------------------
    # Guard: skip if facts table does not exist (memory module not installed).
    # ------------------------------------------------------------------
    facts_exists = conn.execute(text("SELECT to_regclass('facts')")).scalar()
    if facts_exists is None:
        _rename_reminders_table(conn)
        return

    # ------------------------------------------------------------------
    # Step 1: Ensure calendar_sources row for internal_reminders.
    # ------------------------------------------------------------------
    source_id = conn.execute(
        text("""
            INSERT INTO calendar_sources (
                source_key, source_kind, lane, provider,
                butler_name, display_name, writable, metadata
            )
            VALUES (
                :source_key, 'internal_reminders', 'butler', 'internal',
                :butler_name, :display_name, true, '{}'::jsonb
            )
            ON CONFLICT (source_key) DO UPDATE
                SET updated_at = now()
            RETURNING id
        """),
        {
            "source_key": _SOURCE_KEY,
            "butler_name": _BUTLER_NAME,
            "display_name": f"{_BUTLER_NAME} reminders",
        },
    ).scalar()

    if source_id is None:
        # Already exists — fetch it.
        source_id = conn.execute(
            text("SELECT id FROM calendar_sources WHERE source_key = :sk"),
            {"sk": _SOURCE_KEY},
        ).scalar()

    # ------------------------------------------------------------------
    # Step 2: Load active reminder facts.
    # ------------------------------------------------------------------
    rows = conn.execute(
        text("""
            SELECT id, subject, content, metadata, entity_id, created_at
            FROM facts
            WHERE predicate = 'reminder'
              AND scope = 'relationship'
              AND validity = 'active'
              AND valid_at IS NULL
        """)
    ).fetchall()

    migrated_event_ids: list[tuple] = []  # (event_id, subject, fact_entity_id)

    for row in rows:
        fact_id = row[0]
        subject = row[1]
        content = row[2] or ""
        meta = row[3] or {}
        fact_entity_id = row[4]  # may be None
        created_at = row[5]

        if isinstance(meta, str):
            meta = json.loads(meta)

        # Derive starts_at from next_trigger_at or due_at
        due_at_str = meta.get("next_trigger_at") or meta.get("due_at")
        if not due_at_str:
            # No time information — cannot create a valid calendar event; skip.
            continue

        # Map reminder_type → recurrence_rule
        reminder_type = meta.get("type", "one_time")
        dismissed = bool(meta.get("dismissed", False))

        if reminder_type == "recurring_yearly":
            recurrence_rule = "RRULE:FREQ=YEARLY"
        elif reminder_type == "recurring_monthly":
            recurrence_rule = "RRULE:FREQ=MONTHLY"
        else:
            recurrence_rule = None

        status = "cancelled" if dismissed else "confirmed"
        origin_ref = str(fact_id)

        # INSERT into calendar_events — idempotent: on conflict return existing id so
        # entity associations are populated even on reruns / partial runs.
        insert_result = conn.execute(
            text("""
                INSERT INTO calendar_events (
                    source_id, origin_ref, title, body,
                    timezone, starts_at, ends_at,
                    all_day, status, visibility,
                    recurrence_rule, source_butler,
                    metadata, created_at, updated_at
                )
                VALUES (
                    :source_id, :origin_ref, :title, NULL,
                    'UTC',
                    :due_at_str::timestamptz,
                    :due_at_str::timestamptz + interval '15 minutes',
                    false, :status, 'default',
                    :recurrence_rule, 'relationship',
                    CAST(:metadata AS jsonb), :created_at, now()
                )
                ON CONFLICT (source_id, origin_ref) DO UPDATE
                    SET updated_at = EXCLUDED.updated_at
                RETURNING id
            """),
            {
                "source_id": source_id,
                "origin_ref": origin_ref,
                "title": content or "(untitled reminder)",
                "due_at_str": due_at_str,
                "status": status,
                "recurrence_rule": recurrence_rule,
                "metadata": _json_dumps(meta),
                "created_at": created_at,
            },
        ).scalar()

        if insert_result is not None:
            migrated_event_ids.append((insert_result, subject, fact_entity_id))

    # ------------------------------------------------------------------
    # Step 3: Resolve entity associations and populate calendar_event_entities.
    # Use entity_id stored on the fact directly when available; fall back to
    # parsing the subject (contact:{contact_id}:reminder:{uuid}) only when the
    # fact has no entity_id set.
    # ------------------------------------------------------------------
    for event_id, subject, fact_entity_id in migrated_event_ids:
        entity_id = fact_entity_id or _resolve_entity_id_from_subject(conn, subject)
        if entity_id is None:
            continue
        conn.execute(
            text("""
                INSERT INTO calendar_event_entities (event_id, entity_id)
                VALUES (:event_id, :entity_id)
                ON CONFLICT DO NOTHING
            """),
            {"event_id": event_id, "entity_id": entity_id},
        )

    # ------------------------------------------------------------------
    # Step 4: Delete only the active reminder facts that were eligible for
    # migration (same WHERE clause as the SELECT in Step 2).  Historical and
    # superseded rows (valid_at IS NOT NULL or validity != 'active') are left
    # untouched to avoid irreversible data loss.
    # ------------------------------------------------------------------
    conn.execute(
        text("""
            DELETE FROM facts
            WHERE predicate = 'reminder'
              AND scope = 'relationship'
              AND validity = 'active'
              AND valid_at IS NULL
        """)
    )

    # ------------------------------------------------------------------
    # Step 5: Rename reminders table to _reminders_backup.
    # ------------------------------------------------------------------
    _rename_reminders_table(conn)


def downgrade() -> None:
    conn = op.get_bind()

    # Restore reminders table name if backup exists.
    reminders_backup_exists = conn.execute(text("SELECT to_regclass('_reminders_backup')")).scalar()
    if reminders_backup_exists is not None:
        reminders_exists = conn.execute(text("SELECT to_regclass('reminders')")).scalar()
        if reminders_exists is None:
            conn.execute(text("ALTER TABLE _reminders_backup RENAME TO reminders"))

    # Remove migrated calendar_events rows (identified by source_key).
    source_id = conn.execute(
        text("SELECT id FROM calendar_sources WHERE source_key = :sk"),
        {"sk": _SOURCE_KEY},
    ).scalar()
    if source_id is not None:
        conn.execute(
            text("DELETE FROM calendar_events WHERE source_id = :sid"),
            {"sid": source_id},
        )
        conn.execute(
            text("DELETE FROM calendar_sources WHERE id = :sid"),
            {"sid": source_id},
        )

    # Note: reminder facts that were deleted cannot be restored in downgrade.
    # This is intentional — the downgrade preserves the table structure only.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rename_reminders_table(conn) -> None:  # type: ignore[no-untyped-def]
    """Rename reminders → _reminders_backup if reminders exists."""
    reminders_exists = conn.execute(text("SELECT to_regclass('reminders')")).scalar()
    if reminders_exists is not None:
        backup_exists = conn.execute(text("SELECT to_regclass('_reminders_backup')")).scalar()
        if backup_exists is None:
            conn.execute(text("ALTER TABLE reminders RENAME TO _reminders_backup"))


def _resolve_entity_id_from_subject(conn, subject: str):  # type: ignore[no-untyped-def]
    """Extract contact_id from a reminder subject and resolve its entity_id.

    Subject format: contact:{contact_id}:reminder:{uuid}
    Falls back to None if the subject does not match or the contact has no entity.
    """
    if not subject:
        return None
    parts = subject.split(":")
    if len(parts) != 4 or parts[0] != "contact" or parts[2] != "reminder":
        return None
    try:
        contact_id_str = parts[1]
        # Validate it looks like a UUID (basic check)
        contact_id = _uuid.UUID(contact_id_str)
    except (ValueError, IndexError):
        return None

    row = conn.execute(
        text("SELECT entity_id FROM public.contacts WHERE id = :cid"),
        {"cid": str(contact_id)},
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return row[0]


def _json_dumps(obj: object) -> str:
    """Serialize a dict to JSON string for pg JSONB insertion."""
    return json.dumps(obj, default=str)

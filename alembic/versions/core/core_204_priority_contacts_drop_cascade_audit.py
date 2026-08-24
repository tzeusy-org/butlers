"""priority_contacts: retire the unreachable cascade-delete audit trigger.

Revision ID: core_204
Revises: core_203
Create Date: 2026-08-24 00:00:00.000000

Issue: bu-fi36x.

What was wrong
--------------
``core_101`` installed an unconditional ``AFTER DELETE ... FOR EACH ROW`` trigger
on ``public.priority_contacts``. Its sole purpose was to make *cascaded* removals
observable: back then ``contact_id`` carried
``REFERENCES public.contacts(id) ON DELETE CASCADE``, so deleting a contact
removed the priority-contact row silently. The trigger wrote one audit row with
``action = 'ingestion.priority_contact.cascade_remove'``,
``actor  = 'system:contact_cascade'`` and
``note   = 'contact removed from public.contacts'``.

That premise no longer holds:

- ``core_131`` (bu-vat93) dropped ``priority_contacts_contact_id_fkey``. The
  replacement FK, ``priority_contacts_entity_id_fkey`` →
  ``public.entities(id) ON DELETE SET NULL``, nulls a column — it never removes
  a row. It is now the ONLY inbound FK on the table.
- ``core_134`` (bu-y6o7q) dropped ``public.contacts`` entirely.

With no cascading FK left, the trigger could only ever fire on a *direct* DELETE
— in practice the router's own ``DELETE /api/ingestion/priority-contacts/{id}``
(``src/butlers/api/routers/priority_contacts.py``), which already appends its own
``ingestion.priority_contact.remove`` row. So one ordinary removal wrote TWO
audit rows, and the second one asserted a provenance that could not occur.

Why removal rather than a conditional trigger
---------------------------------------------
A condition needs something to select for. There is no surviving cascade path to
distinguish, and a row trigger cannot tell the router's DELETE apart from any
other direct DELETE against the same table by the same role. The honest fix is to
delete the trigger and leave the audit row to the caller that knows why the row
went away. Should a cascading inbound FK ever be re-added,
``tests/migrations/test_priority_contacts_cascade_audit.py`` fails and forces a
deliberate re-decision.

Historical audit rows: DELIBERATELY LEFT UNTOUCHED
--------------------------------------------------
Any ``ingestion.priority_contact.cascade_remove`` rows already in
``public.audit_log`` keep their now-inaccurate note. Audit history is immutable:
rewriting or deleting landed audit rows so they read correctly in hindsight is a
worse defect than the inaccurate note, and it would destroy the only record that
the double-write ever happened. This migration therefore performs NO backfill and
NO UPDATE/DELETE against ``audit_log``. Those rows are dated evidence of the
defect fixed here, and no live path can produce another one.

Reversibility
-------------
``downgrade()`` recreates the butler-less trigger function and trigger exactly as
``core_129`` left them, restoring the pre-``core_204`` behaviour (double audit
rows included).
"""

from __future__ import annotations

from alembic import op

revision = "core_204"
down_revision = "core_203"
branch_labels = None
depends_on = None

_TABLE = "public.priority_contacts"
_TRIGGER = "trg_priority_contacts_cascade_audit"
_FUNCTION = "public.priority_contacts_cascade_audit()"

# Verbatim from core_129 (_TRIGGER_FN_GLOBAL) — the butler-less shape this
# migration retires, kept here so downgrade() is self-contained.
_TRIGGER_FN_GLOBAL = """
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
            OLD.contact_id::text,
            'contact removed from public.contacts'
        );
        RETURN OLD;
    END;
    $$
"""


def upgrade() -> None:
    # Idempotent, and a clean no-op on a provision where core_101 never ran:
    # DROP TRIGGER ... ON a missing table still errors, so guard on the table.
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NULL THEN
                RAISE NOTICE 'core_204: {_TABLE} not found — nothing to drop';
                RETURN;
            END IF;
            DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE};
        END
        $$;
    """)

    # Drop the function too. Leaving an orphaned trigger function behind is how a
    # future CREATE TRIGGER resurrects a defect nobody re-reviews.
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}")


def downgrade() -> None:
    # The function stands alone (no table dependency), so recreate it first and
    # unconditionally; only the trigger needs the table-presence guard.
    op.execute(_TRIGGER_FN_GLOBAL)
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NULL THEN
                RETURN;
            END IF;
            DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE};
            CREATE TRIGGER {_TRIGGER}
            AFTER DELETE ON {_TABLE}
            FOR EACH ROW
            EXECUTE FUNCTION public.priority_contacts_cascade_audit();
        END
        $$;
    """)

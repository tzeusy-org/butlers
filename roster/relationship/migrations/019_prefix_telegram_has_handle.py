"""entity_facts: prefix verbatim telegram has-handle objects with 'telegram:'.

Revision ID: rel_019
Revises: rel_018
Create Date: 2026-06-02 00:00:00.000000

Background
----------
Before bead bu-wni4z the reconciler, backfill script, and contact_info_add tool
wrote telegram_user_id / telegram_username values to ``relationship.entity_facts``
as ``has-handle`` with the raw ``contact_info.value`` as the object (verbatim,
no prefix).  The read path (``daemon._resolve_contact_channel_identifier``,
``ef_predicate_to_ci_type``) expects the canonical ``"telegram:<value>"`` form to
distinguish telegram entries from linkedin/twitter/other handles.

This migration prefixes those legacy verbatim rows in-place.

Disambiguation
--------------
We cannot reliably identify all telegram rows purely from the ``entity_facts``
table (the ``has-handle`` predicate is shared across telegram, linkedin, twitter,
and other types).  The safest approach is to JOIN back to ``public.contact_info``
via the contact's entity_id and only prefix rows whose contact_info source type is
in (``'telegram'``, ``'telegram_user_id'``, ``'telegram_username'``).

Rows that were written by the contacts module backfill path (not from contact_info)
and already carry the prefix are left unchanged (the WHERE clause excludes them).

Idempotency
-----------
The WHERE clause ``AND ef.object NOT LIKE 'telegram:%'`` ensures that rows already
carrying the prefix are skipped; re-running the migration is safe.

Safety
------
- Only ``validity = 'active'`` rows are updated (retracted rows are historical).
- The join to ``public.contact_info`` limits the update to rows traceable to a
  telegram-typed contact_info source.  Rows written by other paths (e.g. the
  contacts module's own telegram_provider that already added the prefix) are
  excluded by the ``NOT LIKE`` guard.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rel_019"
down_revision = "rel_018"
branch_labels = None
depends_on = None

# Types in public.contact_info that correspond to telegram has-handle entries.
_TELEGRAM_CI_TYPES = ("telegram", "telegram_user_id", "telegram_username")


def _contact_info_present() -> bool:
    """True if public.contact_info still exists.

    On a fresh provision this relationship migration may be scheduled after
    core_115 (bu-e2ja9) drops public.contact_info; the alembic chains have no
    guaranteed cross-chain ordering. There is nothing to prefix once the legacy
    table is gone, so this guard lets the migration no-op instead of failing on
    the missing table. Already-migrated databases recorded rel_019 long ago and
    never re-run it.
    """
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT to_regclass('public.contact_info')")).scalar() is not None


def upgrade() -> None:
    """Prefix verbatim telegram has-handle objects with 'telegram:'."""
    if not _contact_info_present():
        return
    types_sql = ", ".join(f"'{t}'" for t in _TELEGRAM_CI_TYPES)
    op.execute(f"""
        UPDATE relationship.entity_facts ef
        SET object     = 'telegram:' || ef.object,
            updated_at = now()
        WHERE ef.predicate   = 'has-handle'
          AND ef.object_kind = 'literal'
          AND ef.validity    = 'active'
          AND ef.object NOT LIKE 'telegram:%'
          AND EXISTS (
              SELECT 1
              FROM public.contacts c
              JOIN public.contact_info ci ON ci.contact_id = c.id
              WHERE c.entity_id = ef.subject
                AND ci.type IN ({types_sql})
                AND ci.value = ef.object
          )
    """)


def downgrade() -> None:
    """Strip the 'telegram:' prefix added by upgrade() (best-effort; may not be exact)."""
    if not _contact_info_present():
        return
    types_sql = ", ".join(f"'{t}'" for t in _TELEGRAM_CI_TYPES)
    op.execute(f"""
        UPDATE relationship.entity_facts ef
        SET object     = substring(ef.object FROM length('telegram:') + 1),
            updated_at = now()
        WHERE ef.predicate   = 'has-handle'
          AND ef.object_kind = 'literal'
          AND ef.validity    = 'active'
          AND ef.object LIKE 'telegram:%'
          AND EXISTS (
              SELECT 1
              FROM public.contacts c
              JOIN public.contact_info ci ON ci.contact_id = c.id
              WHERE c.entity_id = ef.subject
                AND ci.type IN ({types_sql})
                AND ci.value = substring(ef.object FROM length('telegram:') + 1)
          )
    """)

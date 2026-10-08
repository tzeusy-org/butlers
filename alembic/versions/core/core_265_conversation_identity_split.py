"""Split durable conversation identity from per-message reply targeting.

Revision ID: core_265
Revises: core_261
Create Date: 2026-10-08 00:00:00.000000

``public.dashboard_conversations`` gains ``external_conversation_id``, the
connector-supplied, channel-namespaced conversation key, plus a partial unique
index that replaces ``source_thread_identity`` as the anchor upsert target.

Data transform (first installing schema only):

* Telegram bot anchors (``source_channel`` ``telegram``/``telegram_bot``) whose
  identity is a legacy ``<chat_id>:<message_id>``, a bare ``<chat_id>``, or the
  core_208 helper's ``telegram:<chat_id>`` key on ``telegram:<chat_id>``.
* Telegram user-client and WhatsApp anchors key on their identity with the
  ``telegram:`` / ``whatsapp:`` namespace the connectors now emit, so a bare
  ``123`` and an already namespaced ``telegram:123`` share one key.
* Anchors sharing a key collapse to one per ``(butler, channel, key)``. The
  survivor is the row holding the newest provider handle; the other rows'
  messages and durable turns move onto it before those rows are deleted.
* Every other anchored row copies its identity verbatim.
* ``source_thread_identity`` is rewritten to the same value, so the core_185
  index and this revision's index stay congruent.

Every rewritten row and moved link is snapshotted into private ``core_265_*``
tables first. Downgrade restores the deleted rows, their message and turn links,
and the original identities, while keeping every other column of the survivors
as it stands (post-upgrade titles, statuses, and provider handles are not
reverted). It then verifies the restore against the snapshots and raises,
rolling the whole downgrade back with the snapshots intact, if any row or link
could not be restored. Only a complete restore drops the column, the index, and
the snapshot tables, so a later re-upgrade snapshots afresh.

Core revisions replay once per butler schema against the shared ``public``
tables. Upgrade therefore runs the transform only when it installs the column;
later schemas no-op. Downgrade restores only when the last schema at or past
this revision leaves it.

There is no mixed-version compatibility trigger: the rollout stops every
conversation-anchor writer before upgrading (bu-psarp), so no core_208 writer
runs against the migrated table.
"""

from __future__ import annotations

from alembic import context, op

revision = "core_265"
down_revision = "core_261"
branch_labels = None
depends_on = None

_SHARED_DDL_LOCK = "butlers:core_265:conversation-identity-split"

# The conversation key of a row matched by _COLLAPSE_PREDICATE.
_CONVERSATION_KEY_SQL = r"""
    CASE
        WHEN c.source_channel IN ('telegram', 'telegram_bot') THEN 'telegram:' || CASE
            WHEN c.source_thread_identity ~ '^telegram:-?[0-9]+$'
                THEN substring(c.source_thread_identity FROM 10)
            ELSE split_part(c.source_thread_identity, ':', 1)
        END
        WHEN c.source_channel = 'telegram_user_client'
            AND c.source_thread_identity NOT LIKE 'telegram:%'
            THEN 'telegram:' || c.source_thread_identity
        WHEN c.source_channel = 'whatsapp_user_client'
            AND c.source_thread_identity NOT LIKE 'whatsapp:%'
            THEN 'whatsapp:' || c.source_thread_identity
        ELSE c.source_thread_identity
    END
"""

_COLLAPSE_PREDICATE = r"""
    (
        c.source_channel IN ('telegram', 'telegram_bot')
        AND (
            c.source_thread_identity ~ '^-?[0-9]+(:[0-9]+)?$'
            OR c.source_thread_identity ~ '^telegram:-?[0-9]+$'
        )
    )
    OR (
        c.source_channel IN ('telegram_user_client', 'whatsapp_user_client')
        AND c.source_thread_identity IS NOT NULL
    )
"""

_BACKUP_TABLES = (
    "core_265_anchor_backup",
    "core_265_message_backup",
    "core_265_turn_backup",
)


def _installed_schema_count() -> int:
    """Count schemas whose core chain is at this revision or a descendant of it."""
    script = context.script
    descendants: set[str] = set()
    pending = [revision]
    while pending:
        current = pending.pop()
        if current in descendants:
            continue
        descendants.add(current)
        pending.extend(script.get_revision(current).nextrev)

    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND namespace.nspname NOT LIKE 'pg_%%'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in list(schemas):
        versions = set(
            bind.exec_driver_sql(f"SELECT version_num FROM {quote(schema)}.alembic_version")
            .scalars()
            .all()
        )
        count += int(bool(versions & descendants))
    return count


def _column_installed() -> bool:
    return bool(
        op.get_bind()
        .exec_driver_sql(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'dashboard_conversations'
                  AND column_name = 'external_conversation_id'
            )
            """
        )
        .scalar()
    )


def _create_backups() -> None:
    # Plain CREATE TABLE: a leftover snapshot from an earlier cycle must fail the
    # upgrade loudly rather than be reused.
    op.execute(
        """
        CREATE TABLE public.core_265_anchor_backup (
            id UUID PRIMARY KEY,
            survivor_id UUID NOT NULL,
            butler_name TEXT NOT NULL,
            title TEXT,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            message_count INTEGER NOT NULL,
            routed_butler TEXT,
            source_channel TEXT NOT NULL,
            source_thread_identity TEXT NOT NULL,
            provider_session_id TEXT,
            provider_runtime_type TEXT,
            provider_session_updated_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE public.core_265_message_backup (
            message_id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE public.core_265_turn_backup (
            message_id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
        )
        """
    )
    # Snapshots belong to the table they protect; no runtime role is granted.
    tables = ", ".join(f"'{table}'" for table in _BACKUP_TABLES)
    op.execute(
        f"""
        DO $ownership$
        DECLARE
            target_owner TEXT;
            backup_table TEXT;
        BEGIN
            SELECT role.rolname INTO target_owner
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_roles AS role ON role.oid = relation.relowner
            WHERE relation.oid = 'public.dashboard_conversations'::regclass;

            FOREACH backup_table IN ARRAY ARRAY[{tables}] LOOP
                EXECUTE format('ALTER TABLE public.%I OWNER TO %I', backup_table, target_owner);
            END LOOP;
        END;
        $ownership$
        """
    )


def _collapse_anchors() -> None:
    op.execute(
        f"""
        INSERT INTO public.core_265_anchor_backup (
            id, survivor_id, butler_name, title, status, created_at, updated_at,
            message_count, routed_butler, source_channel, source_thread_identity,
            provider_session_id, provider_runtime_type, provider_session_updated_at
        )
        SELECT c.id,
               first_value(c.id) OVER (
                   PARTITION BY c.butler_name, c.source_channel, {_CONVERSATION_KEY_SQL}
                   ORDER BY (c.provider_session_id IS NOT NULL) DESC,
                            c.provider_session_updated_at DESC NULLS LAST,
                            c.updated_at DESC, c.created_at DESC, c.id DESC
               ),
               c.butler_name, c.title, c.status, c.created_at, c.updated_at,
               c.message_count, c.routed_butler, c.source_channel,
               c.source_thread_identity, c.provider_session_id,
               c.provider_runtime_type, c.provider_session_updated_at
        FROM public.dashboard_conversations AS c
        WHERE {_COLLAPSE_PREDICATE}
        """
    )
    op.execute(
        """
        INSERT INTO public.core_265_message_backup (message_id, conversation_id)
        SELECT m.id, m.conversation_id
        FROM public.dashboard_messages AS m
        JOIN public.core_265_anchor_backup AS b ON b.id = m.conversation_id
        WHERE b.id <> b.survivor_id
        """
    )
    op.execute(
        """
        INSERT INTO public.core_265_turn_backup (message_id, conversation_id)
        SELECT t.message_id, t.conversation_id
        FROM public.dashboard_conversation_turns AS t
        JOIN public.core_265_anchor_backup AS b ON b.id = t.conversation_id
        WHERE b.id <> b.survivor_id
        """
    )
    # Re-link before deleting: both relations cascade on anchor deletion.
    op.execute(
        """
        UPDATE public.dashboard_messages AS m
        SET conversation_id = b.survivor_id
        FROM public.core_265_anchor_backup AS b
        WHERE m.conversation_id = b.id AND b.id <> b.survivor_id
        """
    )
    op.execute(
        """
        UPDATE public.dashboard_conversation_turns AS t
        SET conversation_id = b.survivor_id
        FROM public.core_265_anchor_backup AS b
        WHERE t.conversation_id = b.id AND b.id <> b.survivor_id
        """
    )
    op.execute(
        """
        DELETE FROM public.dashboard_conversations AS c
        USING public.core_265_anchor_backup AS b
        WHERE c.id = b.id AND b.id <> b.survivor_id
        """
    )
    op.execute(
        f"""
        WITH survivor AS (
            SELECT b.survivor_id, count(*) AS members
            FROM public.core_265_anchor_backup AS b
            GROUP BY b.survivor_id
        )
        UPDATE public.dashboard_conversations AS c
        SET source_thread_identity = {_CONVERSATION_KEY_SQL},
            external_conversation_id = {_CONVERSATION_KEY_SQL},
            message_count = CASE
                WHEN survivor.members > 1 THEN (
                    SELECT count(*) FROM public.dashboard_messages AS m
                    WHERE m.conversation_id = c.id
                )
                ELSE c.message_count
            END
        FROM survivor
        WHERE c.id = survivor.survivor_id
        """
    )


def _copy_remaining_identities() -> None:
    op.execute(
        """
        UPDATE public.dashboard_conversations
        SET external_conversation_id = source_thread_identity
        WHERE external_conversation_id IS NULL
          AND source_thread_identity IS NOT NULL
        """
    )


def upgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_SHARED_DDL_LOCK}', 0))")
    if _column_installed():
        return

    op.execute(
        "ALTER TABLE public.dashboard_conversations ADD COLUMN external_conversation_id TEXT NULL"
    )
    _create_backups()
    _collapse_anchors()
    _copy_remaining_identities()
    op.execute(
        """
        CREATE UNIQUE INDEX uq_dashboard_conversations_external_conversation
            ON public.dashboard_conversations (
                butler_name, source_channel, external_conversation_id
            )
            WHERE external_conversation_id IS NOT NULL
        """
    )


def _verify_restore() -> None:
    # ON CONFLICT DO NOTHING and the EXISTS guards above skip what a
    # post-upgrade row blocks. Anything skipped fails the downgrade, which rolls
    # back with the snapshots intact, instead of being dropped with them.
    # Messages and turns deleted after the upgrade are legitimately absent.
    op.execute(
        """
        DO $verify$
        DECLARE
            missing_anchors BIGINT;
            moved_messages BIGINT;
            moved_turns BIGINT;
        BEGIN
            SELECT count(*) INTO missing_anchors
            FROM public.core_265_anchor_backup AS b
            WHERE NOT EXISTS (
                SELECT 1 FROM public.dashboard_conversations AS c
                WHERE c.id = b.id
                  AND c.source_thread_identity = b.source_thread_identity
            )
              AND (b.id <> b.survivor_id OR EXISTS (
                  SELECT 1 FROM public.dashboard_conversations AS c WHERE c.id = b.id
              ));
            SELECT count(*) INTO moved_messages
            FROM public.core_265_message_backup AS b
            JOIN public.dashboard_messages AS m ON m.id = b.message_id
            WHERE m.conversation_id <> b.conversation_id;
            SELECT count(*) INTO moved_turns
            FROM public.core_265_turn_backup AS b
            JOIN public.dashboard_conversation_turns AS t ON t.message_id = b.message_id
            WHERE t.conversation_id <> b.conversation_id;

            IF missing_anchors + moved_messages + moved_turns > 0 THEN
                RAISE EXCEPTION
                    'core_265 downgrade cannot restore % anchor(s), % message link(s), '
                    '% turn link(s) from public.core_265_* snapshots',
                    missing_anchors, moved_messages, moved_turns
                    USING HINT = 'A post-upgrade public.dashboard_conversations row holds '
                        'an original identity. Resolve it and retry; nothing was changed.';
            END IF;
        END;
        $verify$
        """
    )


def downgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_SHARED_DDL_LOCK}', 0))")
    if _installed_schema_count() > 1 or not _column_installed():
        return

    op.execute("DROP INDEX IF EXISTS public.uq_dashboard_conversations_external_conversation")
    # Survivors first: a restored row may reclaim the identity a survivor holds.
    op.execute(
        """
        UPDATE public.dashboard_conversations AS c
        SET source_thread_identity = b.source_thread_identity
        FROM public.core_265_anchor_backup AS b
        WHERE c.id = b.id AND b.id = b.survivor_id
        """
    )
    op.execute(
        """
        INSERT INTO public.dashboard_conversations (
            id, butler_name, title, status, created_at, updated_at, message_count,
            routed_butler, source_channel, source_thread_identity,
            provider_session_id, provider_runtime_type, provider_session_updated_at
        )
        SELECT id, butler_name, title, status, created_at, updated_at, message_count,
               routed_butler, source_channel, source_thread_identity,
               provider_session_id, provider_runtime_type, provider_session_updated_at
        FROM public.core_265_anchor_backup
        WHERE id <> survivor_id
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE public.dashboard_messages AS m
        SET conversation_id = b.conversation_id
        FROM public.core_265_message_backup AS b
        WHERE m.id = b.message_id
          AND EXISTS (
              SELECT 1 FROM public.dashboard_conversations AS c WHERE c.id = b.conversation_id
          )
        """
    )
    op.execute(
        """
        UPDATE public.dashboard_conversation_turns AS t
        SET conversation_id = b.conversation_id
        FROM public.core_265_turn_backup AS b
        WHERE t.message_id = b.message_id
          AND EXISTS (
              SELECT 1 FROM public.dashboard_conversations AS c WHERE c.id = b.conversation_id
          )
        """
    )
    op.execute(
        """
        WITH survivor AS (
            SELECT survivor_id FROM public.core_265_anchor_backup
            GROUP BY survivor_id HAVING count(*) > 1
        )
        UPDATE public.dashboard_conversations AS c
        SET message_count = (
            SELECT count(*) FROM public.dashboard_messages AS m
            WHERE m.conversation_id = c.id
        )
        FROM survivor
        WHERE c.id = survivor.survivor_id
        """
    )
    _verify_restore()
    op.execute("ALTER TABLE public.dashboard_conversations DROP COLUMN external_conversation_id")
    for table in reversed(_BACKUP_TABLES):
        op.execute(f"DROP TABLE public.{table}")

"""dashboard_messages: tsvector + trigram full-text search index

Revision ID: core_221
Revises: core_220
Create Date: 2026-09-05 00:00:00.000000

NOTE: renumbered twice while landing due to migration-slot races with
concurrent sibling branches: ``core_216`` (original) -> ``core_218`` (after
bu-8cdl1.7/RFC 0032 claimed ``core_216`` and moved to ``core_217``) ->
``core_221`` (after ``core_217``..``core_220`` all landed on ``main`` ahead of
this PR). Re-verify against ``alembic/versions/core/`` right before merge and
bump ``down_revision`` again if another revision lands first (alembic
revision chains must be linear).

bu-0ynlk.9 (conversation_recall core tool + message-level full-text search).
Any butler can now answer "what did I ask you last week about X" via the new
always-on ``conversation_recall`` tool, and the dashboard gains a
message-level search endpoint (``GET /api/conversations/messages/search``).
Both are backed by one index pair on ``public.dashboard_messages``:

  - ``search_vector`` — a ``GENERATED ALWAYS ... STORED`` tsvector column over
    ``content`` (English config), with a GIN index for ``@@`` ranked matches
    (``ts_rank`` / ``ts_headline``).
  - A trigram (``pg_trgm``) GIN index on ``content`` itself, so the existing
    ILIKE-based ``conversation_search`` (api/conversations.py) also becomes
    index-eligible without changing its query shape.

Grants
------
``conversation_recall`` is owner-scoped: it reads across every butler's rows
in this cross-butler ``public`` table, regardless of which butler's role
happens to be running the tool. ``core_006_dashboard.py`` granted
SELECT/INSERT/UPDATE/DELETE on ``dashboard_conversations``/``dashboard_messages``
to the 9 butler roles that existed at the time; three ordinary butler roles
added to the roster since then (concierge, lifestyle, qa) were never granted
access to these two tables. Any butler running under one of those roles would
get a permission-denied the first time it called the new tool. This migration
closes that gap with the same guarded grant pattern core_006 uses, so the new
roles gain full parity with the original nine. ``downgrade()`` only revokes
the roles it added — the original nine keep whatever core_006 already granted.

``butler_chronicler_rw`` (also added after core_006) is deliberately excluded:
RFC 0014 §D1 restricts it to an explicit evidence-surface allowlist declared
in ``src/butlers/chronicler/contracts.py`` and forbids blanket table grants —
see ``scripts/init-db.sql``'s chronicler grant block. ``dashboard_messages``
is not on that allowlist, so chronicler calling ``conversation_recall`` gets
the same permission-denied it would already get from the pre-existing
``conversation_reply`` tool (also always-on, also ungranted for chronicler) —
a pre-existing gap this migration does not widen or attempt to close.

Idempotency / reversibility
---------------------------
Every DDL statement is guarded (``IF NOT EXISTS`` / guarded ``DO`` blocks), so
a re-run is a no-op. ``downgrade()`` drops the indexes and column and revokes
the four added-role grants.

Why plain ``CREATE INDEX``, not ``CONCURRENTLY``
--------------------------------------------------
``public.dashboard_messages`` is cross-butler: every butler schema's "core"
chain applies this migration against the *same physical relation*, and two
schemas' chains can run concurrently (see
``tests/migrations/test_runtime_attention_outbox_migration.py::
test_core_chain_serializes_global_runtime_attention_downgrade_and_reapply_across_processes``).
An earlier attempt at this migration paired a blocking, table-rewriting
``ALTER TABLE ... ADD COLUMN ... GENERATED ALWAYS ... STORED`` (takes
``AccessExclusiveLock`` for the rewrite) with ``CREATE INDEX CONCURRENTLY``
on that same table inside ``op.get_context().autocommit_block()``. Under the
two-schema concurrent-upgrade race, one session's CIC can still be in its
internal "wait for pre-existing snapshots to finish" phase when the other
session reaches an unrelated, already-merged, ordinary transactional
``ALTER TABLE`` on the same relation from a *different* core migration
earlier in the chain (e.g. ``core_213_dashboard_messages_sources.py``'s
``ADD COLUMN sources``) — that session blocks waiting for
``AccessExclusiveLock``, while the CIC session is in turn waiting on that
now-blocked session's still-open transaction: a textbook Postgres CIC
deadlock (confirmed via repeated local runs of the test above). A
session-scoped advisory lock only protects statements that opt into taking
it, and retrofitting one onto every already-applied migration that touches
this shared table is neither practical nor safe to do to already-merged,
already-applied revisions.

The fix is simpler: use plain, ordinary ``CREATE INDEX IF NOT EXISTS`` (no
``CONCURRENTLY``) here, matching every other migration in this codebase that
touches ``public.dashboard_messages``/``dashboard_conversations``
(``core_006_dashboard.py``, ``core_210_expected_signals.py``,
``core_213_dashboard_messages_sources.py``) — none of which use
``CONCURRENTLY`` on a cross-butler ``public`` table. Plain DDL still takes
``AccessExclusiveLock``, but two sessions each requesting it just serialize
in FIFO order (the second blocks until the first's transaction commits) —
Postgres never has to wait on old snapshots the way CIC does, so there is no
opportunity for the circular wait. ``dashboard_messages`` is not yet at a
size where a brief index-build lock is disruptive; the only real cost is a
short write-path pause during migration, which is exactly what every other
migration against this table already accepts.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_221"
down_revision = "core_220"
branch_labels = None
depends_on = None

_TABLE_FQNS = ("public.dashboard_conversations", "public.dashboard_messages")

# Ordinary butler roles added to the roster after core_006_dashboard.py's
# original 9-role grant list but never backfilled onto these two cross-butler
# tables. butler_chronicler_rw is deliberately excluded — see module docstring.
_NEW_ROLES = (
    "butler_concierge_rw",
    "butler_lifestyle_rw",
    "butler_qa_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
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


def _revoke_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """REVOKE privilege ON table FROM role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'REVOKE {privilege} ON TABLE {table_fqn} FROM {_quote_ident(role)}';
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
    # Shared, database-wide extension. Normally pre-installed by DB
    # provisioning; IF NOT EXISTS makes this a no-op when already present.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Generated tsvector column: recomputed automatically on every INSERT/UPDATE
    # of `content`, so search_vector is never stale. NULL content coalesces to
    # '' rather than producing a NULL tsvector.
    op.execute(
        """
        ALTER TABLE public.dashboard_messages
        ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED
        """
    )

    # Plain (non-CONCURRENTLY) index creation — see module docstring "Why
    # plain CREATE INDEX, not CONCURRENTLY" for why CIC on this cross-butler
    # table deadlocks under concurrent per-schema migration upgrades.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dashboard_messages_search_vector
        ON public.dashboard_messages
        USING gin (search_vector)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dashboard_messages_content_trgm
        ON public.dashboard_messages
        USING gin (content gin_trgm_ops)
        """
    )

    for table_fqn in _TABLE_FQNS:
        for role in _NEW_ROLES:
            _grant_if_table_exists(table_fqn, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    for table_fqn in _TABLE_FQNS:
        for role in _NEW_ROLES:
            _revoke_if_table_exists(table_fqn, _TABLE_PRIVILEGES, role)

    op.execute("DROP INDEX IF EXISTS idx_dashboard_messages_content_trgm")
    op.execute("DROP INDEX IF EXISTS idx_dashboard_messages_search_vector")
    op.execute("ALTER TABLE public.dashboard_messages DROP COLUMN IF EXISTS search_vector")

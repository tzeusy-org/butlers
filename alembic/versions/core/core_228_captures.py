"""captures: a durable ledger for capture(), the second-brain intake verb

Revision ID: core_228
Revises: core_227
Create Date: 2026-09-09 00:00:00.000000

Creates ``public.captures`` -- the durable receipt row written synchronously
by the ``capture()`` core tool (bu-2jtfw.9, docs/redesigns/2026-09-05-jarvis-
pursuit.md, ranked move #9). A capture always lands as ``held`` first; a
later routing step either promotes it to ``routed`` (citing the target row it
wrote) or ``refused`` (an ownership boundary, naming the owning butler and
tool). If the routing step never runs -- a dead session, a crash -- the row
simply stays ``held`` and is discoverable via ``GET /api/captures?state=held``.
The row is never deleted; ``un-routing`` resets ``receipt_state`` instead.

Grant model: every butler role gets SELECT, INSERT, UPDATE (no DELETE --
captures are a ledger, not a scratch table; the "rollback: never deleted"
invariant from the design's behavior matrix is enforced here at the grant
level, matching ``public.memory_catalog``'s centrally-preserved pattern).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_228"
down_revision = "core_227"
branch_labels = None
depends_on = None

# Mirrors the current _ALL_BUTLER_ROLES list from core_215_entity_graph_edges.py
# (the most recently landed canonical set at authoring time).
_ALL_BUTLER_ROLES = (
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
)

_TABLE_FQN = "public.captures"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_if_role_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    safe_table_fqn = table_fqn.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{safe_table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
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


def _grant_schema_usage_if_exists(schema: str, role: str) -> None:
    """GRANT USAGE ON SCHEMA only when schema and role exist."""
    safe_schema = schema.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = '{safe_schema}'
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
            THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {_quote_ident(schema)} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.captures (
            capture_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            channel         TEXT NOT NULL,
            content         TEXT NOT NULL,

            -- Idempotency key: a transport retry with the same mutation_id
            -- must yield the same capture row, never a duplicate.
            mutation_id     UUID UNIQUE,

            receipt_state   TEXT NOT NULL DEFAULT 'held'
                CHECK (receipt_state IN ('held', 'routed', 'refused')),

            -- Populated only once receipt_state = 'routed'.
            routed_kind     TEXT,
            target_schema   TEXT,
            target_table    TEXT,
            target_row_id   UUID,

            -- Populated when routing hits a failure (receipt_state stays
            -- 'held') or an ownership boundary (receipt_state = 'refused').
            refusal_reason  TEXT,

            source_butler   TEXT NOT NULL,
            tenant_id       TEXT NOT NULL DEFAULT 'owner',

            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- A routed capture's receipt must cite a real target row.
            CONSTRAINT chk_captures_routed_has_target CHECK (
                receipt_state != 'routed'
                OR (target_schema IS NOT NULL
                    AND target_table IS NOT NULL
                    AND target_row_id IS NOT NULL)
            )
        )
    """)

    # Held-capture lane: GET /api/captures?state=held scans this.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_captures_held
        ON public.captures (created_at DESC)
        WHERE receipt_state = 'held'
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_captures_state_created
        ON public.captures (receipt_state, created_at DESC)
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_if_role_exists(_TABLE_FQN, _TABLE_PRIVILEGES, role)
        _grant_schema_usage_if_exists("public", role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_captures_state_created")
    op.execute("DROP INDEX IF EXISTS public.idx_captures_held")
    op.execute("DROP TABLE IF EXISTS public.captures CASCADE")

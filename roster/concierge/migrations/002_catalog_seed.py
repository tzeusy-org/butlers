"""seed_concierge_memory_catalog_entry

Revision ID: concierge_002
Revises: concierge_001
Create Date: 2026-09-04 00:00:01.000000

Seeds a single static routing entry into ``public.memory_catalog`` so that
``core.delegation_ledger.resolve_target_via_catalog`` (the hybrid semantic +
full-text search that decides "whose domain covers this question") surfaces
``concierge`` as the top hit for system-plane fleet questions such as "how
much did the fleet spend yesterday" or "what sessions failed today".

This is a curated routing directive, not a canonical memory item — there is
no owning ``facts``/``rules`` row behind it, so ``source_table`` uses the
sentinel value ``'catalog_seed'`` (not ``'facts'``/``'rules'``) to signal that
``memory_catalog_fetch`` (which only dereferences ``'facts'``/``'rules'``)
must never attempt to resolve it; only the routing search path
(``resolve_target_via_catalog`` / ``GET /api/memory/catalog/search``) reads
this row.

``embedding`` is intentionally left NULL: generating a real embedding
requires the memory module's embedding engine, which is not available inside
a plain SQL migration. The hybrid search's RRF fusion degrades gracefully —
this row simply contributes no semantic-leg score and is ranked purely on
the full-text leg (``search_vector``), which is populated here via
``to_tsvector('english', ...)``, matching the keyword-heavy phrasing of the
system-plane questions it exists to route.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "concierge_002"
down_revision = "concierge_001"
branch_labels = None
depends_on = None

_CATALOG_SOURCE_SCHEMA = "concierge"
_CATALOG_SOURCE_TABLE = "catalog_seed"
_CATALOG_SOURCE_ID = "00000000-0000-0000-0000-00000000c0c9"

_SUMMARY = (
    "Concierge answers system-plane questions about the butler fleet itself: "
    "fleet status, uptime, which butlers are running or offline, session "
    "counts, session failures and errors, spend, cost, and token usage "
    "across the fleet today, yesterday, this week, or this month. Ask "
    "concierge how much the fleet spent, which sessions failed, what is "
    "currently running, or what a session cost."
)


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO public.memory_catalog (
            source_schema, source_table, source_id,
            source_butler, tenant_id,
            summary, embedding, search_vector, memory_type,
            title, sensitivity,
            updated_at
        )
        VALUES (
            '{_CATALOG_SOURCE_SCHEMA}', '{_CATALOG_SOURCE_TABLE}',
            '{_CATALOG_SOURCE_ID}'::uuid,
            'concierge', 'owner',
            '{_SUMMARY}', NULL,
            to_tsvector('english', '{_SUMMARY}'), 'rule',
            'Concierge: fleet status, spend, and sessions', 'normal',
            now()
        )
        ON CONFLICT (source_schema, source_table, source_id)
        DO UPDATE SET
            summary       = EXCLUDED.summary,
            search_vector = EXCLUDED.search_vector,
            title         = EXCLUDED.title,
            sensitivity   = EXCLUDED.sensitivity,
            updated_at    = now()
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM public.memory_catalog
        WHERE source_schema = '{_CATALOG_SOURCE_SCHEMA}'
          AND source_table = '{_CATALOG_SOURCE_TABLE}'
          AND source_id = '{_CATALOG_SOURCE_ID}'::uuid
        """
    )

"""api_runtime_discretion_classification: flip discretion/classification tiers
onto the new 'api' runtime adapter.

Revision ID: core_157
Revises: core_156
Create Date: 2026-07-05 00:00:01.000000

Part of bu-qvnce.12 (2026-07-04 JARVIS pursuit, move 12), slices 1-2.

``model_catalog_defaults.toml`` only seeds ``public.model_catalog`` on a brand
new database bootstrap (core_004's ``upgrade()`` reads it once, at that
migration's run time — see its docstring/comment); it has no effect on an
already-migrated database. Adding two new catalog rows directly here is the
sanctioned mechanism for changing live routing behavior on existing
deployments (mirrors core_147's precedent of a data-affecting migration on
this same table).

Two new rows, both ``runtime_type='api'`` (``butlers.core.runtimes.api.ApiAdapter``,
added alongside this migration), reusing the existing ``claude-haiku-4-5-20251001``
model id (already priced in ``pricing.toml`` independent of runtime_type):

- ``api-haiku-cheap``     complexity_tier='cheap'     priority=30, enabled=true
  Outranks every existing 'cheap' entry (gpt-5.4-mini priority=25) so
  switchboard classification (bu-qvnce.12 slice 4 renames its
  trigger_source 'tick'->'classification') routes onto the direct-API adapter
  by default: no subprocess spawn, no MCP handshake, for a call that was
  already always single-turn/tool-free.
- ``api-haiku-specialty`` complexity_tier='specialty' priority=20, enabled=true
  Outranks the existing 'specialty' entry (discretion-qwen3.5-9b priority=10)
  so connector discretion screening (``DiscretionDispatcher``, already
  invoking with ``mcp_servers={}``/``max_turns=1``) gets the same win.

Both are additive rows at higher priority, not replacements: the previous
top-priority entries for these tiers stay ``enabled=true`` at their existing
priority, so ``next_same_tier_candidate`` same-tier failover (already wired
into ``core.spawner._run()``) falls back to them automatically if the new
adapter's invocation fails (missing/invalid API key, network error, etc.).
Discretion calls are not covered by that spawner-side failover (they bypass
the spawner), but ``DiscretionEvaluator.evaluate()`` already fails
open/closed on ANY dispatcher exception by sender weight, so a broken adapter
degrades discretion screening rather than hard-failing it.

Rollback (``downgrade()``) removes exactly these two rows by alias; it does
not touch ``last_verified_ok``/``priority`` on any pre-existing row.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "core_157"
down_revision = "core_156"
branch_labels = None
depends_on = None

_MODEL_ID = "claude-haiku-4-5-20251001"

_SEED_ROWS = (
    {
        "alias": "api-haiku-cheap",
        "runtime_type": "api",
        "model_id": _MODEL_ID,
        "complexity_tier": "cheap",
        "priority": 30,
    },
    {
        "alias": "api-haiku-specialty",
        "runtime_type": "api",
        "model_id": _MODEL_ID,
        "complexity_tier": "specialty",
        "priority": 20,
    },
)


def upgrade() -> None:
    bind = op.get_bind()
    seed_sql = sa.text(
        """
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, extra_args,
             complexity_tier, priority, enabled)
        VALUES
            (:alias, :runtime_type, :model_id, '[]'::jsonb,
             :complexity_tier, :priority, true)
        ON CONFLICT (alias) DO NOTHING
        """
    )
    for row in _SEED_ROWS:
        bind.execute(seed_sql, row)


def downgrade() -> None:
    bind = op.get_bind()
    aliases = [row["alias"] for row in _SEED_ROWS]
    bind.execute(
        sa.text("DELETE FROM public.model_catalog WHERE alias = ANY(:aliases)"),
        {"aliases": aliases},
    )

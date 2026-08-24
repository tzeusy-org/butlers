"""Give ``public.model_catalog`` a validated capability and context envelope.

Revision ID: core_204
Revises: core_203
Create Date: 2026-08-25 00:00:00.000000

bu-6jv4m.7: catalog eligibility (``enabled`` / ``last_verified_ok`` / breaker /
quota) has never proven that a model can do the job a dispatch needs. The
concrete failure this unblocks is already seeded: ``api-haiku-cheap`` sits at
priority 30 -- the top of the ``cheap`` tier -- while ``ApiAdapter.invoke``
raises ``RuntimeError`` for any non-empty ``mcp_servers``, which every butler
session except ``healing``/``qa`` builds. Ranking cannot fix that; the entry has
to be disqualified before it can win.

Three additive columns, no backfill:

- ``capabilities`` (JSONB, default ``{}``) -- per-entry overrides on the adapter
  capability baseline declared in code
  (``RuntimeAdapter.declared_capabilities``). Empty is the correct default: the
  adapter layer already answers ``tool_use`` and ``session_resume`` for every
  registered runtime type, so an empty envelope means "nothing model-specific to
  add", not "unknown". Keys are validated against
  ``butlers.core.model_capabilities.ModelFeature`` in Python; the CHECK here only
  pins the JSON shape, because the feature vocabulary lives with the adapters and
  a database constraint would have to be re-migrated every time it grows.
- ``max_context_tokens`` / ``max_output_tokens`` (INTEGER, nullable) -- the
  context envelope, which is genuinely per model and cannot come from the
  adapter. NULL means undeclared, and a dispatch that requires a context floor
  treats undeclared as unproven and excludes the entry (fail closed), so these
  stay NULL until an operator populates them rather than being guessed here.

Purely additive and idempotent; existing rows keep resolving exactly as before
(an empty envelope excludes nobody).
"""

from __future__ import annotations

from alembic import op

revision = "core_204"
down_revision = "core_203"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
ALTER TABLE public.model_catalog
    ADD COLUMN IF NOT EXISTS capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS max_context_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS max_output_tokens INTEGER;

ALTER TABLE public.model_catalog
    DROP CONSTRAINT IF EXISTS chk_model_catalog_capabilities_object;
ALTER TABLE public.model_catalog
    ADD CONSTRAINT chk_model_catalog_capabilities_object
    CHECK (jsonb_typeof(capabilities) = 'object');

ALTER TABLE public.model_catalog
    DROP CONSTRAINT IF EXISTS chk_model_catalog_max_context_tokens_positive;
ALTER TABLE public.model_catalog
    ADD CONSTRAINT chk_model_catalog_max_context_tokens_positive
    CHECK (max_context_tokens IS NULL OR max_context_tokens > 0);

ALTER TABLE public.model_catalog
    DROP CONSTRAINT IF EXISTS chk_model_catalog_max_output_tokens_positive;
ALTER TABLE public.model_catalog
    ADD CONSTRAINT chk_model_catalog_max_output_tokens_positive
    CHECK (max_output_tokens IS NULL OR max_output_tokens > 0);
"""


DOWNGRADE_SQL = """
ALTER TABLE public.model_catalog
    DROP CONSTRAINT IF EXISTS chk_model_catalog_max_output_tokens_positive;
ALTER TABLE public.model_catalog
    DROP CONSTRAINT IF EXISTS chk_model_catalog_max_context_tokens_positive;
ALTER TABLE public.model_catalog
    DROP CONSTRAINT IF EXISTS chk_model_catalog_capabilities_object;
ALTER TABLE public.model_catalog
    DROP COLUMN IF EXISTS max_output_tokens,
    DROP COLUMN IF EXISTS max_context_tokens,
    DROP COLUMN IF EXISTS capabilities;
"""


def upgrade() -> None:
    """Add the capability envelope columns and their shape constraints."""
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    """Drop the envelope columns; resolution reverts to eligibility-only fit."""
    op.execute(DOWNGRADE_SQL)

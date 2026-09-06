"""token_usage_ledger_composition_and_resume: per-layer prompt digest + resume outcome.

Revision ID: core_223
Revises: core_222
Create Date: 2026-09-06 00:00:00.000000

Slice 4 of bu-2jtfw.4 (cache-honest spend epic, PR #4039 covered slices 1-3),
deferred as bu-hz0g0. No query on main could answer "what did each prompt
layer cost" -- ``public.token_usage_ledger`` records the merged input/output/
cache token totals for a spawn, but nothing about the five layers
``spawner_context._compose_system_prompt`` merges into that prompt (base
CLAUDE.md, timezone instruction, situational context preamble, owner routing
instructions, memory context). Nor does it record whether a conversational
turn resumed a provider-native session or dispatched cold.

Columns (all additive, nullable -- existing rows keep working; mirrors
core_156's ``purpose`` column, which took the same nullable-no-default shape
for the same reason: an evolving, code-owned vocabulary rather than a DB
CHECK constraint):

  public.token_usage_ledger.base_prompt_tokens           INTEGER NULL
  public.token_usage_ledger.timezone_instruction_tokens  INTEGER NULL
  public.token_usage_ledger.context_preamble_tokens      INTEGER NULL
  public.token_usage_ledger.routing_instructions_tokens  INTEGER NULL
  public.token_usage_ledger.memory_context_tokens        INTEGER NULL
  public.token_usage_ledger.resume_outcome               TEXT NULL

The five *_tokens columns are populated only by ``Spawner.trigger()``'s
composed-prompt spawn path (``spawner_context.compose_prompt_digest()``);
callers with no prompt composition of their own -- the discretion dispatcher
lane, most notably -- pass none of these kwargs to ``record_token_usage()``
and the columns stay honestly NULL rather than a fabricated zero.
``resume_outcome`` is set only for conversational (``trigger_source ==
"route"``) turns on a resume-capable adapter; every other row's value is
NULL, not a default "not_applicable" string.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_223"
down_revision = "core_222"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.token_usage_ledger
            ADD COLUMN IF NOT EXISTS base_prompt_tokens INTEGER NULL,
            ADD COLUMN IF NOT EXISTS timezone_instruction_tokens INTEGER NULL,
            ADD COLUMN IF NOT EXISTS context_preamble_tokens INTEGER NULL,
            ADD COLUMN IF NOT EXISTS routing_instructions_tokens INTEGER NULL,
            ADD COLUMN IF NOT EXISTS memory_context_tokens INTEGER NULL,
            ADD COLUMN IF NOT EXISTS resume_outcome TEXT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.token_usage_ledger
            DROP COLUMN IF EXISTS base_prompt_tokens,
            DROP COLUMN IF EXISTS timezone_instruction_tokens,
            DROP COLUMN IF EXISTS context_preamble_tokens,
            DROP COLUMN IF EXISTS routing_instructions_tokens,
            DROP COLUMN IF EXISTS memory_context_tokens,
            DROP COLUMN IF EXISTS resume_outcome
        """
    )

"""Declare probed vision support on ``public.model_catalog`` entries.

Revision ID: core_248
Revises: core_247
Create Date: 2026-09-24 00:00:00.000000

bu-2jtfw.7 made image-bearing dispatches require ``ModelFeature.VISION`` and left
every row undeclared, so every image dispatch failed closed with
``no_fitting_candidate ... exclusions=capability_unknown:vision`` (e.g. a finance
receipt photo). Nothing ever wrote ``capabilities.vision``.

The declarations below are exact-path evidence per the model-catalog spec
("Vision capability requires exact-path evidence"): on 2026-09-24 each
``(runtime_type, model_id)`` was driven end-to-end -- CLI -> streamable-HTTP MCP
tool returning an image content block -> model -- and asked to read a random
six-letter nonce rendered only in the image bytes (never in the prompt or tool
metadata). Runtime versions: codex-cli 0.156.1 (dev ChatGPT account),
opencode 1.17.7 (opencode-go subscription).

- ``true``: returned the nonce verbatim.
- ``false``: ``opencode-go/qwen3.7-max`` called the tool and answered
  ``NO_IMAGE`` (text-only; OpenCode strips the image before inference).

Rows are matched on ``(runtime_type, model_id)`` so every alias of a probed model
(e.g. ``gpt-6-sol-high`` and ``healing-codex``) gets the declaration. The update
merges into the existing envelope and is idempotent, which matters because the
core chain runs once per butler schema against the shared table. Models not
listed keep vision undeclared (unknown) -- a vendor claim alone is not evidence.

Downgrade is a no-op: this is recorded evidence, not schema. The ``vision`` key is
valid on every revision from core_204 on, and removing it would re-break image
dispatch without making the claim any less true.
"""

from __future__ import annotations

import json

from alembic import op

revision = "core_248"
down_revision = "core_247"
branch_labels = None
depends_on = None


# (runtime_type, model_id) -> probed vision support.
VISION_EVIDENCE: dict[tuple[str, str], bool] = {
    ("codex", "gpt-6-sol"): True,
    ("codex", "gpt-6-luna"): True,
    ("opencode", "opencode-go/minimax-m3"): True,
    ("opencode", "opencode-go/glm-5.3-flash"): True,
    ("opencode", "opencode-go/mimo-v2.6-pro"): True,
    ("opencode", "opencode-go/mimo-v2.6-flash"): True,
    ("opencode", "opencode-go/qwen3.7-max"): False,
}


def upgrade() -> None:
    """Merge the probed ``vision`` declaration into each matching row's envelope."""
    for (runtime_type, model_id), supported in VISION_EVIDENCE.items():
        envelope = json.dumps({"vision": supported})
        op.execute(
            "UPDATE public.model_catalog "
            f"SET capabilities = capabilities || '{envelope}'::jsonb, updated_at = now() "
            f"WHERE runtime_type = '{runtime_type}' AND model_id = '{model_id}' "
            f"AND capabilities -> 'vision' IS DISTINCT FROM '{str(supported).lower()}'::jsonb"
        )


def downgrade() -> None:
    """No-op: vision evidence is data, not schema (see module docstring)."""

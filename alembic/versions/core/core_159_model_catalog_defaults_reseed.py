"""model_catalog_defaults_reseed: fix dead toml bootstrap seeding (tier vocab).

Revision ID: core_159
Revises: core_157
Create Date: 2026-07-05 00:00:02.000000

NOTE (coordination, bu-vq97l): named/numbered core_159 instead of the next
free slot (core_158) because a parallel in-flight lane (bu-qvnce.8, attention
ledger) claimed core_158 on its own branch first. ``down_revision`` is left
as ``core_157`` for now since merge order between the two lanes is not yet
known; whichever of core_158/core_159 merges second must rebase and repoint
its ``down_revision`` onto the other before merging, per the coordinator.

bu-vq97l (discovered-from bu-qvnce.12): core_004's ``_load_seed_entries()``
filters ``model_catalog_defaults.toml`` entries against
``_COMPLEXITY_TIERS = ("trivial", "medium", "high", "extra_high",
"discretion", "self_healing")`` — the LEGACY pre-core_093 tier vocabulary.
The toml's entries all carry the CANONICAL post-core_093 vocabulary
(``cheap``, ``workhorse``, ``reasoning``, ``specialty``, ``local``,
``legacy``), so none of them ever matched and ``_load_seed_entries()``
returned ``[]``. On a genuinely fresh install today, ``public.model_catalog``
only gets rows from later *data* migrations (core_147 changes a DEFAULT, no
inserts; core_157 inserts exactly the two ``api-haiku-*`` rows) — every other
tier (``workhorse``, ``reasoning``, ``local``, ``legacy``) ends up with ZERO
catalog entries, so ``resolve_model()`` has no candidates for them at all.

core_004 already ran (successfully, against the vocabulary current at the
time it was authored) on every already-migrated database, so its own code is
left untouched here — this migration does not change core_004's applied
behavior, it only adds a corrective idempotent re-seed on top, matching the
precedent set by core_147 (DEFAULT fix) and core_157 (direct data insert):
"a real fix needs a new migration re-seeding from the current toml with the
corrected vocabulary filter, run after the tier-rename chain."

Re-reads ``model_catalog_defaults.toml`` with a canonical-vocabulary filter
and inserts any alias not already present (``ON CONFLICT (alias) DO
NOTHING``):

- Fresh installs: core_004 seeds nothing (bug reproduced unchanged); this
  migration then inserts every toml entry, giving every tier real candidates.
- Existing installs: aliases seeded by a prior (working, pre-vocab-drift)
  core_004 run already exist, so those rows are left untouched; only aliases
  genuinely missing (e.g. models added to the toml after that install's
  bootstrap) are backfilled. core_157's two ``api-haiku-*`` rows are also
  present in the toml today and are skipped here via the same ON CONFLICT.

Downgrade is a best-effort delete of exactly the aliases this migration's
upgrade() would insert, keyed off the toml at downgrade time — mirroring
core_157's alias-scoped downgrade. It intentionally does not attempt to
restore rows a previous core_004 run may have already deleted/modified.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "core_159"
down_revision = "core_157"
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)

# Canonical six post-core_093 tiers. Duplicated locally (not imported from
# core_093) to keep each migration file self-contained and immune to future
# edits of sibling revision modules — same pattern as core_147/core_093's own
# locally-duplicated tier tuples.
_CANONICAL_TIERS = ("reasoning", "workhorse", "cheap", "specialty", "local", "legacy")


def _load_seed_entries() -> list[dict]:
    """Load model_catalog_defaults.toml entries whose tier is canonical-vocab."""
    import tomllib  # noqa: PLC0415

    defaults_path = Path(__file__).resolve().parents[3] / "model_catalog_defaults.toml"
    if not defaults_path.exists():
        return []
    with open(defaults_path, "rb") as f:
        data = tomllib.load(f)
    return [
        m for m in data.get("models", []) if m.get("complexity_tier") in _CANONICAL_TIERS
    ]


def upgrade() -> None:
    seed_entries = _load_seed_entries()
    if not seed_entries:
        log.warning(
            "core_159: model_catalog_defaults.toml produced no seed entries; "
            "public.model_catalog will not be backfilled."
        )
        return

    seed_sql = sa.text(
        "INSERT INTO public.model_catalog"
        " (alias, runtime_type, model_id, extra_args,"
        "  complexity_tier, priority, enabled)"
        " VALUES"
        " (:alias, :runtime_type, :model_id,"
        " CAST(:extra_args AS jsonb), :complexity_tier, :priority, :enabled)"
        " ON CONFLICT (alias) DO NOTHING"
    )
    seed_params = [
        {
            "alias": entry["alias"],
            "runtime_type": entry["runtime_type"],
            "model_id": entry["model_id"],
            "extra_args": json.dumps(entry.get("extra_args", [])),
            "complexity_tier": entry["complexity_tier"],
            "priority": entry.get("priority", 0),
            "enabled": entry.get("enabled", True),
        }
        for entry in seed_entries
    ]
    op.get_bind().execute(seed_sql, seed_params)


def downgrade() -> None:
    seed_entries = _load_seed_entries()
    if not seed_entries:
        return
    aliases = [entry["alias"] for entry in seed_entries]
    op.get_bind().execute(
        sa.text("DELETE FROM public.model_catalog WHERE alias = ANY(:aliases)"),
        {"aliases": aliases},
    )

"""Static regression coverage for the butler reachability condition ledger (bu-6jv4m.3).

The whole acknowledge-until-recurrence contract for an unreachable butler rests
on one structural fact: at most one condition row per butler may be OPEN, and
resolved rows must NOT constrain new ones.  That is a PARTIAL unique index, and
the router's atomic open-or-extend upsert infers on exactly its predicate.  A
plain unique index here would silently convert "this butler went down again" into
a constraint violation, and dropping the predicate would let two concurrent polls
open two competing episodes with two different onsets -- either way the ack
watermark stops meaning anything.  So the shape is asserted, not assumed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_200_butler_reachability_conditions.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "core_200_butler_reachability_conditions", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed_sql(function_name: str) -> str:
    module = _load_migration()
    op = MagicMock()
    with patch.object(module, "op", op):
        getattr(module, function_name)()
    return "\n".join(str(call.args[0]) for call in op.execute.call_args_list)


def test_revision_chains_onto_the_current_core_head() -> None:
    module = _load_migration()

    assert module.revision == "core_200"
    assert module.down_revision == "core_199"
    assert module.branch_labels is None
    assert module.depends_on is None


def test_upgrade_separates_the_onset_clock_from_the_observation_clock() -> None:
    sql = _executed_sql("upgrade")

    assert "CREATE TABLE IF NOT EXISTS public.butler_reachability_conditions" in sql
    # started_at is the episode onset (the ack epoch); last_seen_at is the probe
    # clock. Collapsing them back into one column is the original bug.
    assert "started_at   TIMESTAMPTZ NOT NULL DEFAULT now()" in sql
    assert "last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()" in sql
    assert "resolved_at  TIMESTAMPTZ NULL" in sql
    assert "observations INTEGER NOT NULL DEFAULT 1" in sql


def test_open_episode_uniqueness_is_partial_so_recurrence_is_possible() -> None:
    sql = _executed_sql("upgrade")

    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_butler_reachability_conditions_open" in sql
    # The predicate is load-bearing twice over: it is what the router's
    # ON CONFLICT (butler) WHERE resolved_at IS NULL infers on, and it is what
    # lets a butler accumulate more than one (resolved) outage.
    open_index = sql.split("ux_butler_reachability_conditions_open", 1)[1]
    assert "ON public.butler_reachability_conditions (butler)" in open_index
    assert "WHERE resolved_at IS NULL" in open_index.split("CREATE INDEX", 1)[0]

    assert "ix_butler_reachability_conditions_history" in sql
    assert "(butler, started_at DESC)" in sql


def test_downgrade_drops_indexes_before_the_table() -> None:
    sql = _executed_sql("downgrade")

    assert "DROP INDEX IF EXISTS ix_butler_reachability_conditions_history" in sql
    assert "DROP INDEX IF EXISTS ux_butler_reachability_conditions_open" in sql
    assert "DROP TABLE IF EXISTS public.butler_reachability_conditions" in sql
    assert sql.index("DROP INDEX") < sql.index("DROP TABLE")

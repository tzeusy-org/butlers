"""core_334: the timed insight lane and shared claim columns, and the guarded downgrade."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from butlers.testing.migration import load_migration

_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/core/core_334_insight_timed_lane.py"


def _run(fn: str, installed: int) -> list[str]:
    module = load_migration(_PATH, "core_334")
    statements: list[str] = []
    op = MagicMock()
    op.execute.side_effect = statements.append
    bind = op.get_bind.return_value

    def answer(sql: str, *args: object) -> MagicMock:
        result = MagicMock()
        if "pg_class version_table" in sql:
            result.scalars.return_value = iter([f"s{i}" for i in range(installed)])
        else:
            result.scalar.return_value = True
        return result

    bind.exec_driver_sql.side_effect = answer
    bind.dialect.identifier_preparer.quote.side_effect = lambda name: name
    with patch.object(module, "op", op):
        getattr(module, fn)()
    return statements


def test_core_334_extends_the_current_head() -> None:
    module = load_migration(_PATH, "core_334")
    assert (module.revision, module.down_revision) == ("core_334", "core_331")


def test_upgrade_adds_the_lane_window_and_claim_without_grants() -> None:
    sql = "\n".join(_run("upgrade", 1))
    for column in ("lane", "deliver_after", "claim_id", "claimed_at", "attempt_started_at"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql
    assert "CHECK (lane IN ('digest', 'timed'))" in sql
    assert "(lane = 'timed') = (deliver_after IS NOT NULL)" in sql
    assert "deliver_after < expires_at" in sql
    assert "ON public.insight_candidates (dedup_key) WHERE lane = 'timed'" in sql
    assert "GRANT" not in sql and "POLICY" not in sql


def test_downgrade_refuses_while_timed_candidates_exist_then_drops() -> None:
    sql = _run("downgrade", 1)
    refusal = next(s for s in sql if "RAISE EXCEPTION" in s)
    assert "WHERE lane = 'timed'" in refusal
    assert "DROP COLUMN IF EXISTS lane" in sql[-1]


def test_only_the_last_schema_drops_the_shared_columns() -> None:
    assert not any("DROP COLUMN" in s for s in _run("downgrade", 2))
    assert any("DROP COLUMN" in s for s in _run("downgrade", 1))

"""core_309 refuses a protected deep downgrade before its autocommit index drop.

``DROP INDEX CONCURRENTLY`` commits, so a deep downgrade that core_198 later
refuses would otherwise leave the index dropped and the stamp moved. The real
round trip is pinned by
``test_core_232_deep_downgrade_preflight_preserves_head_and_round_trips``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.testing.migration import load_migration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_309_owntracks_points_arrival_cursor_index.py"
)


@pytest.mark.unit
def test_downgrade_preflights_before_the_autocommit_index_drop() -> None:
    migration = load_migration(_MIGRATION_PATH)
    calls: list[str] = []
    operation = MagicMock()
    operation.get_context.return_value.autocommit_block.side_effect = lambda: (
        calls.append("autocommit") or MagicMock()
    )
    operation.execute.side_effect = lambda _sql: calls.append("execute")

    with (
        patch.object(migration, "op", operation),
        patch.object(
            migration,
            "preflight_runtime_attention_downgrade",
            side_effect=lambda _op, _context: calls.append("preflight"),
        ) as preflight,
    ):
        migration.downgrade()

    preflight.assert_called_once_with(operation, migration.context)
    assert calls == ["preflight", "autocommit", "execute"]


@pytest.mark.unit
def test_a_refused_preflight_drops_nothing() -> None:
    migration = load_migration(_MIGRATION_PATH)
    operation = MagicMock()

    with (
        patch.object(migration, "op", operation),
        patch.object(
            migration,
            "preflight_runtime_attention_downgrade",
            side_effect=RuntimeError("protected core_198 rollback preflight failed"),
        ),
        pytest.raises(RuntimeError, match="preflight failed"),
    ):
        migration.downgrade()

    operation.get_context.return_value.autocommit_block.assert_not_called()
    operation.execute.assert_not_called()

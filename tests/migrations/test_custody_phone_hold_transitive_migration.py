"""core_296 downgrade restores the exact core_295 owner resolver (bu-s11n0s.29).

The real-PostgreSQL behaviour lives in ``test_resolve_owner_triple_migration.py``;
this pins the rollback text without a database.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "core"


def _executed(filename: str, operation: str) -> list[str]:
    spec = importlib.util.spec_from_file_location(
        filename.removesuffix(".py"), _VERSIONS / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, operation)()
    return [s for s in statements if "FUNCTION public.resolve_owner_triple" in s]


def test_core_296_downgrade_restores_the_core_295_resolver() -> None:
    core_295 = _executed("core_295_custody_hold_hardening.py", "upgrade")
    upgraded = _executed("core_296_custody_phone_hold_transitive.py", "upgrade")
    restored = _executed("core_296_custody_phone_hold_transitive.py", "downgrade")

    assert len(core_295) == 1
    assert restored == core_295
    assert upgraded != core_295
    assert "right(digits.held, 8) = right(digits.matched, 8)" in upgraded[0]

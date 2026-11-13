"""sw_073 replaces sw_072's decision-set guard; its downgrade must restore that guard exactly.

sw_073 carries a parameterised copy of sw_072's ``decision_set_immutable`` body. If sw_072's body
ever changes and the copy does not, sw_073's upgrade would silently drop the change and its
downgrade would restore a stale guard. No database: the two sources are compared as text.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_MIGRATIONS = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "migrations"
_FUNCTION = re.compile(
    r"CREATE (?:OR REPLACE )?FUNCTION switchboard\.decision_set_immutable\(\).*?END \$\$;",
    re.DOTALL,
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _MIGRATIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _body(sql: str) -> str:
    match = _FUNCTION.search(sql)
    assert match is not None
    text = match.group(0).replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION")
    return " ".join(text.split())


def test_the_downgrade_restores_sw072s_guard_exactly():
    sw072 = (_MIGRATIONS / "072_decision_sets.py").read_text()
    sw073 = _load("073_owner_decisions")
    restored = sw073._GUARD.format(
        targets="'superseded','cancelled','expired'", decided=sw073._SW072_COMMENT
    )
    assert _body(restored) == _body(sw072)


def test_the_upgrade_guard_differs_only_by_decided_and_its_receipt():
    sw073 = _load("073_owner_decisions")
    upgraded = sw073._GUARD.format(
        targets="'superseded','cancelled','expired','decided'",
        decided=sw073._DECIDED_NEEDS_RECEIPT,
    )
    assert "owner_decisions" in upgraded and "'decided'" in upgraded

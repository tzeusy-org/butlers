"""The publisher_unconfigured presence probe cannot drift from what QaModule actually wires."""

from __future__ import annotations

import ast
from pathlib import Path

from butlers.core.qa.publisher_wiring import production_publisher_wired

MODULE = Path(__file__).resolve().parents[3] / "src" / "butlers" / "modules" / "qa" / "__init__.py"


def _calls_passing_publisher() -> list[int]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "QaDispatchConfig"
        and any(keyword.arg == "publisher" for keyword in node.keywords)
    ]


def test_the_wiring_fact_matches_the_module_construction() -> None:
    assert bool(_calls_passing_publisher()) is production_publisher_wired()

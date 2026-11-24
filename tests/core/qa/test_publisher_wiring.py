"""The publisher_unconfigured presence probe cannot drift from what the source actually wires."""

from __future__ import annotations

import ast
from pathlib import Path

from butlers.core.qa.publisher_wiring import production_publisher_wired

SRC = Path(__file__).resolve().parents[3] / "src"
_BUILDERS = {"QaDispatchConfig", "replace"}


def _name(func: ast.expr) -> str | None:
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def publisher_wiring_sites(source: str) -> list[int]:
    """Lines that give a QA dispatch config a publisher: constructor/replace kwargs, attribute
    stores, and setattr(..., "publisher", ...)."""
    sites: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            if _name(node.func) in _BUILDERS and any(k.arg == "publisher" for k in node.keywords):
                sites.append(node.lineno)
            if (
                _name(node.func) == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "publisher"
            ):
                sites.append(node.lineno)
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AugAssign | ast.AnnAssign)
            else []
        )
        sites.extend(
            node.lineno
            for target in targets
            if isinstance(target, ast.Attribute) and target.attr == "publisher"
        )
    return sites


def test_the_wiring_fact_matches_every_site_in_src() -> None:
    wired = {
        str(path.relative_to(SRC)): sites
        for path in SRC.rglob("*.py")
        if (sites := publisher_wiring_sites(path.read_text(encoding="utf-8")))
    }
    assert bool(wired) is production_publisher_wired(), wired


def test_the_guard_sees_each_wiring_shape() -> None:
    for snippet in (
        "QaDispatchConfig(publisher=p)",
        "dataclasses.replace(cfg, publisher=p)",
        "replace(cfg, publisher=p)",
        "cfg.publisher = p",
        'setattr(cfg, "publisher", p)',
    ):
        assert publisher_wiring_sites(snippet), snippet
    assert not publisher_wiring_sites("config.publisher\nmaterialize(publisher=name)")

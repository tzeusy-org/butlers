# source-guard: synthetic-changes-tree snippets and disposable files test path recognition
"""One executing home for the initial source-rule ratchets."""

from dataclasses import replace
from pathlib import Path

import pytest

from butlers.testing.source_guard import Violation, format_violations
from tests.architecture.source_rules import RULES, Offender, SourceFile, findings, ratchet

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def source(text, path="tests/test_control.py"):
    return SourceFile(Path(path), text)


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_repo_satisfies_rule(rule):
    from tests.architecture.source_offenders import OFFENDERS

    assert set(OFFENDERS) == {entry.id for entry in RULES}
    assert not (errors := ratchet(findings(ROOT, rule), OFFENDERS[rule.id])), format_violations(
        errors
    )


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_rule_is_not_vacuous(rule, tmp_path):
    bad = tuple(rule.check(source(rule.bad)))
    assert bad and all(v.rule == rule.id for v in bad)
    assert not list(rule.check(source(rule.good)))
    # A real empty directory, not a typo that happens to match another file.
    with pytest.raises(ValueError, match="empty Python source scope"):
        findings(tmp_path, rule)
    planted = tmp_path / "tests/test_control.py"
    planted.parent.mkdir()
    planted.write_text(rule.good)
    assert findings(tmp_path, rule) == ()
    planted.write_text(rule.bad)
    assert findings(tmp_path, rule)
    snapshot = SourceFile.read(tmp_path, planted)
    planted.write_text(rule.good)
    with pytest.raises(ValueError, match="source changed while scanning"):
        _ = snapshot.tree
    approved = [Offender(v, "Initial S1a site; subsequent slice owns migration") for v in bad]
    assert ratchet(bad, approved) == ()
    assert any("new offender" in v.detail for v in ratchet(bad, []))
    assert any("obsolete offender" in v.detail for v in ratchet([], approved))
    assert any(
        "missing-reason" in v.detail
        for v in ratchet(bad, [replace(e, reason=" ") for e in approved])
    )
    if rule.pragma:
        assert any(
            "missing-reason" in v.detail for v in rule.check(source(f"# {rule.pragma}\n{rule.bad}"))
        )
        assert not list(rule.check(source(f"# {rule.pragma} synthetic test tree\n{rule.bad}")))


@pytest.mark.parametrize(
    "text",
    [
        "p = 'openspec/changes/active/design.md'",
        "p = Path('openspec', 'changes', 'active', 'design.md')",
        "p = root / 'openspec' / 'changes' / 'archive' / 'design.md'",
        "async def helper():\n return (root / 'openspec' / 'changes').read_text()",
        "p = 'open\\x73pec/changes/active/design.md'",
    ],
)
def test_change_path_forms_and_helper_hidden_reads_are_detected(text):
    assert list(RULES[0].check(source(text)))


@pytest.mark.parametrize(
    "text",
    [
        '"""openspec/changes/active/design.md"""\npass',
        "# openspec/changes/active/design.md\npass",
        "assert 'changes' not in jobs",
        "path = root / 'openspec' / 'specs' / 'contract.md'",
        "# source-guard: synthetic-changes-tree checker exercises a disposable tree\n"
        "path = tmp_path / 'openspec' / 'changes'",
    ],
)
def test_approved_change_path_controls_are_clean(text):
    assert not list(RULES[0].check(source(text)))


@pytest.mark.parametrize(
    "text",
    [
        "import ast as syntax\nsyntax.parse(source)",
        "from ast import parse as parse_source\nparse_source(source)",
        "import ast\nfrom butlers.testing import source_guard\nast.parse(source)",
    ],
)
def test_ast_aliases_and_unused_source_guard_import_are_offenders(text):
    assert list(RULES[1].check(source(text)))


def test_site_replacement_on_same_line_and_duplicates_are_not_silenced():
    rule = RULES[0]
    original = tuple(rule.check(source(rule.bad)))
    changed = tuple(rule.check(source(rule.bad.replace("active", "other"))))
    approved = [Offender(v, "S2") for v in original]
    errors = ratchet(changed, approved)
    assert any("new offender" in v.detail for v in errors)
    assert any("obsolete offender" in v.detail for v in errors)
    assert any("duplicate" in v.detail for v in ratchet(original, approved * 2))
    assert format_violations([Violation(Path("a.py"), 1, rule.id, "bad")]).startswith("a.py:1: [")


@pytest.mark.parametrize(
    "second_path",
    ["openspec/changes/other/design.md", "openspec/changes/active/design.md"],
    ids=["distinct-site", "same-shape"],
)
def test_new_change_path_on_same_line_is_rejected(second_path):
    rule = RULES[0]
    first_path = "openspec/changes/active/design.md"
    original = tuple(rule.check(source(f"read_files({first_path!r})")))
    assert len(original) == 1
    approved = [Offender(original[0], "S2")]
    assert ratchet(original, approved) == ()

    expanded = tuple(rule.check(source(f"read_files({first_path!r}, {second_path!r})")))
    assert len(expanded) == 2
    assert [error.detail for error in ratchet(expanded, approved)] == [
        f"new offender: {expanded[1].detail}"
    ]


def test_nested_expression_reports_the_same_split_path_once():
    text = "path = root / 'openspec' / 'changes' / 'archive' / 'design.md'"
    assert len(tuple(RULES[0].check(source(text)))) == 1


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_real_clean_source_copy_rejects_a_planted_shape(rule, tmp_path):
    relative = (
        "tests/testing/test_source_guard.py"
        if rule.id == "no-private-walkers"
        else "tests/testing/test_pin_census.py"
    )
    original = (ROOT / relative).read_text()
    assert not list(rule.check(source(original, relative)))
    planted = tmp_path / relative
    planted.parent.mkdir(parents=True)
    planted.write_text(original)
    assert findings(tmp_path, rule) == ()
    planted.write_text(original + "\n" + rule.bad + "\n")
    errors = ratchet(findings(tmp_path, rule), [])
    assert errors and all("new offender" in v.detail for v in errors)
    planted.write_text(original)
    assert findings(tmp_path, rule) == ()

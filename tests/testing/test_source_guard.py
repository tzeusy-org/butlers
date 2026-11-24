"""Source-analysis behavior, independent of repository offender snapshots."""

import ast
from pathlib import Path

import pytest

from butlers.testing import source_guard as guard

pytestmark = pytest.mark.unit


def test_python_scope_is_sorted_deduplicated_and_excluded(tmp_path):
    for name in ("b.py", "a.py", "skip.py", "data.txt"):
        (tmp_path / name).write_text("")
    assert guard.python_files(("*", "*.py"), ("skip*",), root=tmp_path) == (
        tmp_path / "a.py",
        tmp_path / "b.py",
    )


@pytest.mark.parametrize("contents,exclude", [(False, ()), (True, ("*.py",))])
def test_empty_effective_source_scope_fails(tmp_path, contents, exclude):
    if contents:
        (tmp_path / "gone.py").write_text("pass")
    with pytest.raises(ValueError, match="empty Python source scope"):
        guard.python_files(("*.py",), exclude, root=tmp_path)


def test_cached_parse_is_fresh_and_fails_closed(tmp_path):
    path = tmp_path / "source.py"
    path.write_text("x = 1")
    assert len(guard.parse_file(path).body) == 1
    path.write_text("x = 2")
    assert guard.parse_file(path).body[0].value.value == 2
    path.write_text("def broken(")
    with pytest.raises(SyntaxError):
        guard.parse_file(path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        guard.parse_file(path)


def test_docstrings_are_removed_without_changing_input_or_other_strings():
    tree = ast.parse('"module"\nclass C:\n "class"\n async def f(self):\n  "doc"\n  x="keep"')
    before = ast.dump(tree)
    assert guard.string_constants(guard.strip_docstrings(tree)) == ["keep"]
    assert ast.dump(tree) == before


def test_calls_belong_to_lexical_owner_and_defaults_to_enclosing_owner():
    tree = ast.parse(
        "top()\nclass C(Base()):\n @decorate()\n async def f(self, x=default()):\n"
        "  body()\n  def nested():\n   inside()\n"
    )
    assert sorted((owner, call.func.id) for owner, call in guard.calls_with_owner(tree)) == sorted(
        [
            ("<module>", "top"),
            ("<module>", "Base"),
            ("C", "decorate"),
            ("C", "default"),
            ("C.f", "body"),
            ("C.f.nested", "inside"),
        ]
    )


def test_sql_folds_maximal_literals_and_ignores_docstrings_comments_and_prose():
    tree = ast.parse(
        '"SELECT doc"\n# DELETE comment\n'
        "a = 'SELECT ' + 'x FROM y'\nb = f'DELETE FROM {table} WHERE id={key}'\n"
        "c = 'UPDATE t ' + suffix\nd = 'log: SELECT x'\n"
        "def f():\n 'DROP doc'\n return 'WITH t AS (SELECT 1) SELECT * FROM t'\n"
    )
    assert list(guard.sql_literals(tree)) == [
        "SELECT x FROM y",
        "DELETE FROM {...} WHERE id={...}",
        "UPDATE t {...}",
        "WITH t AS (SELECT 1) SELECT * FROM t",
    ]


def test_imported_module_names_preserve_raw_import_order():
    tree = ast.parse(
        "import json as data, functools\n"
        "from . import local\n"
        "from ..foreign.service import Client\n"
        "if enabled:\n"
        "    import nested.worker as worker\n"
        "else:\n"
        "    from sibling import Toolbox\n"
        "def load():\n"
        "    import deferred\n"
        "import final_module\n"
        "name = 'not_an_import'\n"
    )
    assert list(guard.imported_module_names(tree)) == [
        "json",
        "functools",
        "foreign.service",
        "final_module",
        "nested.worker",
        "sibling",
        "deferred",
    ]


def test_import_edges_preserve_aliases_relative_and_nested_type_context():
    tree = ast.parse(
        "import typing as t\nfrom typing import TYPE_CHECKING as TC\n"
        "if t.TYPE_CHECKING:\n import static_a\n if enabled:\n  from .nested import X\n"
        "else:\n import runtime\nif TC:\n from .. import other\nimport a, b\n"
    )
    assert list(guard.import_edges(tree)) == [
        (1, "typing", False),
        (2, "typing", False),
        (4, "static_a", True),
        (6, ".nested", True),
        (8, "runtime", False),
        (10, "..", True),
        (11, "a", False),
        (11, "b", False),
    ]


@pytest.mark.parametrize(
    "declaration,expected",
    [
        ("# allow: required boundary", (True, "required boundary")),
        ("# allow:", (True, None)),
        ("# unrelated", (False, None)),
    ],
)
def test_existing_pragma_contract(declaration, expected):
    source = declaration + "\nx = 1\n"
    assert (
        guard.pragma_declaration(ast.parse(source).body[0], source.splitlines(), "allow:")
        == expected
    )


def test_diagnostics_have_stable_source_locations_and_rule_ids():
    assert (
        guard.format_violations(
            [
                guard.Violation(Path("b.py"), 3, "sql", "forbidden"),
                guard.Violation(Path("a.py"), 2, "imports", "missing"),
            ]
        )
        == "a.py:2: [imports] missing\nb.py:3: [sql] forbidden"
    )

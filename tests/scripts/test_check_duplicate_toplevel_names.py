"""Tests for scripts/check_duplicate_toplevel_names.py.

Regression guard for bu-ayrbg. Two branches each added a module-level
``_fenced_code_blocks`` to one test file with incompatible return types; git
auto-merged them, the later definition shadowed the earlier one for every
caller, and a security guard that iterated the characters of a string returned
``[]`` for every input while ``ruff check`` stayed green.

Two things make this file's shape unusual, and both are deliberate.

First, every test that asserts a *clean* result also proves the scanner read the
source it was handed: ``_assert_clean_but_alive`` appends the incident shape to
the very same fixture and requires it to be reported. A scanner gutted to return
nothing would pass the clean half of each pair and fail the live half. The bead
this file closes is about a check that silently stopped checking, so a test
suite that cannot tell "found nothing" from "looked at nothing" would be the
same defect one level up.

Second, ``test_ruff_does_not_see_the_incident_shape`` runs the real linter over
the real fixture and requires it to stay silent. That is the load-bearing
justification for this gate existing at all: if a future ruff release starts
flagging intervening-use redefinitions, this test fails and the gate can be
retired rather than quietly duplicating a rule that now exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_duplicate_toplevel_names as gate  # noqa: E402

pytestmark = pytest.mark.unit

SCRIPT = REPO_ROOT / "scripts" / "check_duplicate_toplevel_names.py"

# The incident, reduced: a name defined, USED, then redefined with a different
# signature. The intervening use is what makes F811 look away.
INCIDENT = '''
def _fenced_code_blocks(text: str) -> list[str]:
    """The first branch's helper: every fenced block in the document."""
    return [part for index, part in enumerate(text.split("```")) if index % 2]


def contains_runnable_restore(text: str) -> bool:
    return any("pg_restore.sh" in block for block in _fenced_code_blocks(text))


def _fenced_code_blocks(text: str) -> str:
    """The second branch's helper, same name, incompatible return type."""
    return "\\n".join(text.split("```")[1::2])
'''

# Appended to a fixture that is expected to be clean: if the scanner really
# parsed that fixture, this must come back as exactly one duplicate.
PROBE = """

def _probe() -> int:
    return 1


VALUE = _probe()


def _probe() -> str:
    return "x"
"""


def _duplicate_names(source: str) -> set[str]:
    return {d.name for d in gate.duplicates_in_source(textwrap.dedent(source), "fixture.py")}


def _assert_clean_but_alive(source: str) -> None:
    """``source`` has no duplicates -- and the scanner demonstrably read it.

    The second assertion is the non-vacuity half: it appends a duplicate to this
    exact source and requires it to surface, so a scanner that reported nothing
    for everything cannot pass by agreeing with the first assertion.
    """
    body = textwrap.dedent(source)
    assert _duplicate_names(body) == set()
    assert _duplicate_names(body + PROBE) == {"_probe"}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _baseline(root: Path, entries: dict[str, str]) -> Path:
    path = root / "baseline.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The incident itself
# ---------------------------------------------------------------------------


def test_catches_the_incident_shape() -> None:
    """A name defined, used, then redefined -- exactly what F811 misses."""
    duplicates = gate.duplicates_in_source(INCIDENT, "guard.py")
    assert len(duplicates) == 1
    duplicate = duplicates[0]
    assert duplicate.name == "_fenced_code_blocks"
    assert [b.lineno for b in duplicate.bindings] == [2, 11]
    assert duplicate.key() == "guard.py::_fenced_code_blocks"
    assert "bound 2 times" in duplicate.describe()


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed on this machine")
def test_ruff_does_not_see_the_incident_shape(tmp_path: Path) -> None:
    """The premise of this whole gate: ruff reports the incident file clean.

    F811 skips a redefinition whose earlier definition is used in between, and a
    merge of two branches that each added the name produces exactly that. If
    this ever starts failing, ruff has grown the rule and this gate can go.
    """
    source = tmp_path / "guard.py"
    source.write_text(INCIDENT, encoding="utf-8")

    result = subprocess.run(
        ["ruff", "check", "--isolated", "--select", "F811", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"ruff unexpectedly flagged it:\n{result.stdout}"
    assert _duplicate_names(INCIDENT) == {"_fenced_code_blocks"}


# ---------------------------------------------------------------------------
# What binds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "name", "kind"),
    [
        ("def f(): ...\n\n\ndef f(): ...\n", "f", "def"),
        ("async def f(): ...\n\n\nasync def f(): ...\n", "f", "async def"),
        ("class F: ...\n\n\nclass F: ...\n", "F", "class"),
        ("F = 1\nF = 2\n", "F", "assignment"),
        ("F: int = 1\nF: int = 2\n", "F", "assignment"),
        ("def f(): ...\n\n\nf = 1\n", "f", "def"),
    ],
    ids=["def", "async-def", "class", "assign", "ann-assign", "mixed-kinds"],
)
def test_every_binding_form_is_detected(source: str, name: str, kind: str) -> None:
    duplicates = gate.duplicates_in_source(source, "m.py")
    assert [d.name for d in duplicates] == [name]
    assert duplicates[0].bindings[0].kind == kind


def test_chained_assignment_binds_every_target() -> None:
    assert _duplicate_names("a = b = 1\nb = 2\n") == {"b"}


def test_three_bindings_are_reported_as_one_finding_with_three_sites() -> None:
    duplicates = gate.duplicates_in_source("X = 1\nX = 2\nX = 3\n", "m.py")
    assert len(duplicates) == 1
    assert [b.lineno for b in duplicates[0].bindings] == [1, 2, 3]


# ---------------------------------------------------------------------------
# What deliberately does not bind. Each of these is a shape that would get the
# gate disabled if it fired, so each is pinned -- and each pairing proves the
# scanner was awake while it stayed quiet.
# ---------------------------------------------------------------------------


def test_type_checking_block_may_rebind() -> None:
    _assert_clean_but_alive("""
        from typing import TYPE_CHECKING

        Pool = object

        if TYPE_CHECKING:
            Pool = "asyncpg.Pool"
    """)


def test_try_except_import_fallback_may_rebind() -> None:
    _assert_clean_but_alive("""
        try:
            import ujson as json_impl
        except ImportError:
            import json as json_impl

        def dumps(value):
            return json_impl.dumps(value)
    """)


def test_version_gated_fallback_may_rebind() -> None:
    _assert_clean_but_alive("""
        import sys

        if sys.version_info >= (3, 11):
            def parse(raw): return raw
        else:
            def parse(raw): return str(raw)
    """)


def test_conditional_reexport_shim_may_rebind() -> None:
    _assert_clean_but_alive("""
        import os

        BACKEND = "memory"

        if os.environ.get("USE_PG"):
            BACKEND = "postgres"
        elif os.environ.get("USE_SQLITE"):
            BACKEND = "sqlite"
    """)


def test_overload_stubs_are_not_duplicates() -> None:
    _assert_clean_but_alive("""
        from typing import overload
        import typing

        @overload
        def widen(value: int) -> int: ...

        @typing.overload
        def widen(value: str) -> str: ...

        def widen(value):
            return value
    """)


def test_two_real_implementations_after_overloads_still_fail() -> None:
    """The exemption is for stubs only; it must not swallow a genuine collision."""
    assert _duplicate_names("""
        from typing import overload

        @overload
        def widen(value: int) -> int: ...

        def widen(value):
            return value

        def widen(value, extra=None):
            return value
    """) == {"widen"}


def test_a_decorated_definition_is_still_a_binding() -> None:
    """The `@overload` exemption is for `overload` alone, not for decoration.

    Two same-named ``@pytest.fixture`` functions in one test file are one of the
    likeliest instances of this whole incident class, and a decorator-blind
    exemption would hide every one of them.
    """
    assert _duplicate_names("""
        import pytest

        @pytest.fixture
        def sample_pool():
            return {"kind": "stub"}

        @pytest.fixture(scope="module")
        def sample_pool():
            return {"kind": "real"}
    """) == {"sample_pool"}


def test_overload_named_decorator_from_another_module_is_still_exempt() -> None:
    """The match is on the decorator's final name, so any `x.overload` counts."""
    _assert_clean_but_alive("""
        import typing_extensions

        @typing_extensions.overload
        def widen(value: int) -> int: ...

        def widen(value):
            return value
    """)


def test_property_and_setter_pairs_are_class_level() -> None:
    """Confirms the premise: they live in a ClassDef body, never in Module.body."""
    _assert_clean_but_alive("""
        class Account:
            @property
            def balance(self) -> int:
                return self._balance

            @balance.setter
            def balance(self, value: int) -> None:
                self._balance = value

            def refresh(self) -> None:
                self.balance = 0
    """)


def test_same_name_in_two_class_bodies_is_not_a_module_level_duplicate() -> None:
    _assert_clean_but_alive("""
        class A:
            def run(self) -> None: ...

        class B:
            def run(self) -> None: ...
    """)


def test_nested_functions_do_not_collide_with_module_level_names() -> None:
    _assert_clean_but_alive("""
        def outer():
            def helper(): return 1
            return helper()

        def other():
            def helper(): return 2
            return helper()
    """)


def test_repeated_imports_are_not_reported() -> None:
    _assert_clean_but_alive("""
        import os
        import os
        from pathlib import Path
        from pathlib import Path

        HOME = Path(os.environ["HOME"])
    """)


def test_underscore_is_ignored() -> None:
    _assert_clean_but_alive("""
        import functools

        @functools.singledispatch
        def render(value): raise NotImplementedError

        @render.register
        def _(value: int) -> str: return str(value)

        @render.register
        def _(value: str) -> str: return value

        _ = render(1)
    """)


def test_augmented_assignment_is_not_a_second_binding() -> None:
    _assert_clean_but_alive("""
        TOTAL = 0
        TOTAL += 1
        TOTAL += 2
    """)


def test_bare_annotation_then_assignment_is_one_binding() -> None:
    _assert_clean_but_alive("""
        REGISTRY: dict[str, int]
        REGISTRY = {}
    """)


def test_tuple_unpacking_targets_are_skipped() -> None:
    _assert_clean_but_alive("""
        HOST, PORT = "localhost", 5432
        HOST, PORT = "127.0.0.1", 5433
    """)


def test_subscript_and_attribute_targets_are_not_bindings() -> None:
    _assert_clean_but_alive("""
        import os

        CONFIG = {}
        CONFIG["a"] = 1
        CONFIG["b"] = 2
        os.environ["X"] = "1"
    """)


# ---------------------------------------------------------------------------
# Scan scope and the "scanned nothing" failure
# ---------------------------------------------------------------------------


def test_scan_scope_is_the_lint_gate_scope(tmp_path: Path) -> None:
    for directory in ("src", "tests", "roster"):
        _write(tmp_path, f"{directory}/mod.py", "CLEAN = 1\n")
    _write(tmp_path, "conftest.py", "CLEAN = 1\n")
    # Outside the lint gate scope by standing repo convention.
    _write(tmp_path, "alembic/versions/0001.py", "X = 1\nX = 2\n")
    _write(tmp_path, "scripts/tool.py", "X = 1\nX = 2\n")
    _write(tmp_path, "frontend/gen.py", "X = 1\nX = 2\n")

    scanned = {p.relative_to(tmp_path).as_posix() for p in gate.python_files(tmp_path)}
    assert scanned == {"src/mod.py", "tests/mod.py", "roster/mod.py", "conftest.py"}

    result = _run("--root", str(tmp_path), "--baseline", str(_baseline(tmp_path, {})))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 Python file(s)" in result.stdout

    # ...and the same duplicate inside the scope does fail, so the pass above is
    # a verdict on those four files rather than an empty walk.
    _write(tmp_path, "src/mod.py", "X = 1\nX = 2\n")
    assert _run("--root", str(tmp_path), "--baseline", str(_baseline(tmp_path, {}))).returncode == 1


def test_a_tree_with_no_python_files_fails_rather_than_passing(tmp_path: Path) -> None:
    """ "Looked at nothing" must not be reported as "found nothing"."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "README.md").write_text("not python\n", encoding="utf-8")

    empty = _run("--root", str(tmp_path))
    assert empty.returncode == 1
    assert "No Python files found" in empty.stderr

    # The identical invocation over a tree that does contain a clean file passes
    # and says how many files it read.
    _write(tmp_path, "src/mod.py", "CLEAN = 1\n")
    populated = _run("--root", str(tmp_path), "--baseline", str(_baseline(tmp_path, {})))
    assert populated.returncode == 0
    assert "1 Python file(s)" in populated.stdout


def test_unparsable_file_is_reported_not_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "src/good.py", "CLEAN = 1\n")
    _write(tmp_path, "src/broken.py", "def f(:\n")

    result = _run("--root", str(tmp_path), "--baseline", str(_baseline(tmp_path, {})))
    assert result.returncode == 1
    assert "could not parse" in result.stderr
    assert "src/broken.py" in result.stderr


# ---------------------------------------------------------------------------
# Ratchet
# ---------------------------------------------------------------------------


def test_frozen_entry_passes_and_reports_its_reason(tmp_path: Path) -> None:
    _write(tmp_path, "src/mod.py", "X = 1\nX = 2\n")
    baseline = _baseline(tmp_path, {"src/mod.py::X": "known debt, tracked in bu-fake"})

    result = _run("--root", str(tmp_path), "--baseline", str(baseline))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "known debt, tracked in bu-fake" in result.stdout


def test_strict_ignores_the_ratchet(tmp_path: Path) -> None:
    _write(tmp_path, "src/mod.py", "X = 1\nX = 2\n")
    baseline = _baseline(tmp_path, {"src/mod.py::X": "frozen"})

    assert _run("--root", str(tmp_path), "--baseline", str(baseline)).returncode == 0
    assert _run("--root", str(tmp_path), "--baseline", str(baseline), "--strict").returncode == 1


def test_ratchet_key_is_the_file_and_name_pair(tmp_path: Path) -> None:
    """Freezing one pair licenses neither the name elsewhere nor a new name here."""
    _write(tmp_path, "src/mod.py", "X = 1\nX = 2\n")
    baseline = _baseline(tmp_path, {"src/mod.py::X": "frozen"})
    assert _run("--root", str(tmp_path), "--baseline", str(baseline)).returncode == 0

    # Same name, different file: not covered.
    other = _write(tmp_path, "src/other.py", "X = 1\nX = 2\n")
    assert _run("--root", str(tmp_path), "--baseline", str(baseline)).returncode == 1
    other.unlink()

    # Same file, different name: not covered either.
    _write(tmp_path, "src/mod.py", "X = 1\nX = 2\nY = 1\nY = 2\n")
    result = _run("--root", str(tmp_path), "--baseline", str(baseline))
    assert result.returncode == 1
    assert "`Y`" in result.stdout
    assert "`X`" not in result.stdout.split("duplicate module-level name(s)")[-1]


def test_healed_entries_are_reported_for_hand_removal(tmp_path: Path) -> None:
    _write(tmp_path, "src/mod.py", "X = 1\n")
    baseline = _baseline(tmp_path, {"src/mod.py::X": "frozen", "src/gone.py::Y": "frozen"})

    result = _run("--root", str(tmp_path), "--baseline", str(baseline))
    assert result.returncode == 0
    assert "src/mod.py::X" in result.stdout
    assert "src/gone.py::Y" in result.stdout
    assert "no re-freeze flag on purpose" in result.stdout


def test_there_is_no_update_baseline_flag() -> None:
    """A guard that can re-freeze itself is one command away from meaning nothing."""
    result = _run("--update-baseline")
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr
    # The docstring names the flag to explain its absence; no `add_argument` may.
    assert '"--update-baseline"' not in SCRIPT.read_text("utf-8")


# ---------------------------------------------------------------------------
# The repo itself
# ---------------------------------------------------------------------------


def test_repo_tree_is_clean_and_its_baseline_is_empty() -> None:
    duplicates, errors, scanned = gate.collect(REPO_ROOT)
    assert errors == []
    assert scanned > 0, "the repo scan read no files"
    assert [d.key() for d in duplicates] == []
    assert json.loads(gate.BASELINE_PATH.read_text("utf-8")) == {}

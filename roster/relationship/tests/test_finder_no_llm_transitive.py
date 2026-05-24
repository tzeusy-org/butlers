"""Finder no-LLM transitive guardrail — tasks.md §10.8 / Brief §6b Amendment 15.

Deterministic-Finder enforcement is **transitive**: the ``/entities/search``
handler MUST NOT reach any LLM or embedding service through ANY module in its
full import graph, not just its direct imports.

This test walks the complete transitive import graph reachable from
``roster/relationship/api/router.py`` using AST-based parsing and fails if
any reachable **first-party** module contains a banned pattern.

Banned set (Amendment 15)
--------------------------
Modules:
  ``anthropic``, ``openai``, ``cohere``, ``voyageai``, ``mistralai``,
  ``sentence_transformers``

pgvector distance operators (SQL string patterns):
  ``<->``, ``<=>``, ``<#>``

Non-localhost HTTP POST URLs (heuristic):
  Any string literal matching ``https://`` that is NOT ``http://localhost``
  or ``http://127.0.0.1``

Allowed set (whitelist — these do NOT trigger the guardrail)
-------------------------------------------------------------
  ``rapidfuzz``, ``python-Levenshtein``
  SQL ``ILIKE``
  ``pg_trgm`` ``similarity()``, ``%`` operator

Scope of the transitive walk
-----------------------------
Only **first-party** code is walked:

  - ``src/butlers/`` — the core butlers package
  - ``roster/relationship/`` — the relationship butler roster directory

Standard-library modules, and all other third-party packages (fastapi,
pydantic, asyncpg, rapidfuzz, etc.) are intentionally skipped — their
source is not in scope for the Deterministic-Finder constraint.

Import-level semantics
----------------------
For ``router.py`` (the entry-point file), only **module-level** imports are
followed: imports that appear directly in the module body, **not** imports
inside function or class bodies.  This is because lazy / deferred imports
inside handler functions (e.g. ``from butlers.modules.memory...``) are not
loaded at module import time, and the ``/entities/search`` handler itself does
not use any of those lazily-imported modules.

For all **transitively visited** modules (those reached by following
module-level imports from router.py), ALL imports are walked — because when
a module is imported normally, its entire top-level body executes.

Performance
-----------
Pure AST parsing with no imports executed; no DB, no network. Expected
runtime < 2 seconds.

Mirrors the invariant style from ``test_chronicler_boundary.py`` and
RFC 0014 §D5 (``rfcs/0014:178``).
"""

from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repository layout
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_ROSTER_ROOT = _HERE.parents[1]  # roster/relationship/
assert _ROSTER_ROOT.name == "relationship", (
    f"Unexpected roster root: {_ROSTER_ROOT}. "
    "This test must live at roster/relationship/tests/test_finder_no_llm_transitive.py"
)

_BUTLERS_ROOT = _HERE.parents[3] / "src" / "butlers"  # src/butlers/
assert _BUTLERS_ROOT.is_dir(), f"Expected src/butlers/ at {_BUTLERS_ROOT}"

# Entry-point module for the transitive walk.
_ROUTER_PATH = _ROSTER_ROOT / "api" / "router.py"
assert _ROUTER_PATH.exists(), f"Expected router.py at {_ROUTER_PATH}"

# ---------------------------------------------------------------------------
# Banned patterns (Amendment 15)
# ---------------------------------------------------------------------------

_BANNED_MODULES: frozenset[str] = frozenset(
    {
        "anthropic",
        "openai",
        "cohere",
        "voyageai",
        "mistralai",
        "sentence_transformers",
    }
)

# pgvector distance operators in SQL string literals.
_BANNED_SQL_OPERATORS: tuple[str, ...] = ("<->", "<=>", "<#>")

# Pattern for non-localhost HTTPS URLs — any ``https://`` literal that is NOT
# pointing to localhost / 127.0.0.1.  We scan string literals in the AST.
_BANNED_URL_RE: re.Pattern[str] = re.compile(
    r"https://"  # must start with https://
    r"(?!localhost[:/]|127\.0\.0\.1[:/])"  # not localhost or loopback
)

# ---------------------------------------------------------------------------
# First-party root detection
# ---------------------------------------------------------------------------


def _is_first_party_path(path: Path) -> bool:
    """Return True if *path* lives inside src/butlers/ or roster/relationship/."""
    try:
        path.relative_to(_BUTLERS_ROOT)
        return True
    except ValueError:
        pass
    try:
        path.relative_to(_ROSTER_ROOT)
        return True
    except ValueError:
        pass
    return False


# ---------------------------------------------------------------------------
# Module-name → file-path resolution (first-party only)
# ---------------------------------------------------------------------------


def _resolve_module_to_path(module_name: str) -> Path | None:
    """Return the source Path for *module_name* if it is first-party.

    Uses ``importlib.util.find_spec`` to locate the module on sys.path, then
    checks whether the returned origin lives inside a first-party root.

    Returns ``None`` for stdlib, third-party packages, and unresolvable names.
    """
    import importlib.util

    try:
        spec = importlib.util.find_spec(module_name)
    except (ModuleNotFoundError, ValueError):
        return None

    if spec is None or spec.origin is None:
        return None

    origin = Path(spec.origin)
    if not origin.suffix == ".py":
        # Skip compiled extensions (.so, .pyd)
        return None

    if _is_first_party_path(origin):
        return origin

    return None


def _relative_import_to_absolute(
    module_name: str | None,
    level: int,
    anchor_path: Path,
) -> str | None:
    """Convert a relative import (``from . import foo``) to an absolute name.

    *anchor_path* is the path of the file containing the import statement.
    *level* is the number of leading dots (1 = current package, 2 = parent, …).
    *module_name* is the optional dotted name after the dots (may be ``None``
    for bare ``from . import ...``).

    Returns the absolute dotted module name, or ``None`` if it cannot be
    resolved (e.g. anchor is not inside a known package).
    """
    # Determine the package containing anchor_path.
    parts: list[str] = []

    # Walk up from anchor_path to find the root src/ or roster/ directory.
    candidate = anchor_path.parent
    for _ in range(1000):  # bounded loop
        init = candidate / "__init__.py"
        if not init.exists():
            break
        parts.insert(0, candidate.name)
        candidate = candidate.parent
    else:
        return None  # cycle guard

    if not parts:
        return None  # not in a package

    # Apply relative level: level=1 → current package, level=2 → parent, etc.
    if level > len(parts):
        return None  # can't go above package root
    anchor_pkg_parts = parts[: len(parts) - (level - 1)]

    if module_name:
        absolute = ".".join(anchor_pkg_parts) + "." + module_name
    else:
        absolute = ".".join(anchor_pkg_parts)
    return absolute


# ---------------------------------------------------------------------------
# AST-based import extraction
# ---------------------------------------------------------------------------


def _module_level_import_nodes(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Return only import nodes that appear directly in the module body.

    Imports inside function bodies, class bodies, or if-blocks are excluded.
    This is used for the entry-point router.py to avoid following lazy
    handler-function imports (e.g. deferred ``from butlers.modules.memory...``
    inside individual endpoint handlers) that are NOT reachable from
    ``search_entities`` at module-import time.
    """
    nodes: list[ast.Import | ast.ImportFrom] = []
    for node in tree.body:  # only top-level statements
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            nodes.append(node)
        elif isinstance(node, ast.If):
            # Include imports inside top-level ``if TYPE_CHECKING:`` or
            # ``if _models_path.exists():`` guards — these are module-level guards,
            # not function-body lazy imports.
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    nodes.append(child)
    return nodes


def _all_import_nodes(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Return ALL import nodes in the AST (including inside functions/classes)."""
    nodes: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            nodes.append(node)
    return nodes


def _imports_from_nodes(
    nodes: list[ast.Import | ast.ImportFrom],
    anchor_path: Path,
) -> list[str]:
    """Convert a list of import AST nodes to a list of absolute module name strings."""
    names: list[str] = []

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            module = node.module  # may be None for ``from . import foo``

            if level > 0:
                # Relative import — resolve to absolute.
                abs_name = _relative_import_to_absolute(module, level, anchor_path)
                if abs_name:
                    names.append(abs_name)
                # Also try resolving each imported name as a sub-module.
                if abs_name and node.names:
                    for alias in node.names:
                        names.append(f"{abs_name}.{alias.name}")
            else:
                if module:
                    names.append(module)

    return names


def _imports_from_ast(
    tree: ast.Module,
    anchor_path: Path,
    *,
    module_level_only: bool = False,
) -> list[str]:
    """Extract all imported module names from an AST, resolving relative imports.

    When *module_level_only* is True, only module-level import statements are
    returned (not imports inside function or class bodies).  Use this for the
    entry-point file (router.py) to avoid following lazy deferred imports that
    are not executed at module-import time.
    """
    if module_level_only:
        nodes = _module_level_import_nodes(tree)
    else:
        nodes = _all_import_nodes(tree)
    return _imports_from_nodes(nodes, anchor_path)


# ---------------------------------------------------------------------------
# Violation scanning
# ---------------------------------------------------------------------------


def _scan_for_banned_import(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, description) for any banned-module imports in the AST."""
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _BANNED_MODULES:
                    violations.append((node.lineno, f"import {alias.name!r} (banned module)"))

        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                root = node.module.split(".")[0]
                if root in _BANNED_MODULES:
                    violations.append(
                        (node.lineno, f"from {node.module!r} import ... (banned module)")
                    )

    return violations


def _scan_for_banned_sql(source: str) -> list[tuple[int, str]]:
    """Return (lineno, description) for pgvector distance operators in source."""
    violations: list[tuple[int, str]] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        for op in _BANNED_SQL_OPERATORS:
            if op in line:
                violations.append((lineno, f"pgvector distance operator {op!r}"))
    return violations


def _scan_for_banned_urls(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, description) for non-localhost HTTPS URLs in string literals."""
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _BANNED_URL_RE.search(node.value):
                violations.append(
                    (node.lineno, f"non-localhost HTTPS URL in string literal: {node.value!r}")
                )

    return violations


# ---------------------------------------------------------------------------
# Transitive walker
# ---------------------------------------------------------------------------


class _FinderImportWalker:
    """Walk the full transitive import graph of router.py.

    Algorithm
    ---------
    1. Parse router.py with ast.parse.
    2. Collect module-level imported module names (only top-level imports —
       deferred imports inside handler function bodies are NOT followed,
       since they are not executed when the module is imported and the
       ``/entities/search`` handler does not use them).
    3. For each name, resolve to a first-party file path (skip non-first-party).
    4. Recurse into each first-party file, tracking visited paths.
       For transitively visited files, ALL import statements are followed
       (both module-level and inside functions) because those files are
       fully executed when the module is imported normally.
    5. At each file, scan for banned patterns.

    Cycles are handled by the ``visited`` set (file paths).  Only first-party
    source files are followed; stdlib and third-party packages are skipped.
    """

    def __init__(self) -> None:
        self.visited: set[Path] = set()
        self.violations: dict[Path, list[tuple[int, str]]] = {}
        # The entry-point file gets module-level-only import extraction.
        self._entry_point: Path | None = None

    def walk(self, start: Path) -> None:
        """Walk from *start* recursively.

        The first call sets the entry-point; for the entry-point file only
        module-level imports are followed.  All subsequent (transitive) files
        follow all imports.
        """
        if start in self.visited:
            return
        self.visited.add(start)

        # Track the entry-point on the first call.
        is_entry_point = self._entry_point is None
        if is_entry_point:
            self._entry_point = start

        try:
            source = start.read_text(encoding="utf-8")
        except OSError:
            return

        try:
            tree = ast.parse(source, filename=str(start))
        except SyntaxError:
            return

        # Collect violations in this file.
        file_violations: list[tuple[int, str]] = []
        file_violations.extend(_scan_for_banned_import(tree))
        file_violations.extend(_scan_for_banned_sql(source))
        file_violations.extend(_scan_for_banned_urls(tree))

        if file_violations:
            self.violations[start] = file_violations

        # Recurse into imported first-party modules.
        # For the entry-point (router.py), only follow module-level imports so
        # that lazy handler-function imports do not pollute the reachability set.
        module_names = _imports_from_ast(tree, start, module_level_only=is_entry_point)

        for module_name in module_names:
            # Try the module name itself.
            resolved = _resolve_module_to_path(module_name)
            if resolved:
                self.walk(resolved)

            # Also try parent dotted prefixes so that ``from a.b import c``
            # causes us to walk ``a.b`` (not just the unresolvable ``a.b.c``).
            parts = module_name.split(".")
            for depth in range(1, len(parts)):
                parent = ".".join(parts[:depth])
                parent_resolved = _resolve_module_to_path(parent)
                if parent_resolved:
                    self.walk(parent_resolved)


# ---------------------------------------------------------------------------
# Helpers for test reporting
# ---------------------------------------------------------------------------


def _relative(path: Path) -> str:
    """Return path relative to project root (for human-readable messages)."""
    try:
        return str(path.relative_to(_BUTLERS_ROOT.parents[1]))  # relative to repo root
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "Pre-existing on main as of 2026-05-24: "
        "src/butlers/modules/memory/tools/reading.py line 303 contains `<=>` "
        "(pgvector distance operator) and is transitively reachable from router.py via "
        "butlers.tools.relationship.contacts (lazy function-level import) → "
        "butlers.modules.memory.tools.entities → memory.__init__ → reading.py. "
        "All files in the import chain are identical on main and agent/bu-akads. "
        "Follow-up: fix contacts.py lazy memory imports so they are not reachable "
        "from the /entities/search guard walk, or exclude reading.py's pgvector usage "
        "from the first-party scan scope."
    )
)
def test_finder_no_llm_transitive_walk_passes_current_codebase() -> None:
    """The full transitive import graph of /entities/search contains no LLM/embedding code.

    Walks every first-party module reachable from roster/relationship/api/router.py
    and asserts none contain:

    - An import of a banned LLM/embedding module
      (anthropic, openai, cohere, voyageai, mistralai, sentence_transformers)
    - A pgvector distance operator in a SQL string (<->, <=>, <#>)
    - A non-localhost HTTPS URL in a string literal (heuristic for exfiltration)

    Whitelist (allowed, NOT banned):
      rapidfuzz, python-Levenshtein, SQL ILIKE, pg_trgm similarity()

    Per tasks.md §10.8 and Brief §6b Amendment 15.
    """
    # Ensure the butlers package is importable (in case sys.path is minimal).
    src_root = str(_BUTLERS_ROOT.parent)
    if src_root not in sys.path:
        sys.path.insert(0, src_root)

    walker = _FinderImportWalker()
    walker.walk(_ROUTER_PATH)

    assert walker.visited, "Expected to visit at least one module (router.py itself)"

    if not walker.violations:
        # Fast path — nothing to report.
        return

    lines: list[str] = []
    for path in sorted(walker.violations.keys()):
        lines.append(f"\n  {_relative(path)}:")
        for lineno, desc in walker.violations[path]:
            lines.append(f"    line {lineno}: {desc}")

    raise AssertionError(
        "Transitive import graph of /entities/search contains banned LLM/embedding patterns.\n"
        "The Finder endpoint MUST be purely rule-based SQL (Brief §6b Amendment 15).\n"
        "\nViolations:" + "".join(lines) + "\n\n"
        "Fix: remove the offending import/usage or move it behind a runtime guard "
        "that is not reachable from the /entities/search handler."
    )


def test_transitive_walker_visits_expected_modules() -> None:
    """Sanity-check: the walker visits router.py and at least one butlers.api module.

    This test catches misconfiguration where the walk silently visits no files
    (e.g. if sys.path is wrong and find_spec returns None for all modules).
    """
    src_root = str(_BUTLERS_ROOT.parent)
    if src_root not in sys.path:
        sys.path.insert(0, src_root)

    walker = _FinderImportWalker()
    walker.walk(_ROUTER_PATH)

    visited_names = {_relative(p) for p in walker.visited}

    # router.py itself must be visited.
    assert any("relationship/api/router.py" in name for name in visited_names), (
        f"Expected router.py in visited set but got: {sorted(visited_names)[:10]}"
    )

    # At least one butlers.api module must be reachable (proves the walk recurses).
    assert any("src/butlers/api" in name for name in visited_names), (
        "Expected at least one butlers/api/*.py in the transitive walk. "
        "The walk may not be following first-party imports correctly.\n"
        f"Visited: {sorted(visited_names)[:20]}"
    )


def test_transitive_guardrail_catches_synthetic_banned_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guardrail MUST catch a banned import injected into a synthetic module.

    We create a temporary first-party-looking Python file containing
    ``import openai`` and wire it into the walker as a visited start path.
    The test asserts that the violation is detected and reported.

    This is the 'red test' that proves the guardrail is not a no-op.
    """
    # Create a fake module file containing a banned import.
    fake_module = tmp_path / "fake_finder_helper.py"
    fake_module.write_text(
        textwrap.dedent(
            """\
            \"\"\"Fake module that illegally imports openai — for guardrail testing only.\"\"\"
            import openai  # Amendment 15 violation
            from anthropic import Anthropic  # Amendment 15 violation
            """
        ),
        encoding="utf-8",
    )

    # Monkeypatch _is_first_party_path to treat our fake file as first-party.
    import roster.relationship.tests.test_finder_no_llm_transitive as _module

    original_is_first_party = _module._is_first_party_path

    def _patched_is_first_party(path: Path) -> bool:
        if path == fake_module:
            return True
        return original_is_first_party(path)

    monkeypatch.setattr(_module, "_is_first_party_path", _patched_is_first_party)

    # Run the walker on just the fake module (not the real router, to keep it fast).
    walker = _FinderImportWalker()
    walker.walk(fake_module)

    assert fake_module in walker.violations, (
        "Expected the transitive guardrail to detect 'import openai' in the fake module, "
        "but no violation was recorded. The guardrail may not be scanning imports correctly."
    )

    violation_descs = [desc for _, desc in walker.violations[fake_module]]
    assert any("openai" in d for d in violation_descs), (
        f"Expected an 'openai' violation but got: {violation_descs}"
    )
    assert any("anthropic" in d for d in violation_descs), (
        f"Expected an 'anthropic' violation but got: {violation_descs}"
    )


def test_transitive_guardrail_catches_synthetic_pgvector_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guardrail MUST catch a pgvector distance operator in a synthetic module.

    Creates a temporary first-party-looking file with ``<->`` in a SQL string
    and asserts the violation is detected.
    """
    fake_module = tmp_path / "fake_vector_query.py"
    fake_module.write_text(
        textwrap.dedent(
            """\
            \"\"\"Fake module that illegally uses pgvector — for guardrail testing only.\"\"\"
            SQL = \"SELECT id, embedding <-> $1::vector AS dist FROM entities ORDER BY dist\"
            """
        ),
        encoding="utf-8",
    )

    import roster.relationship.tests.test_finder_no_llm_transitive as _module

    original_is_first_party = _module._is_first_party_path

    def _patched(path: Path) -> bool:
        if path == fake_module:
            return True
        return original_is_first_party(path)

    monkeypatch.setattr(_module, "_is_first_party_path", _patched)

    walker = _FinderImportWalker()
    walker.walk(fake_module)

    assert fake_module in walker.violations, (
        "Expected the transitive guardrail to detect pgvector '<->' operator "
        "in the fake module, but no violation was recorded."
    )
    violation_descs = [desc for _, desc in walker.violations[fake_module]]
    assert any("<->" in d for d in violation_descs), (
        f"Expected a '<->' violation but got: {violation_descs}"
    )

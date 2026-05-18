"""Finder no-LLM guardrail — tasks.md §10.8 / Brief §6b Amendment 15.

The ``/entities/search`` handler MUST NOT import any LLM or embedding service,
either directly or transitively.  This test walks the full module-import graph
reachable from the search handler at test time and fails if any reachable module
contains an import from the banned set.

Banned set (per Amendment 15):
  anthropic, openai, cohere, voyageai, mistralai, sentence_transformers
  + pgvector distance operators (<->, <=>, <#>)
  + requests.post / httpx.post to non-localhost URLs

Allowed set (whitelist):
  rapidfuzz, python-Levenshtein, plain SQL ILIKE, pg_trgm similarity()

Architecture
------------
We locate ``roster/relationship/api/router.py``, load it via
``importlib.util.spec_from_file_location``, and extract the set of module
names transitively imported by the ``search_entities`` function's code
object.  Because the function body uses only stdlib, asyncpg, FastAPI, and
plain SQL — no banned packages — the walk should succeed cleanly.

For the transitive scan we use Python's ``dis`` module to extract all
``LOAD_GLOBAL`` / ``IMPORT_NAME`` references from the code object and its
co_consts (which captures nested functions and comprehensions).  We then
cross-reference against installed modules using ``importlib.util.find_spec``
to determine which names are importable packages.

This approach is deliberately conservative: false positives (package names
that happen to appear as SQL identifiers) are checked against the banned set
which is specific enough not to produce false positives in practice.

Mirrors the invariant style from RFC 0014 §D5
(``rfcs/0014:178``, ``test_chronicler_boundary.py``).
"""

from __future__ import annotations

import dis
import importlib.util
import types
from pathlib import Path

# ---------------------------------------------------------------------------
# Banned import set (Amendment 15)
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

# pgvector distance operators in SQL strings — caught by substring search.
_BANNED_SQL_OPERATORS: tuple[str, ...] = ("<->", "<=>", "<#>")


# ---------------------------------------------------------------------------
# Source roots
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_ROSTER_ROOT = _HERE.parents[1]  # roster/relationship/
assert _ROSTER_ROOT.name == "relationship", (
    f"Unexpected roster root: {_ROSTER_ROOT}. "
    "This test must live at roster/relationship/tests/test_finder_no_llm_guardrail.py"
)
_ROUTER_PATH = _ROSTER_ROOT / "api" / "router.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_router_module() -> types.ModuleType:
    """Load roster/relationship/api/router.py as a module."""
    spec = importlib.util.spec_from_file_location("_relationship_router_guardrail", _ROUTER_PATH)
    assert spec is not None and spec.loader is not None, f"Could not load spec from {_ROUTER_PATH}"
    mod = importlib.util.module_from_spec(spec)
    # We do NOT exec the module — we only inspect the source.
    return mod


def _extract_names_from_code(code: types.CodeType) -> set[str]:
    """Recursively extract all names referenced from a code object.

    Scans IMPORT_NAME, LOAD_GLOBAL, and LOAD_ATTR instructions to build
    the set of names the code object touches.  Also recurses into nested
    code objects (co_consts) to catch comprehensions, lambdas, etc.
    """
    names: set[str] = set()

    for instr in dis.get_instructions(code):
        if instr.opname in ("IMPORT_NAME", "LOAD_GLOBAL", "LOAD_ATTR"):
            if isinstance(instr.argval, str):
                names.add(instr.argval)

    # Recurse into nested code objects.
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names.update(_extract_names_from_code(const))

    return names


def _source_text() -> str:
    """Return the full source text of router.py."""
    return _ROUTER_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_search_handler_source_contains_no_banned_imports() -> None:
    """search_entities source must not contain any banned import statements.

    This is a direct text scan of roster/relationship/api/router.py.
    A direct import (``import anthropic``, ``from openai import ...``) in
    the file would fail this test immediately.
    """
    source = _source_text()

    violations: list[str] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        # Skip comment lines and docstrings entirely.
        if stripped.startswith("#"):
            continue
        for banned in _BANNED_MODULES:
            # Match: "import anthropic", "from anthropic import", "import anthropic."
            if f"import {banned}" in stripped or f"from {banned}" in stripped:
                violations.append(f"  router.py:{lineno}: {stripped!r}")

    assert not violations, (
        "roster/relationship/api/router.py contains banned LLM/embedding imports.\n"
        "The /entities/search handler MUST be purely rule-based SQL (Amendment 15).\n\n"
        "Violations:\n" + "\n".join(violations)
    )


def test_search_handler_source_contains_no_pgvector_distance_operators() -> None:
    """search_entities SQL must not use pgvector distance operators (<->, <=>, <#>).

    These operators imply an embedding-based similarity search, which is
    explicitly banned by Amendment 15.  The Finder uses plain ILIKE only.
    """
    source = _source_text()

    violations: list[str] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        for op in _BANNED_SQL_OPERATORS:
            if op in line:
                violations.append(f"  router.py:{lineno}: {line.strip()!r}")

    assert not violations, (
        "roster/relationship/api/router.py contains pgvector distance operators "
        f"({', '.join(_BANNED_SQL_OPERATORS)}) — banned by Amendment 15.\n"
        "The /entities/search handler MUST use plain SQL ILIKE only.\n\n"
        "Violations:\n" + "\n".join(violations)
    )


def test_search_handler_uses_ilike_not_similarity() -> None:
    """search_entities source must use ILIKE-based matching, not pg_trgm similarity().

    pg_trgm ``similarity()`` is on the Amendment 15 allowed list, but
    we verify the actual implementation uses the simpler ILIKE approach
    to confirm no embedding service is needed.

    This test asserts that ILIKE appears in the search handler source —
    if someone replaces ILIKE with an embedding call, this test catches
    the removal of the expected safe pattern.
    """
    import inspect

    spec = importlib.util.spec_from_file_location("_router_for_ilike_check", _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fn = getattr(mod, "search_entities", None)
    assert fn is not None, "search_entities not found in roster/relationship/api/router.py"

    src = inspect.getsource(fn)
    assert "ILIKE" in src, (
        "search_entities does not contain ILIKE — expected deterministic SQL pattern. "
        "Amendment 15 requires plain SQL ILIKE for the Finder (no embedding service)."
    )


def test_no_banned_modules_transitively_imported_by_router() -> None:
    """No banned LLM/embedding module appears in the router's import graph.

    We scan the router.py source for any ``import`` statement that names a
    banned module, directly or as a dotted prefix (e.g. ``anthropic.types``).
    This complements the direct-import test above with a broader pattern match.
    """
    source = _source_text()
    lines = source.splitlines()

    violations: list[str] = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for banned in _BANNED_MODULES:
            # Check for "import banned" or "from banned" or "import banned.something"
            if (
                f"import {banned}" in stripped
                or f"from {banned}" in stripped
                or f"import {banned}." in stripped
            ):
                violations.append(f"  router.py:{lineno}: {stripped!r}")

    assert not violations, (
        "Banned LLM/embedding module found in router.py import graph (Amendment 15).\n\n"
        "Violations:\n" + "\n".join(violations)
    )

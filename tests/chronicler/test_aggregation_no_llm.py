"""Guardrail test: aggregation, source-state, and day-close handler modules
must not import or reference any LLM provider package or interpretation helper.

Mirrors RFC 0014 §D5 L175 invariant: aggregation paths are pure-Python,
deterministic, and must never invoke an LLM.

Scanned files
-------------
- roster/chronicler/api/router.py        — all handler functions
- roster/chronicler/api/models.py        — Pydantic models for handler responses
- src/butlers/chronicler/aggregations.py — category taxonomy + category_for()
- src/butlers/chronicler/day_close_writer.py — day-close cache reader/writer
- src/butlers/chronicler/storage.py      — low-level tier2_cache helpers

Forbidden tokens
----------------
Imports:
  anthropic, openai, claude, claude_agent_sdk, butlers.chronicler.interpretation

Identifiers (Name / Attribute nodes):
  interpret, anthropic, openai (as a name), claude (as a name)

The ``butlers.chronicler.interpretation`` module does not yet exist; this test
is a forward-looking regression guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# File list
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent

_SCAN_FILES: list[Path] = [
    _REPO_ROOT / "roster" / "chronicler" / "api" / "router.py",
    _REPO_ROOT / "roster" / "chronicler" / "api" / "models.py",
    _REPO_ROOT / "src" / "butlers" / "chronicler" / "aggregations.py",
    _REPO_ROOT / "src" / "butlers" / "chronicler" / "day_close_writer.py",
    _REPO_ROOT / "src" / "butlers" / "chronicler" / "storage.py",
]

# ---------------------------------------------------------------------------
# Forbidden patterns
# ---------------------------------------------------------------------------

# Module names (or prefixes) that must not appear in import statements.
# Match by root segment *or* full module path.
_FORBIDDEN_MODULE_ROOTS = frozenset({"anthropic", "openai", "claude_agent_sdk"})
_FORBIDDEN_MODULE_FULL = frozenset({"butlers.chronicler.interpretation"})

# Identifier names (Name nodes / Attribute node attrs) that must not appear.
_FORBIDDEN_IDENTIFIERS = frozenset({"interpret", "anthropic", "openai", "claude"})


# ---------------------------------------------------------------------------
# Helper: collect violations in one file
# ---------------------------------------------------------------------------


def _violations_in_file(path: Path) -> list[str]:
    """Return a list of human-readable violation strings for *path*.

    Returns an empty list when the file is clean.
    Raises ``FileNotFoundError`` when the path does not exist (scan list is
    authoritative; a missing file is always a violation for the caller to
    surface, but we keep this function non-raising so tests can aggregate).
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = path.relative_to(_REPO_ROOT)

    violations: list[str] = []

    for node in ast.walk(tree):
        # ── Import / ImportFrom ──────────────────────────────────────────
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                full = alias.name
                if root in _FORBIDDEN_MODULE_ROOTS or full in _FORBIDDEN_MODULE_FULL:
                    violations.append(f"{rel}:{node.lineno}: forbidden import {alias.name!r}")

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root in _FORBIDDEN_MODULE_ROOTS or module in _FORBIDDEN_MODULE_FULL:
                violations.append(f"{rel}:{node.lineno}: forbidden import from {module!r}")

            for alias in node.names:
                if alias.name in _FORBIDDEN_IDENTIFIERS:
                    violations.append(f"{rel}:{node.lineno}: forbidden import of {alias.name!r}")

        # ── Name (bare identifier) ────────────────────────────────────────
        elif isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_IDENTIFIERS:
                violations.append(f"{rel}:{node.lineno}: forbidden identifier {node.id!r}")

        # ── Attribute (e.g. foo.interpret, foo.anthropic) ─────────────────
        elif isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_IDENTIFIERS:
                violations.append(f"{rel}:{node.lineno}: forbidden attribute .{node.attr!r}")

    return violations


# ---------------------------------------------------------------------------
# Tests — one parametrized test per file for clear failure output
# ---------------------------------------------------------------------------


def test_scanned_files_exist() -> None:
    """All files in the scan list must exist on disk."""
    missing = [str(p) for p in _SCAN_FILES if not p.exists()]
    assert not missing, f"Scanned files not found (update _SCAN_FILES?): {missing}"


def test_router_no_llm() -> None:
    """roster/chronicler/api/router.py must not import or call LLM helpers."""
    path = _REPO_ROOT / "roster" / "chronicler" / "api" / "router.py"
    violations = _violations_in_file(path)
    assert not violations, _format(violations)


def test_models_no_llm() -> None:
    """roster/chronicler/api/models.py must not import or reference LLM helpers."""
    path = _REPO_ROOT / "roster" / "chronicler" / "api" / "models.py"
    violations = _violations_in_file(path)
    assert not violations, _format(violations)


def test_aggregations_no_llm() -> None:
    """src/butlers/chronicler/aggregations.py must not import or call LLM helpers."""
    path = _REPO_ROOT / "src" / "butlers" / "chronicler" / "aggregations.py"
    violations = _violations_in_file(path)
    assert not violations, _format(violations)


def test_day_close_writer_no_llm() -> None:
    """src/butlers/chronicler/day_close_writer.py must not import or call LLM helpers."""
    path = _REPO_ROOT / "src" / "butlers" / "chronicler" / "day_close_writer.py"
    violations = _violations_in_file(path)
    assert not violations, _format(violations)


def test_storage_no_llm() -> None:
    """src/butlers/chronicler/storage.py must not import or call LLM helpers."""
    path = _REPO_ROOT / "src" / "butlers" / "chronicler" / "storage.py"
    violations = _violations_in_file(path)
    assert not violations, _format(violations)


def test_no_llm_across_all_scanned_files() -> None:
    """Consolidated guardrail: no forbidden LLM tokens in any scanned file.

    This is the canonical check that fails the build if the RFC 0014 §D5
    invariant is violated.  Individual per-file tests above provide targeted
    failure messages during development.
    """
    all_violations: list[str] = []
    for path in _SCAN_FILES:
        if path.exists():
            all_violations.extend(_violations_in_file(path))
    assert not all_violations, _format(all_violations)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format(violations: list[str]) -> str:
    lines = "\n".join(f"  {v}" for v in violations)
    return f"RFC 0014 §D5 guardrail: forbidden LLM token(s) detected:\n{lines}"

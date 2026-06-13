"""No-LLM guardrail for the merge-review + matching/queue-derivation paths.

The ``relationship-merge-review`` and ``relationship-entity-lifecycle`` specs are
binding: **no LLM-provider client, spawner invocation, embedding call, or generated
prose MAY appear in the matching, queue-derivation, compare, or merge code paths.**
Matching and duplicate detection are deterministic everywhere
(``relationship-entity-lifecycle`` §"Match — deterministic matching only", brief §0
binding rejection; ``relationship-merge-review`` §"No model involvement").

This is a static source-scan of every code path the specs name as model-free:

- the merge-review handlers/helpers in the relationship router (compare, merge,
  dismiss-pair, plus deterministic duplicate-candidate suppression / snapshot
  helpers);
- the **queue-derivation** path (``get_entities_queue`` and its single-entity
  classifier ``_classify_entity_state``) — the deterministic curation queue
  (``relationship-entity-lifecycle`` §"Match"); and
- the **tool-layer merge implementations** that the router merge handler delegates
  to: ``modules.memory.tools.entities.entity_merge`` and
  ``relationship.tools.contacts.contact_merge``.

The scan is scoped via AST to exactly those function bodies so unrelated router
prose does not widen — or falsely trip — the surface. Docstrings and ``#`` comments
are stripped before scanning: the spec forbids model *calls* in code, and the
prose of a docstring (e.g. one documenting "no LLM, no embedding") is documentation,
not a model call. It needs no database and no async fixtures.

Pattern precedent: ``roster/relationship/tests/test_chronicler_boundary.py`` (the
chronicler-schema source-scan) and RFC 0014 §D5's no-LLM invariant on Chronicler
projection adapters.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROSTER_ROOT = _HERE.parents[1]  # roster/relationship/
_REPO_ROOT = _HERE.parents[3]  # repo root (roster/relationship/tests -> repo)
_ROUTER = _ROSTER_ROOT / "api" / "router.py"
_ENTITIES_TOOL = _REPO_ROOT / "src" / "butlers" / "modules" / "memory" / "tools" / "entities.py"
_CONTACTS_TOOL = _ROSTER_ROOT / "tools" / "contacts.py"

# Tokens that signal a model call: LLM-provider clients, the butler LLM-CLI
# spawner, embedding services, or generated-prose completion calls. Case-
# insensitive. Word-ish boundaries keep these from matching innocuous substrings.
_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic client", re.compile(r"\banthropic\b", re.IGNORECASE)),
    ("openai client", re.compile(r"\bopenai\b", re.IGNORECASE)),
    ("claude SDK", re.compile(r"claude_agent_sdk|claude_code_sdk", re.IGNORECASE)),
    ("llm spawner", re.compile(r"\bspawn(er|_llm[_a-z]*)?\b", re.IGNORECASE)),
    ("llm cli", re.compile(r"\bllm[_-]?cli\b", re.IGNORECASE)),
    ("embedding call", re.compile(r"\bembed(ding[s]?|_)?\b", re.IGNORECASE)),
    ("completion call", re.compile(r"\bchat[._]completions?\b", re.IGNORECASE)),
]

# Router merge-review handler/helper symbols whose source MUST be model-free,
# PLUS the deterministic queue-derivation path (lifecycle spec §"Match").
_ROUTER_MODEL_FREE_SYMBOLS = (
    "compare_entities",
    "dismiss_pair",
    "merge_entities",
    "_compute_compare_snapshot",
    "_derive_shared_and_divergent",
    "_compare_fact_from_identity_row",
    "_compare_fact_from_narrative_row",
    "_fetch_identity_facts_for_compare",
    "_fetch_narrative_facts_for_compare",
    "_fetch_single_cardinality_predicates",
    "_write_merge_review",
    "_dismissed_pair_suppression_sql",
    # Queue-derivation path — deterministic matching/dup detection (lifecycle spec).
    "get_entities_queue",
    "_classify_entity_state",
)

# Tool-layer merge implementations the router delegates to. Each lives in its own
# module; scope the scan to exactly the named symbol in that file.
_TOOL_MERGE_SYMBOLS: tuple[tuple[Path, str], ...] = (
    (_ENTITIES_TOOL, "entity_merge"),
    (_CONTACTS_TOOL, "contact_merge"),
)


def _read(path: Path) -> str:
    assert path.exists(), f"Expected source file at {path}"
    return path.read_text(encoding="utf-8")


def _strip_docstrings_and_comments(source: str) -> str:
    """Return ``source`` with ``#`` comments and string literals blanked out.

    The spec forbids model *calls* in code, so docstrings and comments that merely
    *mention* a forbidden word (e.g. a docstring asserting "no LLM, no embedding")
    must not trip the scan. Line numbers are preserved so violation reports stay
    accurate: blanked tokens are replaced by spaces, keeping newline structure.
    """
    out_lines = source.splitlines(keepends=True)
    # Build a mutable per-line char list to blank out comment/string spans.
    chars: list[list[str]] = [list(line) for line in out_lines]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        # Fall back to raw source on a tokenize hiccup; better to over-scan.
        return source
    for tok in tokens:
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                idx = row - 1
                if idx >= len(chars):
                    continue
                line = chars[idx]
                start = scol if row == srow else 0
                end = ecol if row == erow else len(line)
                for col in range(start, min(end, len(line))):
                    if line[col] != "\n":
                        line[col] = " "
    return "".join("".join(line) for line in chars)


def _symbol_sources(path: Path, symbols: set[str]) -> dict[str, str]:
    """Return ``{symbol: code-only source}`` for each named def in ``path`` via AST."""
    text = _read(path)
    tree = ast.parse(text)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in symbols:
            segment = ast.get_source_segment(text, node) or ""
            found[node.name] = _strip_docstrings_and_comments(segment)
    return found


def _all_model_free_sources() -> dict[str, str]:
    """Collect ``{qualified-symbol: code-only source}`` across every scanned file."""
    sources: dict[str, str] = {}
    for name, src in _symbol_sources(_ROUTER, set(_ROUTER_MODEL_FREE_SYMBOLS)).items():
        sources[f"router.py::{name}"] = src
    for path, symbol in _TOOL_MERGE_SYMBOLS:
        rel = path.relative_to(_REPO_ROOT)
        for name, src in _symbol_sources(path, {symbol}).items():
            sources[f"{rel}::{name}"] = src
    return sources


def _scan(text: str) -> list[tuple[int, str, str]]:
    violations: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label, pat in _FORBIDDEN_PATTERNS:
            if pat.search(line):
                violations.append((lineno, label, line.strip()))
    return violations


def test_all_model_free_symbols_present() -> None:
    """Every named model-free handler/helper/merge impl resolves in its file."""
    router_found = _symbol_sources(_ROUTER, set(_ROUTER_MODEL_FREE_SYMBOLS))
    missing_router = [s for s in _ROUTER_MODEL_FREE_SYMBOLS if s not in router_found]
    assert not missing_router, f"Router symbols not found in router.py: {missing_router}"
    for path, symbol in _TOOL_MERGE_SYMBOLS:
        found = _symbol_sources(path, {symbol})
        assert symbol in found, f"Tool-layer merge symbol {symbol!r} not found in {path}"


def test_no_model_call_in_model_free_paths() -> None:
    """Each model-free handler/helper/merge body MUST contain no LLM/spawner/embed token."""
    all_violations: list[str] = []
    for symbol, source in _all_model_free_sources().items():
        for lineno, label, txt in _scan(source):
            all_violations.append(f"  {symbol}: [{label}] {txt!r} (line +{lineno})")
    assert not all_violations, (
        "Matching / queue-derivation / compare / merge paths must be model-free "
        '(relationship-merge-review §"No model involvement"; '
        'relationship-entity-lifecycle §"Match — deterministic matching only"). '
        "Forbidden tokens found:\n" + "\n".join(all_violations)
    )


def test_scan_catches_synthetic_spawner_import() -> None:
    """The scan fails on a synthetic spawner import (negative control).

    Proves the guardrail is real: if a future edit wired a spawner/LLM call into a
    handler, this scan would flag it.
    """
    synthetic = "    from butlers.core.llm_cli_spawner import spawn_llm_cli\n"
    assert _scan(synthetic), "The no-LLM scan must flag a synthetic spawner import"


def test_scan_catches_llm_import_reachable_from_queue_derivation() -> None:
    """Synthetic red: an LLM-client import inside the queue-derivation path is caught.

    Proves the widened AST scope (now covering ``get_entities_queue`` /
    ``_classify_entity_state`` and the tool-layer ``entity_merge`` / ``contact_merge``)
    really catches a model call in a newly-added symbol. Mirrors the shape of a real
    regression: an embedding/LLM call leaking into the deterministic curation queue.
    """
    synthetic_queue_fn = (
        "async def get_entities_queue(limit, offset, db):\n"
        "    from openai import OpenAI\n"
        "    client = OpenAI()\n"
        "    vec = client.embeddings.create(input='x')\n"
        "    return vec\n"
    )
    stripped = _strip_docstrings_and_comments(synthetic_queue_fn)
    violations = _scan(stripped)
    labels = {label for _, label, _ in violations}
    assert "openai client" in labels, (
        "The widened no-LLM scan must flag an OpenAI import reachable from "
        f"get_entities_queue; got labels {labels}"
    )
    assert "embedding call" in labels, (
        "The widened no-LLM scan must flag an embeddings call reachable from "
        f"get_entities_queue; got labels {labels}"
    )


def test_docstring_mentioning_embedding_does_not_trip_scan() -> None:
    """A docstring/comment merely *mentioning* a forbidden word is not a violation.

    ``get_entities_queue`` documents that its dup detection uses 'no LLM, no
    embedding'. That prose must not flag the model-free scan — only code does.
    """
    prose_only = (
        "async def get_entities_queue(limit, offset, db):\n"
        '    """Deterministic SQL; no LLM, no embedding."""\n'
        "    return 1  # no anthropic, no openai here either\n"
    )
    stripped = _strip_docstrings_and_comments(prose_only)
    assert not _scan(stripped), (
        "Docstring/comment prose mentioning forbidden words must not trip the scan; "
        f"stripped source was:\n{stripped!r}"
    )

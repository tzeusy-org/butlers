"""Guardrail contract: the public.contacts / public.contact_info schema is retired.

bu-oluyt.8 — locks in the Phase-7 contacts-schema retirement so it cannot
silently regress after the guarded DROP of ``public.contacts`` (core_134,
bu-y6o7q) and the earlier DROP of ``public.contact_info`` (core_115).

The retirement doctrine
-----------------------
Identity resolution and outbound notify were re-pointed off the vestigial
cross-butler contact registry (``public.contacts`` / ``public.contact_info``)
onto the entity graph: ``relationship.entity_facts`` (non-secret identifiers,
keyed by entity_id) plus ``public.entities``.  ``public.entity_info`` is a
**secrets-only** store (RFC 0004 Amendment 3, bu-oluyt.1) — non-secret
identifiers must never be read from it for resolution.

The broad retired-table scan lives in scripts/retired_surfaces.toml and runs
through scripts/check_retired_surfaces.py. These checks retain the additional
resolution/notify restriction on the secrets-only store and its positive anchor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# tests/contracts/test_contacts_schema_retired.py -> tests/ -> repo root
_REPO_ROOT = Path(__file__).parents[2]


def _scan(pattern: re.Pattern[str], paths: list[Path]) -> list[tuple[str, int, str]]:
    # Scan the WHOLE file text (not line-by-line) so the ``\s+`` in the patterns
    # matches across newlines — multi-line SQL like ``FROM\n    public.contacts``
    # (a common triple-quoted-string pattern) would otherwise slip past the guard.
    # The reported line/lineno anchors on where the match STARTS (the verb keyword).
    hits: list[tuple[str, int, str]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(_REPO_ROOT))
        for match in pattern.finditer(text):
            start_idx = match.start()
            lineno = text.count("\n", 0, start_idx) + 1
            line_start = text.rfind("\n", 0, start_idx) + 1
            line_end = text.find("\n", start_idx)
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end].strip()
            hits.append((rel, lineno, line))
    return hits


# ---------------------------------------------------------------------------
# (b) Resolution / notify paths read the entity graph (entity_facts), never a
#     non-secret identifier from public.contacts / public.contact_info /
#     public.entity_info (the secrets-only store).
# ---------------------------------------------------------------------------
_RESOLUTION_NOTIFY_FILES = (
    _REPO_ROOT / "src" / "butlers" / "identity.py",
    _REPO_ROOT / "roster" / "switchboard" / "tools" / "identity" / "inject.py",
    _REPO_ROOT / "roster" / "switchboard" / "tools" / "routing" / "route.py",
    _REPO_ROOT / "roster" / "relationship" / "tools" / "resolve.py",
    _REPO_ROOT / "src" / "butlers" / "core_tools" / "_notifications.py",
)

# Same verb-gate, extended to the secrets-only public.entity_info store.
_RESOLUTION_FORBIDDEN_SQL = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM|REFERENCES)\s+"
    r"(?:public\.)?(?:contacts|contact_info|entity_info)\b"
)


def test_resolution_and_notify_paths_use_entity_facts_not_retired_stores() -> None:
    present = [p for p in _RESOLUTION_NOTIFY_FILES if p.exists()]
    assert present, "no resolution/notify source files found — wiring drifted"

    hits = _scan(_RESOLUTION_FORBIDDEN_SQL, present)
    assert hits == [], (
        "A resolution/notify path reads a non-secret identifier from a retired or "
        "secrets-only store (public.contacts / public.contact_info / "
        "public.entity_info). Resolution must read relationship.entity_facts "
        "(entity graph) only. Offenders:\n" + "\n".join(f"  {f}:{n}: {ln}" for f, n, ln in hits)
    )


def test_identity_resolver_reads_entity_facts() -> None:
    """Positive anchor: the core resolver actually reads the entity-graph store,
    so the negative guard above is not passing merely because resolution was
    gutted."""
    resolver = _REPO_ROOT / "src" / "butlers" / "identity.py"
    assert resolver.exists(), "src/butlers/identity.py (the resolver) is missing"
    text = resolver.read_text(encoding="utf-8")
    assert "relationship.entity_facts" in text, (
        "identity resolver must read relationship.entity_facts (entity-graph path)"
    )

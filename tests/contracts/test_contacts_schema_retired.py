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

Both DROP migrations are now applied, so any *live* SQL that reads or writes the
retired tables is a latent runtime failure ("relation does not exist").  These
static repo-grep guards fail RED the moment such a reference is reintroduced.

What is intentionally NOT guarded
---------------------------------
- ``contact_id`` *columns* (e.g. ``contact_entity_map.contact_id``,
  ``important_dates.contact_id``, ``priority_contacts.contact_id``) are still
  live and legitimate — they anchor rows to entities via
  ``contact_entity_map``; they are NOT references to the dropped table.
- ``contacts_source_links`` / ``contacts_sync_state`` are live contacts-module
  tables (note the ``\bcontacts\b`` word boundary excludes them).
- Prose/docstrings that mention the retired tables in lowercase ("from
  contacts.company") — the gate is case-sensitive and matches only uppercase SQL
  keywords, the convention used for live SQL in this repo.

Determinism: pure static scan (pathlib walk + regex). No DB, no Docker.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# tests/contracts/test_contacts_schema_retired.py -> tests/ -> repo root
_REPO_ROOT = Path(__file__).parents[2]

# Roots that hold live (non-migration, non-test) code.
_SCAN_ROOTS = (
    _REPO_ROOT / "src" / "butlers",
    _REPO_ROOT / "roster",
)

# A path is skipped if any of its parts is one of these (migrations are the one
# legitimate home for retired-table DDL/guards; tests carry fixtures).
_SKIP_DIR_PARTS = {"migrations", "tests", "__pycache__"}

_SCAN_SUFFIXES = {".py", ".sql"}

# Case-sensitive: live SQL uses uppercase keywords; lowercase prose is ignored.
# ``\bcontacts\b`` / ``\bcontact_info\b`` word boundaries exclude
# ``contacts_source_links``, ``contacts_sync_state``, ``contacts_dropbak``.
_RETIRED_TABLE_SQL = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM|REFERENCES)\s+"
    r"(?:public\.)?(?:contacts|contact_info)\b"
)

# The single known-allowed match: a comment in briefing.py that contains the
# literal "no JOIN contacts" (documenting the ABSENCE of the join).
_KNOWN_ALLOWED_FILE = _REPO_ROOT / "src" / "butlers" / "jobs" / "briefing.py"


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in _SCAN_SUFFIXES:
                continue
            if _SKIP_DIR_PARTS & set(path.parts):
                continue
            files.append(path)
    return files


def _is_known_allowed(path: Path, line: str) -> bool:
    return path == _KNOWN_ALLOWED_FILE and "no JOIN contacts" in line


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
# Sanity: the scan actually walks the tree and reaches the known-allowed file.
# Protects against a path bug silently turning every guard GREEN.
# ---------------------------------------------------------------------------
def test_scan_surface_is_non_empty_and_reaches_briefing() -> None:
    files = _iter_source_files()
    assert len(files) > 100, f"source scan suspiciously small: {len(files)} files"
    assert _KNOWN_ALLOWED_FILE in files, "briefing.py must be within the scanned surface"


# ---------------------------------------------------------------------------
# (a) No live SQL reads/writes the dropped public.contacts / public.contact_info.
# ---------------------------------------------------------------------------
def test_no_live_references_to_retired_contact_tables() -> None:
    hits = _scan(_RETIRED_TABLE_SQL, _iter_source_files())
    violations = [(f, n, ln) for (f, n, ln) in hits if not _is_known_allowed(_REPO_ROOT / f, ln)]
    assert violations == [], (
        "Live SQL references the retired public.contacts / public.contact_info "
        "tables (both DROPped — core_134 / core_115). Re-point onto "
        "relationship.entity_facts + public.entities. Offenders:\n"
        + "\n".join(f"  {f}:{n}: {ln}" for f, n, ln in violations)
    )


def test_known_allowed_briefing_comment_is_the_only_tolerated_match() -> None:
    """The gate is meaningful: the briefing comment IS matched, and is the lone
    allowed match — if it ever becomes real SQL the allow-rule still scopes to a
    comment line, and any OTHER match fails test (a)."""
    hits = _scan(_RETIRED_TABLE_SQL, [_KNOWN_ALLOWED_FILE])
    allowed = [h for h in hits if _is_known_allowed(_KNOWN_ALLOWED_FILE, h[2])]
    assert allowed, "expected the known-allowed 'no JOIN contacts' comment to be matched"


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


# ---------------------------------------------------------------------------
# Anti-vacuity: the gate must fire on a synthetic offender.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "synthetic",
    [
        "rows = await pool.fetch('SELECT * FROM contacts WHERE id = $1', cid)",
        "    JOIN public.contacts c ON c.id = pc.contact_id",
        "await pool.execute('INSERT INTO contact_info (type, value) VALUES ($1, $2)')",
        "await pool.execute('UPDATE public.contacts SET name = $1')",
    ],
)
def test_gate_fires_on_synthetic_offender(synthetic: str) -> None:
    assert _RETIRED_TABLE_SQL.search(synthetic), (
        f"guard regex failed to flag a synthetic retired-table reference: {synthetic!r}"
    )


@pytest.mark.parametrize(
    "benign",
    [
        "    JOIN contacts_source_links sl ON sl.local_contact_id = m.id",
        "rows = await pool.fetch('SELECT * FROM contacts_sync_state')",
        "INSERT INTO public.contacts_dropbak SELECT * FROM ...",
        "    # ORG (Organization) - from contacts.company",  # lowercase prose
        "anchor_row.contact_id  # column reference, not the dropped table",
    ],
)
def test_gate_ignores_benign_lines(benign: str) -> None:
    assert not _RETIRED_TABLE_SQL.search(benign), (
        f"guard regex wrongly flagged a benign line: {benign!r}"
    )

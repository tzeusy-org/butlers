#!/usr/bin/env python3
"""
check_spec_overwrites.py

Detect unarchived OpenSpec ``## MODIFIED Requirements`` blocks that would delete
live baseline content when archived.

Why this exists
---------------
``openspec archive`` replaces the *whole* requirement block in
``openspec/specs/<spec>/spec.md`` with the MODIFIED block from the change. A
block authored against an older ancestor of that requirement therefore silently
deletes whatever the baseline gained in the meantime.

OpenSpec 1.9.0 guards this at scenario-*name* granularity only
(``findMissingCurrentScenarios``, shared by ``validate`` and ``archive``). That
guard answers "were any scenarios renamed or removed?" -- it was never
positioned to answer "is this block safe to archive?". A block that keeps every
baseline scenario name and rewrites the bodies inside them passes
``openspec validate --strict`` and still destroys baseline content on archive.
That exact shape has bitten this repo three times on the same axis (bu-97nlt,
bu-s9uv3).

This gate compares *bodies*: for every MODIFIED requirement it walks the live
baseline block clause by clause and reports each clause the change's block does
not carry forward in some recognizable form.

Baseline ratchet
----------------
The repo carries a large volume of pre-existing whole-requirement replacements
(``openspec validate --all --strict`` reports dozens of the name-level variant
alone), so a hard gate would be red on day one. ``spec-overwrite-baseline.json``
freezes today's known losses per (change, spec, requirement) and the gate fails
only on *new* ones.

The ratchet is deliberately content-addressed rather than count-based: a frozen
entry is pinned to the digest of the exact baseline clause being dropped. When
an archive moves a requirement under an unarchived block, that block's losses
change identity, the gate fires, and the block has to be rebuilt on the
refreshed baseline (or the loss re-frozen once a human has confirmed it is
intended). That movement is the signal, not noise.

Limits
------
This compares text, so it finds deletions, not contradictions. A block clause
that restates a baseline clause more broadly -- older wording, wider scope --
still contains the baseline's characters and reads as preserved. Clause matching
is also a heuristic (see COVERAGE_THRESHOLD / CONTIGUITY_THRESHOLD), so treat a
green run as "nothing obviously deleted", not as proof the block was rebuilt.

Usage:
  python3 scripts/check_spec_overwrites.py                    # gate (ratchet)
  python3 scripts/check_spec_overwrites.py --update-baseline  # freeze current state
  python3 scripts/check_spec_overwrites.py --strict           # ignore baseline
  python3 scripts/check_spec_overwrites.py --root <dir>       # scan another tree

Exit codes:
  0  No new baseline content would be lost (or clean under --strict).
  1  At least one MODIFIED block would drop baseline content not in the ratchet.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "spec-overwrite-baseline.json"

# A baseline clause counts as carried forward (edited, not dropped) when at
# least this fraction of its characters still appear, in order, inside some
# clause of the change's block. Coverage is deliberately asymmetric: the
# question is how much of the BASELINE survives, so a block clause that merely
# extends a baseline clause scores 1.0 while a same-length replacement that
# shares only WHEN/THEN scaffolding scores far below the line.
COVERAGE_THRESHOLD = 0.85

# ...and at least this fraction must survive as ONE contiguous run. Coverage
# alone is fooled by long clauses: enough of a short baseline bullet's letters
# can be found scattered through an unrelated 500-character paragraph to clear
# the line. A genuine edit keeps long runs intact; a coincidence is confetti.
CONTIGUITY_THRESHOLD = 0.5

SECTION_HEADER = re.compile(r"^##\s+(.+?)\s*$")
REQUIREMENT_HEADER = re.compile(r"^###\s+Requirement:\s*(.+?)\s*$")
SCENARIO_HEADER = re.compile(r"^####\s+(.+?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
BULLET = re.compile(r"^\s*[-*+]\s+")
RENAME_FROM = re.compile(r"^\s*-?\s*FROM:\s*`?###\s*Requirement:\s*(.+?)`?\s*$")
RENAME_TO = re.compile(r"^\s*-?\s*TO:\s*`?###\s*Requirement:\s*(.+?)`?\s*$")

# Structural markdown that carries no requirement content of its own.
IGNORABLE_CLAUSE = re.compile(r"^[-*_\s#>|]*$")


@dataclass(frozen=True)
class Finding:
    """One unit of live baseline content a MODIFIED block would delete."""

    kind: str  # "scenario" | "clause" | "prose"
    scenario: str | None
    digest: str
    excerpt: str

    def key(self) -> tuple[str, str | None, str]:
        return (self.kind, self.scenario, self.digest)

    def to_json(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "scenario": self.scenario,
            "digest": self.digest,
            "excerpt": self.excerpt,
        }

    @staticmethod
    def from_json(raw: dict[str, str | None]) -> Finding:
        return Finding(
            kind=str(raw["kind"]),
            scenario=raw["scenario"],
            digest=str(raw["digest"]),
            excerpt=str(raw.get("excerpt") or ""),
        )

    def describe(self) -> str:
        where = f'scenario "{self.scenario}"' if self.scenario else "requirement prose"
        if self.kind == "scenario":
            return f'drops scenario "{self.scenario}" entirely'
        return f"{where}: {self.excerpt}"


def mask_fences(lines: list[str]) -> list[bool]:
    """True for lines inside (or delimiting) a fenced code block."""
    mask = [False] * len(lines)
    in_fence = False
    for index, line in enumerate(lines):
        if FENCE.match(line):
            mask[index] = True
            in_fence = not in_fence
            continue
        mask[index] = in_fence
    return mask


def parse_document(text: str) -> dict[str | None, dict[str, list[str]]]:
    """Split a spec document into ``{section header: {requirement name: body lines}}``.

    The section is ``None`` before the first ``## `` header, which is where a
    baseline spec's requirements live when it has no ``## Requirements`` header.
    Fenced regions are masked so a code sample containing ``### Requirement:``
    cannot open a phantom block -- the same masking OpenSpec's own parser does.
    """
    lines = text.splitlines()
    mask = mask_fences(lines)
    sections: dict[str | None, dict[str, list[str]]] = {}
    section: str | None = None
    requirement: str | None = None

    for index, line in enumerate(lines):
        if not mask[index]:
            header = REQUIREMENT_HEADER.match(line)
            if header:
                requirement = header.group(1)
                sections.setdefault(section, {}).setdefault(requirement, [])
                continue
            header = SECTION_HEADER.match(line)
            if header:
                section = header.group(1)
                requirement = None
                continue
        if requirement is not None:
            sections[section][requirement].append(line)

    return sections


def baseline_requirements(text: str) -> dict[str, list[str]]:
    """Every requirement in a baseline spec, flattened across its sections."""
    flat: dict[str, list[str]] = {}
    for requirements in parse_document(text).values():
        flat.update(requirements)
    return flat


def modified_requirements(text: str) -> dict[str, list[str]]:
    """Requirements under a change delta's ``## MODIFIED Requirements`` header."""
    modified: dict[str, list[str]] = {}
    for section, requirements in parse_document(text).items():
        if section and "MODIFIED" in section.upper():
            modified.update(requirements)
    return modified


def rename_map(text: str) -> dict[str, str]:
    """``{new name: old name}`` scanned over the whole delta document."""
    renames: dict[str, str] = {}
    lines = text.splitlines()
    mask = mask_fences(lines)
    in_renamed = False
    pending: str | None = None
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        header = SECTION_HEADER.match(line)
        if header:
            in_renamed = "RENAMED" in header.group(1).upper()
            pending = None
            continue
        if not in_renamed:
            continue
        from_match = RENAME_FROM.match(line)
        to_match = RENAME_TO.match(line)
        if from_match:
            pending = from_match.group(1).strip()
        elif to_match and pending:
            renames[to_match.group(1).strip()] = pending
            pending = None
    return renames


def split_scenarios(body: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split a requirement body into its prose preamble and ``#### Scenario`` blocks."""
    mask = mask_fences(body)
    prose: list[str] = []
    scenarios: list[tuple[str, list[str]]] = []
    current: list[str] | None = None

    for index, line in enumerate(body):
        if not mask[index]:
            header = SCENARIO_HEADER.match(line)
            if header:
                name = header.group(1)
                name = re.sub(r"[ \t]+#+[ \t]*$", "", name)
                name = re.sub(r"^Scenario:\s*", "", name, flags=re.IGNORECASE).strip()
                current = []
                scenarios.append((name, current))
                continue
        (current if current is not None else prose).append(line)

    return prose, scenarios


def clauses(lines: list[str]) -> list[str]:
    """Logical clauses of a body: one per bullet (with its wrapped continuations).

    Requirement bodies are written as WHEN/THEN/AND bullet chains wrapped at the
    line width, so the bullet -- not the line -- is the unit an author adds,
    edits, or drops. Non-bullet runs collapse into one clause per paragraph.
    """
    mask = mask_fences(lines)
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append(" ".join(current))
            current.clear()

    for index, line in enumerate(lines):
        if not line.strip():
            flush()
            continue
        if not mask[index] and BULLET.match(line):
            flush()
        current.append(line.strip())
    flush()

    normalized = [re.sub(r"\s+", " ", clause).strip() for clause in out]
    return [clause for clause in normalized if not IGNORABLE_CLAUSE.match(clause)]


def normalize_clause(clause: str) -> str:
    """Comparison form: whitespace collapsed, trailing sentence punctuation dropped."""
    return re.sub(r"\s+", " ", clause).strip().rstrip(".,;:").strip()


def digest_of(clause: str) -> str:
    return hashlib.sha256(normalize_clause(clause).encode("utf-8")).hexdigest()[:12]


def excerpt_of(clause: str) -> str:
    flat = normalize_clause(clause)
    return flat if len(flat) <= 120 else flat[:117] + "..."


def survival(baseline_clause: str, block_clause: str) -> tuple[float, float]:
    """``(covered, contiguous)`` fractions of ``baseline_clause`` found in ``block_clause``.

    ``covered`` is every character that still appears in order; ``contiguous`` is
    the single longest surviving run. ``autojunk`` is off: it suppresses
    characters appearing in more than 1% of a string over 200 characters, which
    on requirement prose means common letters, and that would understate how
    much of a long baseline clause survives.
    """
    if not baseline_clause:
        return (1.0, 1.0)
    matcher = difflib.SequenceMatcher(None, baseline_clause, block_clause, autojunk=False)
    blocks = matcher.get_matching_blocks()
    covered = sum(block.size for block in blocks) / len(baseline_clause)
    contiguous = max((block.size for block in blocks), default=0) / len(baseline_clause)
    return (covered, contiguous)


def dropped_clauses(baseline: list[str], block: list[str]) -> list[str]:
    """Baseline clauses with no recognizable counterpart in the block.

    Matching is global-greedy on surviving coverage: the best-scoring pair is
    committed first and both sides consumed, so one rewritten block clause
    cannot stand in for two distinct baseline clauses it happens to resemble.
    """
    baseline_clauses = [normalize_clause(clause) for clause in baseline]
    block_clauses = [normalize_clause(clause) for clause in block]

    scored: list[tuple[float, int, int]] = []
    for b_index, b_clause in enumerate(baseline_clauses):
        for m_index, m_clause in enumerate(block_clauses):
            covered, contiguous = survival(b_clause, m_clause)
            if covered >= COVERAGE_THRESHOLD and contiguous >= CONTIGUITY_THRESHOLD:
                scored.append((covered, b_index, m_index))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    matched_baseline: set[int] = set()
    matched_block: set[int] = set()
    for _ratio, b_index, m_index in scored:
        if b_index in matched_baseline or m_index in matched_block:
            continue
        matched_baseline.add(b_index)
        matched_block.add(m_index)

    return [
        baseline[index] for index in range(len(baseline_clauses)) if index not in matched_baseline
    ]


def find_losses(baseline_body: list[str], block_body: list[str]) -> list[Finding]:
    """Every unit of ``baseline_body`` that archiving ``block_body`` would delete."""
    baseline_prose, baseline_scenarios = split_scenarios(baseline_body)
    block_prose, block_scenarios = split_scenarios(block_body)

    findings: list[Finding] = []

    for clause in dropped_clauses(clauses(baseline_prose), clauses(block_prose)):
        findings.append(Finding("prose", None, digest_of(clause), excerpt_of(clause)))

    # Multiplicity-aware name pairing, matching OpenSpec's own guard: a name
    # present twice in the baseline and once in the block loses one instance.
    remaining: dict[str, list[list[str]]] = {}
    for name, body in block_scenarios:
        remaining.setdefault(name, []).append(body)

    for name, baseline_scenario in baseline_scenarios:
        candidates = remaining.get(name)
        if not candidates:
            findings.append(
                Finding("scenario", name, digest_of(name), f'"{name}" is not in the block')
            )
            continue
        block_scenario = candidates.pop(0)
        for clause in dropped_clauses(clauses(baseline_scenario), clauses(block_scenario)):
            findings.append(Finding("clause", name, digest_of(clause), excerpt_of(clause)))

    return findings


def collect(root: Path) -> tuple[dict[str, list[Finding]], list[str]]:
    """Scan every unarchived change; return ``{entry key: findings}`` plus skip notes."""
    specs_dir = root / "openspec" / "specs"
    changes_dir = root / "openspec" / "changes"

    baselines: dict[str, dict[str, list[str]]] = {}
    for spec_file in sorted(specs_dir.glob("*/spec.md")):
        baselines[spec_file.parent.name] = baseline_requirements(spec_file.read_text("utf-8"))

    found: dict[str, list[Finding]] = {}
    skipped: list[str] = []

    for change_dir in sorted(p for p in changes_dir.iterdir() if p.is_dir()):
        if change_dir.name == "archive":
            continue
        for delta_file in sorted(change_dir.glob("specs/*/spec.md")):
            spec = delta_file.parent.name
            text = delta_file.read_text("utf-8")
            renames = rename_map(text)
            for requirement, block_body in modified_requirements(text).items():
                baseline_name = renames.get(requirement, requirement)
                baseline_body = baselines.get(spec, {}).get(baseline_name)
                if baseline_body is None:
                    skipped.append(
                        f"{change_dir.name}/{spec}: MODIFIED "
                        f'"{requirement}" has no baseline requirement to overwrite'
                    )
                    continue
                findings = find_losses(baseline_body, block_body)
                if findings:
                    found[f"{change_dir.name}/{spec}/{requirement}"] = findings

    return found, skipped


def load_baseline(path: Path) -> dict[str, list[Finding]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text("utf-8"))
    return {key: [Finding.from_json(item) for item in items] for key, items in raw.items()}


def write_baseline(path: Path, found: dict[str, list[Finding]]) -> int:
    payload = {
        key: [finding.to_json() for finding in findings] for key, findings in sorted(found.items())
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return sum(len(findings) for findings in found.values())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail when an unarchived MODIFIED spec block would delete live baseline content."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Tree containing openspec/ to scan (default: repo root).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Ratchet file of already-known losses (default: %(default)s).",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Freeze the current losses into the ratchet file and exit 0.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Ignore the ratchet; any dropped baseline content fails.",
    )
    args = parser.parse_args()

    found, skipped = collect(args.root)

    if args.update_baseline:
        total = write_baseline(args.baseline, found)
        print(
            f"Wrote ratchet: {total} known loss(es) across {len(found)} "
            f"MODIFIED requirement(s) -> {args.baseline}"
        )
        return 0

    baseline = {} if args.strict else load_baseline(args.baseline)

    regressions: list[str] = []
    healed: list[str] = []

    for key, findings in sorted(found.items()):
        allowed = {finding.key() for finding in baseline.get(key, [])}
        new = [finding for finding in findings if finding.key() not in allowed]
        if new:
            regressions.append(key)
            change, spec, requirement = key.split("/", 2)
            print(f'\n{change} -> {spec} "{requirement}"')
            for finding in new:
                print(f"  {finding.describe()}")

    for key, findings in sorted(baseline.items()):
        current = {finding.key() for finding in found.get(key, [])}
        gone = [finding for finding in findings if finding.key() not in current]
        if gone and key not in regressions:
            healed.append(f"{key} ({len(gone)} fewer)")

    for note in skipped:
        print(f"note: {note}")

    if healed:
        print(
            "\nRatchet can be tightened (losses no longer present): "
            + ", ".join(healed)
            + "\n  Run: python3 scripts/check_spec_overwrites.py --update-baseline"
        )

    if regressions:
        total = sum(
            len([f for f in found[key] if f.key() not in {b.key() for b in baseline.get(key, [])}])
            for key in regressions
        )
        print(
            f"\n{total} unfrozen baseline loss(es) across {len(regressions)} MODIFIED "
            "requirement(s). `openspec archive` writes the whole requirement, so each one "
            "above would be deleted from the baseline. Rebuild the block on the current "
            "baseline body, or re-freeze with --update-baseline once you have confirmed "
            "the loss is intended."
        )
        return 1

    print(f"No unfrozen baseline losses across {len(found)} MODIFIED requirement(s) with debt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

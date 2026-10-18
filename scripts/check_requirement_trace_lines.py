#!/usr/bin/env python3
"""
check_requirement_trace_lines.py

Require ``ID:``, ``Source:`` and ``Scope:`` trace lines on every ADDED or MODIFIED requirement
block in an active OpenSpec change (``openspec/changes/<change>/specs/<capability>/spec.md``).

Why this exists
---------------
Requirements without identity are how duplicates and contradictions accumulate (the 2026-10-03
shape audit, ``docs/plans/2026-10-03-shape-condensation-plan.md``).  The condensation program
backfills IDs family by family; this gate stops new deltas from adding unidentified requirements
in the meantime.  Gating *deltas* covers every new canonical requirement, because canonical specs
only change by archiving a delta.

What it checks, per block under ``## ADDED Requirements`` / ``## MODIFIED Requirements``
(``## REMOVED`` and ``## RENAMED`` sections and ``openspec/changes/archive/`` are exempt):

* ``ID: REQ-<capability>-<NNN>`` present, with ``<capability>`` equal to the spec directory name;
* ``Source: <non-empty>`` present;
* ``Scope: v1-mandatory | v1-reserved | post-v1`` present;
* an ADDED ID is not minted twice within one capability across active changes, and does not
  already exist in the canonical ``openspec/specs/<capability>/spec.md``.

The line patterns are copied from the shared th-projects validator
(``spec-trace-check.py``); they are duplicated here because CI cannot see ``~/.dotfiles``.

Baseline ratchet
----------------
Today's unidentified blocks are frozen in ``requirement-trace-baseline.json``, keyed by
``change/capability/requirement`` and pinned to a sha256 of the normalized block.  The ratchet is
content-addressed, like ``check_spec_overwrites.py``: editing a frozen block changes its digest,
so it leaves the baseline and must carry the lines.  Entries whose block is gone (for example
because its change was archived) are reported as info and dropped by ``--write-baseline``.

Usage:
  python3 scripts/check_requirement_trace_lines.py                  # gate (ratchet)
  python3 scripts/check_requirement_trace_lines.py --write-baseline # freeze current debt
  python3 scripts/check_requirement_trace_lines.py --strict         # ignore the baseline
  python3 scripts/check_requirement_trace_lines.py --root <dir>     # scan another tree

Exit codes:
  0  Every ADDED/MODIFIED block is identified or frozen.
  1  At least one block lacks the trace lines (or its ID is wrong or duplicated).
  2  The scan matched no active change spec files (a silent pass would hide a broken path).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "requirement-trace-baseline.json"

SECTION_HEADER = re.compile(r"^##\s+(.+?)\s*$")
REQUIREMENT_HEADER = re.compile(r"^###\s+Requirement:\s*(.+?)\s*$")
ANY_H3 = re.compile(r"^###\s")
FENCE = re.compile(r"^\s*(```|~~~)")

# Copied from th-projects spec-trace-check.py:47-50.
ID_LINE = re.compile(r"^ID:\s*(\S+)\s*$")
SOURCE_LINE = re.compile(r"^Source:\s*\S")
SCOPE_LINE = re.compile(r"^Scope:\s*(v1-mandatory|v1-reserved|post-v1)\s*$")
ID_SHAPE = re.compile(r"^REQ-(?P<capability>.+)-(?P<number>\d+)$")


@dataclass
class Block:
    change: str
    capability: str
    name: str
    kind: str  # "ADDED" | "MODIFIED"
    path: Path
    line: int
    body: list[str]
    req_id: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.change}/{self.capability}/{self.name}"

    @property
    def digest(self) -> str:
        normalized = " ".join(
            " ".join(line.split()) for line in [self.name, *self.body] if line.strip()
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fence_mask(lines: list[str]) -> list[bool]:
    mask = [False] * len(lines)
    inside = False
    for index, line in enumerate(lines):
        if FENCE.match(line):
            mask[index] = True
            inside = not inside
        else:
            mask[index] = inside
    return mask


def parse_blocks(text: str) -> list[tuple[str, str, int, list[str]]]:
    """``(section kind, requirement name, 1-based header line, body lines)`` for ADDED/MODIFIED."""
    lines = text.splitlines()
    mask = _fence_mask(lines)
    blocks: list[tuple[str, str, int, list[str]]] = []
    kind: str | None = None
    current: tuple[str, str, int, list[str]] | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            blocks.append(current)
            current = None

    for index, line in enumerate(lines):
        if not mask[index]:
            section = SECTION_HEADER.match(line)
            if section and not line.startswith("###"):
                close()
                upper = section.group(1).upper()
                kind = "ADDED" if "ADDED" in upper else "MODIFIED" if "MODIFIED" in upper else None
                continue
            header = REQUIREMENT_HEADER.match(line)
            if header:
                close()
                if kind is not None:
                    current = (kind, header.group(1), index + 1, [])
                continue
            if ANY_H3.match(line):
                close()
                continue
        if current is not None:
            current[3].append(line)
    close()
    return blocks


def check_block(block: Block) -> None:
    """Fill ``req_id`` and ``problems`` from the block's trace lines (outside code fences)."""
    mask = _fence_mask(block.body)
    ids: list[str] = []
    has_source = has_scope = False
    for index, line in enumerate(block.body):
        if mask[index]:
            continue
        match = ID_LINE.match(line)
        if match:
            ids.append(match.group(1))
        has_source = has_source or bool(SOURCE_LINE.match(line))
        has_scope = has_scope or bool(SCOPE_LINE.match(line))
    if not ids:
        block.problems.append("missing ID")
    else:
        block.req_id = ids[0]
        shape = ID_SHAPE.match(ids[0])
        if shape is None:
            block.problems.append(f"ID {ids[0]!r} is not REQ-<capability>-<NNN>")
        elif shape.group("capability") != block.capability:
            block.problems.append(
                f"ID {ids[0]!r} names capability {shape.group('capability')!r}, "
                f"but this spec is {block.capability!r}"
            )
    if not has_source:
        block.problems.append("missing Source")
    if not has_scope:
        block.problems.append("missing Scope (v1-mandatory|v1-reserved|post-v1)")


def canonical_ids(root: Path, capability: str) -> set[str]:
    path = root / "openspec" / "specs" / capability / "spec.md"
    if not path.exists():
        return set()
    ids: set[str] = set()
    lines = path.read_text("utf-8").splitlines()
    mask = _fence_mask(lines)
    for index, line in enumerate(lines):
        if not mask[index]:
            match = ID_LINE.match(line)
            if match:
                ids.add(match.group(1))
    return ids


def collect(root: Path) -> tuple[list[Block], int]:
    """Every ADDED/MODIFIED block in active changes, checked; plus the number of files scanned."""
    changes_dir = root / "openspec" / "changes"
    blocks: list[Block] = []
    files = 0
    if not changes_dir.is_dir():
        return blocks, files
    for change_dir in sorted(p for p in changes_dir.iterdir() if p.is_dir()):
        if change_dir.name == "archive":
            continue
        for spec_path in sorted(change_dir.glob("specs/*/spec.md")):
            files += 1
            capability = spec_path.parent.name
            for kind, name, line, body in parse_blocks(spec_path.read_text("utf-8")):
                block = Block(change_dir.name, capability, name, kind, spec_path, line, body)
                check_block(block)
                blocks.append(block)

    minted: dict[tuple[str, str], list[Block]] = {}
    for block in blocks:
        if block.kind == "ADDED" and block.req_id:
            minted.setdefault((block.capability, block.req_id), []).append(block)
    for (capability, req_id), owners in minted.items():
        existing = canonical_ids(root, capability)
        for block in owners:
            if len(owners) > 1:
                others = ", ".join(sorted(o.change for o in owners if o is not block))
                block.problems.append(f"duplicate new ID {req_id} (also minted by {others})")
            if req_id in existing:
                block.problems.append(f"ID {req_id} already exists in openspec/specs/{capability}")
    return blocks, files


def load_baseline(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text("utf-8"))


def write_baseline(path: Path, blocks: list[Block]) -> int:
    payload = {block.key: block.digest for block in blocks if block.problems}
    path.write_text(
        json.dumps(dict(sorted(payload.items())), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return len(payload)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require ID/Source/Scope lines on ADDED/MODIFIED spec requirements."
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Tree containing openspec/.")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH, help="Ratchet file.")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Freeze the current unidentified blocks (and drop stale entries), then exit 0.",
    )
    parser.add_argument("--strict", action="store_true", help="Ignore the ratchet.")
    args = parser.parse_args()

    blocks, files = collect(args.root)
    if files == 0:
        print(
            f"error: no openspec/changes/*/specs/*/spec.md files found under {args.root}; "
            "refusing to pass on an empty scan",
            file=sys.stderr,
        )
        return 2

    if args.write_baseline:
        total = write_baseline(args.baseline, blocks)
        print(f"Wrote baseline: {total} unidentified block(s) -> {args.baseline}")
        return 0

    baseline = {} if args.strict else load_baseline(args.baseline)
    failures = [b for b in blocks if b.problems and baseline.get(b.key) != b.digest]
    for block in failures:
        rel = block.path.relative_to(args.root)
        print(f'{rel}:{block.line}: {block.kind} requirement "{block.name}"')
        for problem in block.problems:
            print(f"  {problem}")

    current = {b.key for b in blocks if b.problems}
    stale = sorted(key for key in baseline if key not in current)
    if stale:
        print(
            f"\nnote: {len(stale)} baseline entr{'y' if len(stale) == 1 else 'ies'} no longer "
            "match an unidentified block; run --write-baseline to drop them."
        )

    if failures:
        print(
            f"\n{len(failures)} requirement block(s) without a valid ID/Source/Scope trace. "
            "Add the three lines (ID: REQ-<capability>-<NNN>, Source: ..., Scope: "
            "v1-mandatory|v1-reserved|post-v1) to each block above."
        )
        return 1
    print(
        f"All {len(blocks)} ADDED/MODIFIED requirement block(s) carry trace lines or are frozen "
        f"({sum(1 for b in blocks if b.problems)} frozen)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

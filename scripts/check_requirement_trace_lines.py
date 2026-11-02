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
* an ID is not minted twice within one capability across active changes.  An ADDED block mints
  its ID, and so does a MODIFIED block whose ID the canonical spec lacks (backfilling an ID onto
  an existing requirement).  Blocks that share a requirement name may share a minted ID (a
  pending-parent sibling modifying what another change adds), but two ADDED blocks may not;
* an ADDED ID does not already exist in the canonical ``openspec/specs/<capability>/spec.md``,
  and a MODIFIED ID that the canonical spec gives to a different requirement name fails.
  Two further MODIFIED rules reverse-map the requirement name to its ID, so a block cannot
  re-mint an identity that already exists: a MODIFIED block whose requirement already has a
  canonical ID must carry that ID, and a MODIFIED block whose requirement another active change
  ADDs under a different ID must carry that ADDED ID.

The line patterns are copied from the shared th-projects validator
(``spec-trace-check.py``); they are duplicated here because CI cannot see ``~/.dotfiles``.  The
block reader (case-insensitive header, marker-and-length code fences, stray ``###`` headings kept
inside the block) matches OpenSpec's own parser, so no block ``openspec archive`` applies is hidden
from the gate.

Baseline ratchet
----------------
Today's unidentified blocks are frozen in ``requirement-trace-baseline.json``, keyed by
``change/capability/requirement`` and pinned to a sha256 of the normalized block.  The ratchet is
content-addressed, like ``check_spec_overwrites.py``: editing a frozen block changes its digest,
so it leaves the baseline and must carry the lines.  Entries whose block is gone (for example
because its change was archived) or now carries valid trace lines must be removed; stale entries
fail the gate. ``--write-baseline`` explicitly drops them when freezing current debt.

Usage:
  python3 scripts/check_requirement_trace_lines.py                  # gate (ratchet)
  python3 scripts/check_requirement_trace_lines.py --write-baseline # freeze current debt
  python3 scripts/check_requirement_trace_lines.py --strict         # ignore the baseline
  python3 scripts/check_requirement_trace_lines.py --root <dir>     # scan another tree

Exit codes:
  0  Every ADDED/MODIFIED block is identified or frozen.
  1  A block lacks valid trace lines, or the baseline contains stale entries.
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
# Parity with OpenSpec's own reader (dist/core/parsers/requirement-blocks.js, code-fence.js),
# which is what `openspec archive` applies: a block this guard cannot see could still be archived.
REQUIREMENT_HEADER = re.compile(r"^###\s*Requirement:\s*(.+?)\s*$", re.IGNORECASE)
FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
FENCE_CLOSE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")

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
        # The section kind is part of the digest: a frozen block moved between ADDED and
        # MODIFIED is a different block and must carry the lines.
        normalized = " ".join(
            " ".join(line.split()) for line in [self.kind, self.name, *self.body] if line.strip()
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fence_mask(lines: list[str]) -> list[bool]:
    """True for lines inside (or delimiting) a fenced code block, as OpenSpec computes it.

    A fence closes only on a bare fence of the same character that is at least as long as the
    opener, so a lone ``` inside a ~~~ fence does not flip the mask.
    """
    mask = [False] * len(lines)
    active: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        if active is None:
            opener = FENCE_OPEN.match(line)
            if opener:
                active = (opener.group(1)[0], len(opener.group(1)))
                mask[index] = True
            continue
        mask[index] = True
        closer = FENCE_CLOSE.match(line)
        if closer and closer.group(1)[0] == active[0] and len(closer.group(1)) >= active[1]:
            active = None
    return mask


def iter_blocks(text: str) -> list[tuple[str | None, str, int, list[str]]]:
    """``(section header, requirement name, 1-based header line, body lines)`` for every block.

    A block runs from its header to the next requirement header or ``## `` section header; a stray
    ``###`` heading stays inside the block, exactly as ``openspec archive`` treats it.
    """
    lines = text.splitlines()
    mask = _fence_mask(lines)
    blocks: list[tuple[str | None, str, int, list[str]]] = []
    section: str | None = None
    current: tuple[str | None, str, int, list[str]] | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            blocks.append(current)
            current = None

    for index, line in enumerate(lines):
        if not mask[index]:
            header = REQUIREMENT_HEADER.match(line)
            if header:
                close()
                current = (section, header.group(1), index + 1, [])
                continue
            heading = SECTION_HEADER.match(line)
            if heading and not line.startswith("###"):
                close()
                section = heading.group(1)
                continue
        if current is not None:
            current[3].append(line)
    close()
    return blocks


def parse_blocks(text: str) -> list[tuple[str, str, int, list[str]]]:
    """``(kind, name, line, body)`` for blocks under ADDED/MODIFIED headings only."""
    out: list[tuple[str, str, int, list[str]]] = []
    for section, name, line, body in iter_blocks(text):
        upper = (section or "").upper()
        kind = "ADDED" if "ADDED" in upper else "MODIFIED" if "MODIFIED" in upper else None
        if kind is not None:
            out.append((kind, name, line, body))
    return out


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


def canonical_ids(root: Path, capability: str) -> dict[str, set[str]]:
    """``{ID: {requirement names}}`` for the canonical spec of one capability."""
    path = root / "openspec" / "specs" / capability / "spec.md"
    ids: dict[str, set[str]] = {}
    if not path.exists():
        return ids
    for _section, name, _line, body in iter_blocks(path.read_text("utf-8")):
        mask = _fence_mask(body)
        for index, line in enumerate(body):
            match = None if mask[index] else ID_LINE.match(line)
            if match:
                ids.setdefault(match.group(1), set()).add(name)
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

    canonical: dict[str, dict[str, set[str]]] = {}
    minted: dict[tuple[str, str], list[Block]] = {}
    for block in blocks:
        if not block.req_id:
            continue
        existing = canonical.setdefault(block.capability, canonical_ids(root, block.capability))
        owners = existing.get(block.req_id)
        if block.kind == "ADDED":
            if owners:
                block.problems.append(
                    f"ID {block.req_id} already exists in openspec/specs/{block.capability}"
                )
            minted.setdefault((block.capability, block.req_id), []).append(block)
        elif owners is None:
            # A MODIFIED block with an ID the canonical spec lacks mints it, like an ADDED one.
            minted.setdefault((block.capability, block.req_id), []).append(block)
            held = sorted(i for i, names in existing.items() if block.name in names)
            if held:
                block.problems.append(
                    f"requirement {block.name!r} already has canonical ID {held[0]} in "
                    f"openspec/specs/{block.capability}; a MODIFIED block must carry it, "
                    f"not {block.req_id}"
                )
        elif block.name not in owners:
            block.problems.append(
                f"ID {block.req_id} belongs to requirement "
                f"{sorted(owners)[0]!r} in openspec/specs/{block.capability}"
            )
    pending: dict[tuple[str, str], dict[str, Block]] = {}
    for block in blocks:
        if block.kind == "ADDED" and block.req_id:
            pending.setdefault((block.capability, block.name), {})[block.req_id] = block
    for block in blocks:
        if block.kind != "MODIFIED" or not block.req_id:
            continue
        for other_id, other in pending.get((block.capability, block.name), {}).items():
            if other_id != block.req_id and other.change != block.change:
                block.problems.append(
                    f"requirement {block.name!r} is ADDED as {other_id} by {other.change}; "
                    f"a MODIFIED block must carry that ID, not {block.req_id}"
                )
    for (_capability, req_id), group in minted.items():
        names = {b.name for b in group}
        added = sum(1 for b in group if b.kind == "ADDED")
        if len(group) > 1 and (len(names) > 1 or added > 1):
            for block in group:
                others = ", ".join(sorted(o.change for o in group if o is not block))
                block.problems.append(f"duplicate new ID {req_id} (also minted by {others})")
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
            f"\nerror: {len(stale)} stale baseline entr{'y' if len(stale) == 1 else 'ies'} "
            "no longer match an unidentified block; remove these keys from the baseline."
        )
        for key in stale:
            print(f"  {key}")

    if failures:
        print(
            f"\n{len(failures)} requirement block(s) without a valid ID/Source/Scope trace. "
            "Add the three lines (ID: REQ-<capability>-<NNN>, Source: ..., Scope: "
            "v1-mandatory|v1-reserved|post-v1) to each block above."
        )
        return 1
    if stale:
        return 1
    print(
        f"All {len(blocks)} ADDED/MODIFIED requirement block(s) carry trace lines or are frozen "
        f"({sum(1 for b in blocks if b.problems)} frozen)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

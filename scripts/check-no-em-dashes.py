#!/usr/bin/env python3
"""
check-no-em-dashes.py

Walk doctrine + dashboard-copy markdown files and report any em-dash (—) found
outside code blocks (fenced or inline). Enforces non-negotiable #6 ("no
em-dashes in doctrine or dashboard copy") so violations cannot re-enter.

Scope (see DEFAULT_GLOBS):
  about/heart-and-soul/**/*.md   about/lay-and-land/**/*.md
  about/craft-and-care/**/*.md   (doctrine essays)
  roster/*/MANIFESTO.md          (public-facing butler identity)
  roster/*/AGENTS.md             (runtime agent notes)

Frontend user-facing copy is enforced separately by an eslint rule
(``no-restricted-syntax`` on ``JSXText``) so code comments / non-UI strings are
never flagged -- see frontend/eslint.config.js.

Baseline ratchet
----------------
The full doctrine corpus carries a large volume of pre-existing violations
(cleanup is tracked separately: about/ under bu-f5wryw, roster doctrine under a
follow-up). Fixing all of them is out of scope for wiring the gate, and a hard
gate over dirty files would be red on day one. Instead this checker freezes the
current per-file count in ``scripts/em-dash-baseline.json`` and fails only when
a file exceeds its baseline (a *new* em-dash) or a file with no baseline entry
gains any em-dash (a *new* dirty file). Existing debt is frozen and can only
ratchet down; new debt is blocked. Regenerate the baseline (e.g. after a
cleanup) with ``--update-baseline``.

Usage:
  python3 scripts/check-no-em-dashes.py                   # gate (ratchet)
  python3 scripts/check-no-em-dashes.py --update-baseline # freeze current state
  python3 scripts/check-no-em-dashes.py --strict          # ignore baseline; any
                                                          # em-dash fails

Exit codes:
  0  No net-new em-dashes (or clean under --strict).
  1  One or more files exceed their baseline (or any em-dash under --strict).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "em-dash-baseline.json"

# Glob patterns (relative to repo root). ``**`` recurses; ``roster/*/NAME.md``
# scopes the roster corpus to exactly the two doctrine filenames, never
# CLAUDE.md / SKILL.md / other roster markdown.
DEFAULT_GLOBS: list[str] = [
    "about/heart-and-soul/**/*.md",
    "about/lay-and-land/**/*.md",
    "about/craft-and-care/**/*.md",
    "roster/*/MANIFESTO.md",
    "roster/*/AGENTS.md",
]


def find_em_dashes(filepath: Path) -> list[tuple[int, str]]:
    """Return (lineno, line_text) for lines with em-dashes outside code blocks."""
    code_fence_pattern = re.compile(r"^\s*```")
    inline_code_pattern = re.compile(r"`[^`\n]*`")

    violations: list[tuple[int, str]] = []
    in_code_fence = False

    with filepath.open(encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.rstrip("\n")

            # Track fenced code blocks
            if code_fence_pattern.match(line):
                in_code_fence = not in_code_fence
                continue

            # Skip lines inside fenced code blocks
            if in_code_fence:
                continue

            # Remove inline code spans before checking
            line_without_inline = inline_code_pattern.sub("", line)

            if "—" in line_without_inline:
                violations.append((lineno, line))

    return violations


def collect(globs: list[str]) -> dict[str, list[tuple[int, str]]]:
    """Map repo-relative POSIX path -> violations, for every matched file."""
    results: dict[str, list[tuple[int, str]]] = {}
    seen: set[Path] = set()
    for pattern in globs:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            rel = path.relative_to(REPO_ROOT).as_posix()
            results[rel] = find_em_dashes(path)
    return results


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    with BASELINE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check for em-dashes in doctrine + dashboard copy."
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=DEFAULT_GLOBS,
        help="Glob patterns to scan, relative to repo root (default: %(default)s)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Freeze the current per-file violation count into em-dash-baseline.json and exit 0.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Ignore the baseline; any em-dash outside code blocks is a violation.",
    )
    args = parser.parse_args()

    found = collect(args.paths)

    if args.update_baseline:
        baseline = {rel: len(v) for rel, v in found.items() if v}
        with BASELINE_PATH.open("w", encoding="utf-8") as f:
            json.dump(dict(sorted(baseline.items())), f, indent=2, ensure_ascii=False)
            f.write("\n")
        total = sum(baseline.values())
        print(
            f"Wrote baseline: {total} em-dash(es) across {len(baseline)} file(s) "
            f"-> {BASELINE_PATH.name}"
        )
        return 0

    baseline = {} if args.strict else load_baseline()

    regressions: list[str] = []
    improved: list[str] = []

    for rel, violations in sorted(found.items()):
        current = len(violations)
        allowed = baseline.get(rel, 0)
        if current > allowed:
            regressions.append(rel)
            print(f"\n{rel}: {current} em-dash(es), baseline allows {allowed}")
            for lineno, text in violations:
                print(f"  line {lineno}: {text[:100]}")
        elif current < allowed:
            improved.append(f"{rel} ({allowed} -> {current})")

    if improved:
        print(
            "\nBaseline can be lowered (fewer em-dashes than recorded): "
            + ", ".join(improved)
            + "\n  Run: python3 scripts/check-no-em-dashes.py --update-baseline"
        )

    if regressions:
        net_new = sum(len(found[r]) - baseline.get(r, 0) for r in regressions)
        print(
            f"\n{net_new} net-new em-dash(es) across {len(regressions)} file(s). "
            "Replace with commas, colons, or parentheses per non-negotiable #6."
        )
        return 1

    print("No net-new em-dashes outside code blocks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
check-no-em-dashes.py

Walk markdown files in the doctrine directories and report any em-dash (—)
found outside code blocks (fenced or inline). Exits 0 when clean.

Directories checked:
  about/heart-and-soul/
  about/lay-and-land/
  about/craft-and-care/

Usage:
  python3 scripts/check-no-em-dashes.py            # check only

Exit codes:
  0  No violations found.
  1  One or more em-dashes found outside code blocks.
"""

import argparse
import re
import sys
from pathlib import Path


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Check for em-dashes in doctrine docs.")
    parser.add_argument(
        "--paths",
        nargs="*",
        default=["about/heart-and-soul", "about/lay-and-land", "about/craft-and-care"],
        help="Directories to scan (default: %(default)s)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    total_violations = 0
    files_with_violations = 0

    for directory in args.paths:
        search_root = repo_root / directory
        if not search_root.exists():
            # Silently skip directories that don't exist yet
            continue

        for md_file in sorted(search_root.rglob("*.md")):
            violations = find_em_dashes(md_file)
            if violations:
                files_with_violations += 1
                total_violations += len(violations)
                rel = md_file.relative_to(repo_root)
                print(f"\n{rel}:")
                for lineno, text in violations:
                    print(f"  line {lineno}: {text[:100]}")

    if total_violations == 0:
        print("No em-dashes found outside code blocks.")
        return 0

    print(
        f"\n{total_violations} em-dash(es) found in {files_with_violations} file(s). "
        "Replace with commas, colons, or parentheses per doctrine."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

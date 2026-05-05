#!/usr/bin/env python3
"""
Extract user-facing strings from TSX files in frontend/src/pages and
frontend/src/components, and output a Markdown inventory grouped by file.

Extracts strings from:
- JSX text nodes  (>Some text<)
- Attributes: title, description, placeholder, alt, aria-label, aria-describedby,
  label, tooltip, emptyMessage

Skips:
- Single-character strings
- Pure numeric strings
- CSS class-like strings (contain only alphanumeric, hyphens, underscores, dots, slashes)
- Import paths, URLs
- Very short purely-technical tokens (e.g. tab IDs like "overview")
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

SCAN_DIRS = [
    FRONTEND_SRC / "pages",
    FRONTEND_SRC / "components",
]

# Attributes whose string-literal values are user-facing
USER_FACING_ATTRS = {
    "title",
    "description",
    "placeholder",
    "alt",
    "aria-label",
    "aria-describedby",
    "label",
    "tooltip",
    "emptyMessage",
    "noResultsMessage",
    "loadingMessage",
}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# JSX text nodes: content between > and < that isn't whitespace-only
JSX_TEXT_RE = re.compile(r">\s*([^<>{}\n]+?)\s*<", re.MULTILINE)

# Attribute patterns: attrName="value" or attrName={'value'} or attrName={"value"}
ATTR_DOUBLE_QUOTE_RE = re.compile(
    r'\b({attrs})\s*=\s*"([^"{{}}]+)"'.format(
        attrs="|".join(re.escape(a) for a in USER_FACING_ATTRS)
    )
)
ATTR_SINGLE_BRACE_RE = re.compile(
    r"\b({attrs})\s*=\s*\{{\'([^\'{{}}]+)\'\}}".format(
        attrs="|".join(re.escape(a) for a in USER_FACING_ATTRS)
    )
)
ATTR_DOUBLE_BRACE_RE = re.compile(
    r'\b({attrs})\s*=\s*\{{"([^"{{}}]+)"\}}'.format(
        attrs="|".join(re.escape(a) for a in USER_FACING_ATTRS)
    )
)

# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------

# Looks like a CSS class, ID, or technical token
CSS_CLASS_RE = re.compile(r"^[\w\-\.\/]+$")
# Looks like a URL or import path
URL_RE = re.compile(r"(https?://|/[\w\-]+/|@/|\.\.?/)")
# Pure number
NUMBER_RE = re.compile(r"^\d+(\.\d+)?$")

# JSX expression / ternary fragments that bled through
JSX_EXPR_RE = re.compile(r"[?:]\s*\(|&&\s*\(|\|\||\{[^}]+\}|\?[^:]+:")

# Tab id-like short identifiers (single lowercase word, ≤15 chars, no spaces)
TECHNICAL_TOKEN_RE = re.compile(r"^[a-z][a-z0-9\-_]{0,14}$")


def is_user_facing(s: str) -> bool:
    """Return True if the string looks like user-facing copy."""
    s = s.strip()
    if len(s) <= 1:
        return False
    if NUMBER_RE.match(s):
        return False
    if URL_RE.search(s):
        return False
    # Must contain at least one letter
    if not re.search(r"[A-Za-z]", s):
        return False
    # Strings with no spaces and no uppercase that look like IDs/CSS tokens
    if " " not in s and CSS_CLASS_RE.match(s) and s == s.lower():
        # Allow short capitalised words (e.g. "Draft") but reject "tabulator-row"
        return False
    # Ternary or JSX expression fragments (e.g. ") : isLoading ? (")
    if JSX_EXPR_RE.search(s):
        return False
    # Starts or ends with JSX punctuation characters
    if s.startswith(")") or s.endswith("(") or s.startswith("{") or s.endswith("}"):
        return False
    return True


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_strings_from_file(path: Path) -> list[str]:
    """Return deduplicated user-facing strings extracted from a TSX file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    found: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = s.strip()
        if s and s not in seen and is_user_facing(s):
            seen.add(s)
            found.append(s)

    # JSX text nodes
    for m in JSX_TEXT_RE.finditer(text):
        add(m.group(1))

    # Attribute values
    for pattern in (ATTR_DOUBLE_QUOTE_RE, ATTR_SINGLE_BRACE_RE, ATTR_DOUBLE_BRACE_RE):
        for m in pattern.finditer(text):
            add(m.group(2))

    return found


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def collect_tsx_files(dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        files.extend(sorted(d.rglob("*.tsx")))
    return files


def generate_report(files: list[Path]) -> tuple[str, int]:
    lines: list[str] = [
        "# Frontend Copy Inventory",
        "",
        "Auto-generated by `scripts/extract-frontend-copy.py`.",
        "Do **not** edit manually: re-run the script to refresh.",
        "",
    ]

    total = 0

    for path in files:
        # Skip test files — they don't contain production UI copy
        if path.stem.endswith(".test") or path.stem.endswith(".spec"):
            continue

        strings = extract_strings_from_file(path)
        if not strings:
            continue

        rel = relative_path(path)
        lines.append(f"## `{rel}`")
        lines.append("")
        for s in strings:
            lines.append(f"- {s}")
            total += 1
        lines.append("")

    lines.append("---")
    lines.append(f"*Total strings: {total}*")
    lines.append("")

    return "\n".join(lines), total


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    files = collect_tsx_files(SCAN_DIRS)

    if not files:
        print("ERROR: No TSX files found. Check SCAN_DIRS.", file=sys.stderr)
        sys.exit(1)

    report, total = generate_report(files)

    out_path = REPO_ROOT / "about" / "lay-and-land" / "frontend-copy-inventory.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"Files scanned : {len(files)}")
    print(f"Total strings : {total}")


if __name__ == "__main__":
    main()

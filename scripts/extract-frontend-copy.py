#!/usr/bin/env python3
"""
Extract user-facing strings from TSX files in frontend/src/pages and
frontend/src/components, and output a Markdown inventory grouped by file.

Copy is collected from a bounded set of *anchors* -- places where a string is
user-facing by construction:

- JSX text nodes  (>Some text<)
- Values of user-facing attributes: title, description, placeholder, alt,
  aria-label, aria-describedby, label, tooltip, emptyMessage, ...
- Arguments to display calls: toast.*, confirm, alert

Attribute values and call arguments are scanned as JavaScript, so copy assembled
in an expression -- a template literal, a ternary, a concatenation -- is
collected too (bu-5n509). Within an anchor only literals at the top nesting
level are taken, which keeps lookup keys and option-bag values (`t("some.key")`,
`{ id: "verify-all" }`) out. Interpolated expressions render as `{}`.

Anchoring, rather than sweeping every string in the file, is what keeps class
names, route paths, query keys and test ids out of the inventory: `className` is
simply not an anchor.

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

# Attributes whose values are user-facing
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

# Calls whose arguments are shown to the user
DISPLAY_CALLS = [
    r"toast\.(?:success|error|warning|info|message|loading)",
    r"toast",
    r"window\.confirm",
    r"window\.alert",
    r"confirm",
    r"alert",
]

# Stands in for an interpolated expression: `Verified ${ok}/${total} models`
# inventories as "Verified {}/{} models". The expression itself is code, not
# copy, and rendering it would churn the inventory on every variable rename.
PLACEHOLDER = "{}"

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# JSX text nodes: content between > and < that isn't whitespace-only
JSX_TEXT_RE = re.compile(r">\s*([^<>{}\n]+?)\s*<", re.MULTILINE)

# Anchor: `attr=` -- the value that follows is scanned as JavaScript
ATTR_ANCHOR_RE = re.compile(
    r"\b({attrs})\s*=\s*".format(attrs="|".join(re.escape(a) for a in sorted(USER_FACING_ATTRS)))
)

# Anchor: `toast.error(` -- the argument list that follows is scanned as JavaScript
CALL_ANCHOR_RE = re.compile(r"\b(?:{calls})\s*\(".format(calls="|".join(DISPLAY_CALLS)))

# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------

# Looks like a CSS class, ID, or technical token
CSS_CLASS_RE = re.compile(r"^[\w\-\.\/]+$")
# Looks like a URL or import path
URL_RE = re.compile(r"(https?://|/[\w\-]+/|@/|\.\.?/)")
# Pure number
NUMBER_RE = re.compile(r"^\d+(\.\d+)?$")
# Contains a letter
LETTER_RE = re.compile(r"[A-Za-z]")

# JSX expression / ternary fragments that bled through
JSX_EXPR_RE = re.compile(r"[?:]\s*\(|&&\s*\(|\|\||\{[^}]+\}|\?[^:]+:")

# Tab id-like short identifiers (single lowercase word, ≤15 chars, no spaces)
TECHNICAL_TOKEN_RE = re.compile(r"^[a-z][a-z0-9\-_]{0,14}$")


def is_user_facing(s: str) -> bool:
    """Return True if the string looks like user-facing copy."""
    s = s.strip()
    if PLACEHOLDER in s:
        # Assembled copy. The anchor already established that it is displayed, and
        # the shape checks below -- which read a brace or a bare lowercase token as
        # a leaked code fragment -- misjudge a string that is meant to have holes
        # in it. Ask only that something is left once the holes are removed.
        probe = s.replace(PLACEHOLDER, " ").strip()
        return len(probe) > 1 and bool(LETTER_RE.search(probe)) and not URL_RE.search(probe)
    if len(s) <= 1:
        return False
    if NUMBER_RE.match(s):
        return False
    if URL_RE.search(s):
        return False
    # Must contain at least one letter
    if not LETTER_RE.search(s):
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
# JavaScript literal scanning
#
# Just enough of a scanner to walk an attribute value or an argument list and
# pull out its string and template literals. Not a parser: it tracks nesting and
# skips comments, and that is all an anchored region needs.
# ---------------------------------------------------------------------------

_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "", "f": "", "0": ""}
_OPENERS = "([{"
_CLOSERS = ")]}"


def _scan_string(text: str, i: int) -> tuple[str, int]:
    """Scan the quoted string starting at text[i]. Return (value, index just past it)."""
    quote = text[i]
    i += 1
    out: list[str] = []
    while i < len(text):
        c = text[i]
        if c == "\\":
            nxt = text[i + 1 : i + 2]
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if c == quote:
            return "".join(out), i + 1
        if c == "\n":  # unterminated -- do not run off the end of the file
            return "".join(out), i
        out.append(c)
        i += 1
    return "".join(out), i


def _scan_template(text: str, i: int, nested: list[str] | None) -> tuple[str, int]:
    """Scan the template literal starting at text[i] (a backtick).

    Return (value with each ${...} rendered as PLACEHOLDER, index just past it).
    When `nested` is given, literals inside the interpolations are appended to it:
    an interpolation of a copy string is itself a copy site, which is how the
    conditional tail of `Verified ${n} models${failed ? ` · ${failed} failed` : ""}`
    gets collected.
    """
    i += 1
    parts: list[str] = []
    while i < len(text):
        c = text[i]
        if c == "\\":
            nxt = text[i + 1 : i + 2]
            parts.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if c == "`":
            return "".join(parts), i + 1
        if c == "$" and text[i + 1 : i + 2] == "{":
            i = _walk(text, i + 2, "}", nested)
            parts.append(PLACEHOLDER)
            continue
        parts.append(c)
        i += 1
    return "".join(parts), i


def _walk(text: str, i: int, stop: str, collected: list[str] | None) -> int:
    """Scan forward from i until `stop` appears at nesting depth 0.

    Return the index just past it. When `collected` is given, append the value of
    every string and template literal found *at depth 0* -- the depth rule is what
    separates copy (`toast.error("Save failed")`) from the code around it
    (`toast.error(t("errors.save"), { id: "save" })`).
    """
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            value, i = _scan_string(text, i)
            if collected is not None and depth == 0:
                collected.append(value)
            continue
        if c == "`":
            take = collected is not None and depth == 0
            nested: list[str] = []
            value, i = _scan_template(text, i, nested if take else None)
            if take:
                collected.append(value)
                collected.extend(nested)
            continue
        if c == "/" and text[i + 1 : i + 2] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and text[i + 1 : i + 2] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == stop and depth == 0:
            return i + 1
        if c in _OPENERS:
            depth += 1
        elif c in _CLOSERS:
            depth -= 1
            if depth < 0:  # region closed by an unmatched bracket
                return i + 1
        i += 1
    return n


def _collect_anchored_value(text: str, i: int) -> list[str]:
    """Collect the literals of the attribute value or argument list starting at i."""
    if i >= len(text):
        return []
    collected: list[str] = []
    if text[i] in "\"'":
        value, _ = _scan_string(text, i)
        collected.append(value)
    elif text[i] == "{":
        _walk(text, i + 1, "}", collected)
    return collected


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
        # An inventory entry is a single Markdown list item, so a multi-line
        # template literal has to collapse to one line.
        s = " ".join(s.split())
        if s and s not in seen and is_user_facing(s):
            seen.add(s)
            found.append(s)

    # JSX text nodes
    for m in JSX_TEXT_RE.finditer(text):
        add(m.group(1))

    # User-facing attribute values
    for m in ATTR_ANCHOR_RE.finditer(text):
        for s in _collect_anchored_value(text, m.end()):
            add(s)

    # Display-call arguments
    for m in CALL_ANCHOR_RE.finditer(text):
        collected: list[str] = []
        _walk(text, m.end(), ")", collected)
        for s in collected:
            add(s)

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
        "Do **not** edit manually. Regenerate (no args) with:",
        "`python3 scripts/extract-frontend-copy.py`",
        "",
        "CI job `frontend-copy-inventory-guard` regenerates this file and fails the",
        "build if the committed copy differs, so it is always current.",
        "",
        "## Scope",
        "",
        "The generator reads `.tsx` sources as text, so it sees copy in a bounded set of",
        "places and nowhere else. It collects:",
        "",
        "- JSX text nodes -- `<span>Save changes</span>`",
        "- Values of user-facing attributes -- `title`, `description`, `placeholder`,",
        "  `alt`, `aria-label`, `label`, `tooltip`, `emptyMessage`, ...",
        "- Arguments to display calls -- `toast.*`, `confirm`, `alert`",
        "",
        "Attribute values and call arguments are scanned as JavaScript, so template",
        "literals and ternary branches are collected. An interpolated expression renders",
        "as `{}`: `Verified {}/{} models` is one string with two runtime holes. Only",
        "literals at the top nesting level of a value or argument list count, which is",
        'what keeps lookup keys and option bags (`t("errors.save")`, `{ id: "toast-1" }`)',
        "out of the list.",
        "",
        "**Not covered**, so absence from this file is not evidence the UI never shows a",
        "string: copy built into a local variable or returned by a helper or hook before",
        "reaching a display site; copy passed through a prop that is not on the attribute",
        "list above; copy that originates in the backend; and anything outside `.tsx`",
        "files under `frontend/src/pages` and `frontend/src/components`.",
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

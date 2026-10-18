"""Frontend test-case deletions need an explicit successor mapping (bu-f9tmbm).

The Python condensation ledger counts pytest definitions only, so a PR that deletes
or weakens a vitest/node:test/Playwright case printed ``losses: 0`` without checking
anything.  This module is the frontend half of that guard.  It reads every
``frontend/**/*.{test,spec}.{ts,tsx,mjs}`` file at the exact base and in the working
tree, derives case ids ``<path>::<describe> > <it>`` and treats each id whose live
count dropped as a loss.  A loss is excused only by an entry in a mapping file that
this change itself adds under ``tests/frontend-test-deletions/``, naming live
successor ids and a reason.  Mapping files already on the base excuse nothing, so a
later deletion is never hidden by an old entry.

Weakened cases count as losses too: ``.skip`` and ``.todo`` cases are not live, and
a commented-out case is invisible to the scanner.  A case whose ``.each`` table
loses a row is not seen (the id is the title); the review of the table stays manual.

The scanner is a small lexer (comments, strings, templates and regex literals are
skipped, parentheses are balanced), not a TypeScript parser.  Cases whose title is
not a literal are recorded under ``<dynamic>`` and therefore still counted.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath

MAPPING_DIRECTORY = "tests/frontend-test-deletions"
_CASE_FILE = re.compile(r"^frontend/.+\.(test|spec)\.(ts|tsx|mjs)$")
_CALLS = {"describe", "it", "test"}
_NOT_LIVE = {"skip", "todo"}
_REGEX_PRECEDERS = {"(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "return", "=>"}


class FrontendDeletionError(Exception):
    """Raised with a bounded category; the ledger entry point reports it as REFUSED."""


def is_case_file(path: str) -> bool:
    return bool(_CASE_FILE.match(path)) and "node_modules" not in PurePosixPath(path).parts


def tokenize(source: str) -> list[tuple[str, str]]:
    """Return ``(kind, text)`` tokens: ident, str, punct. Comments and regexes are dropped."""
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(source)

    def previous_allows_regex() -> bool:
        if not tokens:
            return True
        kind, text = tokens[-1]
        return text in _REGEX_PRECEDERS if kind in {"punct", "ident"} else False

    while i < n:
        c = source[i]
        if c in " \t\r\n":
            i += 1
        elif source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j < 0 else j
        elif source.startswith("/*", i):
            j = source.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "'\"":
            j, parts = i + 1, []
            while j < n and source[j] != c:
                if source[j] == "\\" and j + 1 < n:
                    parts.append(source[j : j + 2])
                    j += 2
                else:
                    parts.append(source[j])
                    j += 1
            tokens.append(("str", "".join(parts)))
            i = j + 1
        elif c == "`":
            j, depth, parts = i + 1, 0, []
            while j < n and not (source[j] == "`" and depth == 0):
                if source.startswith("${", j):
                    depth += 1
                    parts.append("${")
                    j += 2
                    continue
                if source[j] == "}" and depth:
                    depth -= 1
                if source[j] == "\\" and j + 1 < n:
                    parts.append(source[j : j + 2])
                    j += 2
                    continue
                parts.append(source[j])
                j += 1
            tokens.append(("str", "".join(parts)))
            i = j + 1
        elif c == "/" and previous_allows_regex():
            j, in_class = i + 1, False
            while j < n and (source[j] != "/" or in_class) and source[j] != "\n":
                if source[j] == "\\":
                    j += 1
                elif source[j] == "[":
                    in_class = True
                elif source[j] == "]":
                    in_class = False
                j += 1
            i = j + 1
        elif c.isalpha() or c in "_$":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            tokens.append(("ident", source[i:j]))
            i = j
        elif c.isdigit():
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] in "._"):
                j += 1
            tokens.append(("num", source[i:j]))
            i = j
        else:
            tokens.append(("punct", c))
            i += 1
    return tokens


def _skip_group(tokens: list[tuple[str, str]], start: int) -> int:
    """Return the index after the bracket group opening at ``start``."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = [pairs[tokens[start][1]]]
    i = start + 1
    while i < len(tokens) and stack:
        kind, text = tokens[i]
        if kind == "punct":
            if text in pairs:
                stack.append(pairs[text])
            elif text == stack[-1]:
                stack.pop()
        i += 1
    return i


def case_ids(path: str, source: str) -> Counter:
    """Count live case ids; ``.skip``/``.todo`` cases are not live."""
    tokens = tokenize(source)
    found: Counter = Counter()
    contexts: list[tuple[str, int]] = []  # (describe title, paren depth that closes it)
    depth, i = 0, 0
    while i < len(tokens):
        kind, text = tokens[i]
        if kind == "punct" and text == "(":
            depth += 1
        elif kind == "punct" and text == ")":
            depth -= 1
            while contexts and contexts[-1][1] > depth:
                contexts.pop()
        elif kind == "ident" and text in _CALLS and not (i and tokens[i - 1][1] == "."):
            j, modifiers = i + 1, []
            while j + 1 < len(tokens) and tokens[j][1] == "." and tokens[j + 1][0] == "ident":
                modifiers.append(tokens[j + 1][1])
                j += 2
                if modifiers[-1] == "each" and j < len(tokens) and tokens[j][1] in "([":
                    j = _skip_group(tokens, j)
                elif modifiers[-1] == "each" and j < len(tokens) and tokens[j][0] == "str":
                    j += 1  # tagged-template table: describe.each`...`('title', fn)
            if j < len(tokens) and tokens[j][1] == "(":
                title = (
                    tokens[j + 1][1] if j + 1 < len(tokens) and tokens[j + 1][0] == "str" else None
                )
                title = "<dynamic>" if title is None else title
                live = not (set(modifiers) & _NOT_LIVE)
                chain = " > ".join([*(t for t, _ in contexts), title])
                if text == "describe":
                    if live:
                        contexts.append((title, depth + 1))
                else:
                    if live:
                        found[f"{path}::{chain}"] += 1
        i += 1
    return found


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError as exc:
        raise FrontendDeletionError("frontend-git-source-unavailable") from exc


def base_cases(root: Path, base: str) -> Counter:
    result: Counter = Counter()
    for path in _git(root, "ls-tree", "-r", "--name-only", base, "--", "frontend").splitlines():
        if is_case_file(path):
            source = _git(root, "show", f"{base}:{path}")
            result.update(case_ids(path, source))
    return result


def current_cases(root: Path) -> Counter:
    result: Counter = Counter()
    for path in _git(root, "ls-files", "-co", "--exclude-standard", "--", "frontend").splitlines():
        file = root / path
        if is_case_file(path) and file.is_file():
            result.update(case_ids(path, file.read_text(encoding="utf-8")))
    return result


def new_mappings(root: Path, base: str) -> dict[str, dict]:
    """Mapping files this change adds; files already on the base excuse nothing."""
    directory = root / MAPPING_DIRECTORY
    if not directory.is_dir():
        return {}
    on_base = set(
        _git(root, "ls-tree", "-r", "--name-only", base, "--", MAPPING_DIRECTORY).splitlines()
    )
    mappings: dict[str, dict] = {}
    for file in sorted(directory.glob("*.json")):
        relative = f"{MAPPING_DIRECTORY}/{file.name}"
        if relative in on_base:
            continue
        try:
            body = json.loads(file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise FrontendDeletionError("invalid-frontend-deletion-mapping") from exc
        if (
            not isinstance(body, dict)
            or set(body) != {"deleted"}
            or not isinstance(body["deleted"], dict)
        ):
            raise FrontendDeletionError("invalid-frontend-deletion-mapping")
        for case, row in body["deleted"].items():
            if (
                not isinstance(row, dict)
                or set(row) != {"successors", "reason"}
                or not isinstance(row["reason"], str)
                or not row["reason"].strip()
                or not isinstance(row["successors"], list)
                or not row["successors"]
                or any(not isinstance(s, str) for s in row["successors"])
            ):
                raise FrontendDeletionError("invalid-frontend-deletion-mapping")
            if case in mappings:
                raise FrontendDeletionError("duplicate-frontend-deletion-mapping")
            mappings[case] = row
    return mappings


def check(root: Path, base: str) -> None:
    """Refuse a frontend case loss that has no successor mapping added by this change."""
    before, now = base_cases(root, base), current_cases(root)
    lost = {case for case, count in before.items() if now[case] < count}
    mappings = new_mappings(root, base)
    if set(mappings) - lost:
        raise FrontendDeletionError("frontend-deletion-mapping-for-a-case-not-lost")
    if lost - set(mappings):
        raise FrontendDeletionError("frontend-test-deleted-without-successor-mapping")
    for case in lost:
        for successor in mappings[case]["successors"]:
            if successor in lost or now[successor] < 1 or successor == case:
                raise FrontendDeletionError("frontend-deletion-successor-not-live")

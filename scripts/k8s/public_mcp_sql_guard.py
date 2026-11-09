"""Refuse any psql meta-command the public MCP owner-step file does not need (RFC 0039 D3).

Usage: python3 -I scripts/k8s/public_mcp_sql_guard.py < owner-step.sql

The owner step runs the file through psql as the CNPG superuser, and psql meta-commands reach a
shell on the primary (`\\!`, backticks in `\\set` or `\\if`, `\\o |cmd`, `\\g |cmd`, `\\copy ...
program`). psql starts a meta-command at any backslash outside a quoted string, dollar quote or
comment, so this scans the file the way psql's lexer does and admits a backslash only:

- inside a standard '...' string or a $tag$...$tag$ dollar quote (data the server receives), or
- at column 0 of a line that is exactly one of the forms the renderer emits:
  `\\set NAME WORD`, `\\connect butlers`, `\\connect butlers_public`, `\\gset`, `\\if :name`,
  `\\else`, `\\endif`.

Anything else is refused: a backtick anywhere, an escape string (E'...'), a backslash in a
comment or a quoted identifier, an unterminated quote or comment, and an empty file. Stdlib
only; it reads stdin and prints only the refused line number and form, never the file.
"""

from __future__ import annotations

import re
import sys

_ALLOWED = re.compile(
    r"\\set [A-Za-z_][A-Za-z0-9_]* [A-Za-z0-9_]+"
    r"|\\connect butlers|\\connect butlers_public"
    r"|\\gset|\\if :[a-z_][a-z0-9_]*|\\else|\\endif"
)
_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_$]")


class Refused(Exception):
    def __init__(self, line: int, reason: str) -> None:
        super().__init__(f"line {line}: {reason}")


def check(text: str) -> None:
    if not text.strip():
        raise Refused(1, "empty file")
    if "`" in text:
        raise Refused(text[: text.index("`")].count("\n") + 1, "backtick")
    i, n = 0, len(text)

    def line() -> int:
        return text.count("\n", 0, i) + 1

    while i < n:
        c = text[i]
        at_line_start = i == 0 or text[i - 1] == "\n"
        if c == "\\":
            if not at_line_start:
                raise Refused(line(), "meta-command outside column 0")
            end = text.find("\n", i)
            end = n if end < 0 else end
            if not _ALLOWED.fullmatch(text[i:end]):
                raise Refused(line(), "meta-command not in the allowlist")
            i = end
        elif c == "'":
            if i > 0 and text[i - 1] in "Ee" and (i < 2 or not _IDENTIFIER.match(text[i - 2])):
                raise Refused(line(), "escape string")
            i += 1
            while True:
                j = text.find("'", i)
                if j < 0:
                    raise Refused(line(), "unterminated string")
                if text.startswith("''", j):
                    i = j + 2
                    continue
                i = j + 1
                break
        elif c == '"':
            j = text.find('"', i + 1)
            if j < 0:
                raise Refused(line(), "unterminated identifier")
            if "\\" in text[i:j]:
                raise Refused(line(), "backslash in a quoted identifier")
            i = j + 1
        elif c == "$" and (i == 0 or not _IDENTIFIER.match(text[i - 1])):
            match = _DOLLAR.match(text, i)
            if match is None:
                i += 1
                continue
            j = text.find(match.group(0), match.end())
            if j < 0:
                raise Refused(line(), "unterminated dollar quote")
            i = j + len(match.group(0))
        elif text.startswith("--", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            if "\\" in text[i:end]:
                raise Refused(line(), "backslash in a comment")
            i = end
        elif text.startswith("/*", i):
            depth, j = 1, i + 2
            while depth:
                opened, closed = text.find("/*", j), text.find("*/", j)
                if closed < 0:
                    raise Refused(line(), "unterminated comment")
                if 0 <= opened < closed:
                    depth, j = depth + 1, opened + 2
                else:
                    depth, j = depth - 1, closed + 2
            if "\\" in text[i:j]:
                raise Refused(line(), "backslash in a comment")
            i = j
        else:
            i += 1


def main() -> int:
    try:
        check(sys.stdin.read())
    except Refused as refusal:
        print(f"public-mcp owner-sql refused: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

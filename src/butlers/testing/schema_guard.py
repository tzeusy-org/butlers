"""Source-bound detection of copied migration-owned table creation in tests.

Names come from actual migration inputs. Unresolved CREATE TABLE identities are
not silently called unrelated; they need the same finite reviewed exception as
historical/non-query fixtures. No SQL or provider connection runs in this guard.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

_CREATE = re.compile(
    r"\bCREATE\s+(?:UNLOGGED\s+|TEMP(?:ORARY)?\s+)?TABLE\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(?P<identity>(?:\"[^\"]+\"|[\w{}]+)"
    r"(?:\s*\.\s*(?:\"[^\"]+\"|[\w{}]+))*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TableCreation:
    path: str
    line: int
    table: str | None
    source_sha256: str
    literal_sha256: str

    @property
    def identity(self) -> tuple:
        return self.path, self.source_sha256, self.literal_sha256, self.table


def _sql_fragments(module: ast.Module):
    parents = {
        child: parent for parent in ast.walk(module) for child in ast.iter_child_nodes(parent)
    }

    def fragment(node):
        if isinstance(node, ast.Constant) and type(node.value) is str:
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                part.value if isinstance(part, ast.Constant) else "{UNRESOLVED}"
                for part in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = fragment(node.left), fragment(node.right)
            if left is not None or right is not None:
                return (left or "{UNRESOLVED}") + (right or "{UNRESOLVED}")
        return None

    for node in ast.walk(module):
        value = fragment(node)
        if value is None:
            continue
        parent = parents.get(node)
        # Observe the complete finite string expression, not each fragment
        # independently; partial/dynamic object identities remain unresolved.
        if isinstance(parent, (ast.JoinedStr, ast.BinOp)) and fragment(parent) is not None:
            continue
        grandparent = parents.get(parent)
        if (
            isinstance(parent, ast.Expr)
            and isinstance(
                grandparent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            and grandparent.body[0] is parent
        ):
            continue
        yield node.lineno, value


def _table_names(sql: str):
    command = re.compile(r"\bCREATE\s+(?:UNLOGGED\s+|TEMP(?:ORARY)?\s+)?TABLE\b", re.I)
    for start in command.finditer(sql):
        match = _CREATE.match(sql, start.start())
        if match is None:
            yield None
            continue
        value = match["identity"].rsplit(".", 1)[-1].strip().strip('"')
        # The optional clause must never backtrack into treating IF as a table.
        yield None if "{" in value or value.lower() == "if" else value.lower()


def migration_owned_tables(root: Path) -> frozenset[str]:
    names = set()
    paths = sorted(
        {
            *root.glob("alembic/versions/**/*.py"),
            *root.glob("src/**/migrations/*.py"),
            *root.glob("roster/**/migrations/*.py"),
        }
    )
    if not paths:
        raise ValueError("migration input inventory absent")
    for path in paths:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for _, sql in _sql_fragments(module):
            names.update(name for name in _table_names(sql) if name is not None)
        for node in ast.walk(module):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and type(node.args[0].value) is str
            ):
                names.add(node.args[0].value.lower())
    if not names:
        raise ValueError("migration table inventory absent")
    return frozenset(names)


def copied_table_creations(root: Path) -> tuple[TableCreation, ...]:
    owned = migration_owned_tables(root)
    result = []
    for path in sorted({*root.glob("tests/**/*.py"), *root.glob("roster/*/tests/**/*.py")}):
        source = path.read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        module = ast.parse(source, filename=str(path))
        for line, sql in _sql_fragments(module):
            for table in _table_names(sql):
                if table is None or table in owned:
                    result.append(
                        TableCreation(
                            str(path.relative_to(root)),
                            line,
                            table,
                            digest,
                            hashlib.sha256(sql.encode()).hexdigest(),
                        )
                    )
        for node in ast.walk(module):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
            ):
                continue
            table = (
                node.args[0].value.lower()
                if node.args
                and isinstance(node.args[0], ast.Constant)
                and type(node.args[0].value) is str
                else None
            )
            if table is None or table in owned:
                result.append(
                    TableCreation(
                        str(path.relative_to(root)),
                        node.lineno,
                        table,
                        digest,
                        hashlib.sha256(
                            ast.dump(node, include_attributes=False).encode()
                        ).hexdigest(),
                    )
                )
    return tuple(result)


def unreviewed_creations(root: Path, declarations: Path) -> tuple[TableCreation, ...]:
    value = json.loads(declarations.read_text(encoding="utf-8"))
    if (
        type(value) is not dict
        or set(value) != {"schema", "exceptions"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or type(value["exceptions"]) is not list
    ):
        raise ValueError("invalid schema fixture exception declaration")
    admitted = set()
    for row in value["exceptions"]:
        if (
            type(row) is not dict
            or set(row)
            != {"path", "source_sha256", "literal_sha256", "table", "kind", "reason", "proof"}
            or row["kind"] not in {"historical", "non-query"}
            or type(row["reason"]) is not str
            or not row["reason"].strip()
            or type(row["proof"]) is not str
            or not row["proof"].strip()
        ):
            raise ValueError("invalid schema fixture exception declaration")
        if (
            type(row["path"]) is not str
            or not row["path"].endswith(".py")
            or Path(row["path"]).is_absolute()
            or ".." in Path(row["path"]).parts
            or any(
                type(row[key]) is not str or not re.fullmatch(r"[a-f0-9]{64}", row[key])
                for key in ("source_sha256", "literal_sha256")
            )
            or not (row["table"] is None or type(row["table"]) is str)
        ):
            raise ValueError("invalid schema fixture exception declaration")
        identity = tuple(row[key] for key in ("path", "source_sha256", "literal_sha256", "table"))
        if identity in admitted:
            raise ValueError("duplicate schema fixture exception declaration")
        admitted.add(identity)
    actual = copied_table_creations(root)
    if admitted - {row.identity for row in actual}:
        raise ValueError("stale schema fixture exception declaration")
    return tuple(row for row in actual if row.identity not in admitted)

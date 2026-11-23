"""Reconstruct the bu-rvgzmf.4 section-7 census; never redefine its denominator.

The October prototype/TSV are unavailable. This executable interpretation of
the structured definition is explicit about its fixture and category heuristics.
Rows count function definitions, not parametrized pytest items. Use --tsv to
retain the population, owner exclusions, helper closure and source locations.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from butlers.testing.source_guard import calls_with_owner, parse_file, python_files

METHOD = "section-7-reconstruction-v1"
ADOPTED = {"full": {"cases": 500, "lines": 8283}, "in_scope": {"cases": 326, "lines": 5899}}


@dataclass(frozen=True)
class Row:
    path: str
    line: int
    owner: str
    lines: int
    kind: str
    bucket: str
    helpers: str


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ""


def _bucket(path: str) -> str:
    name = Path(path).name
    if path.startswith("tests/migrations/"):
        return "OUT migrations (bu-dss7he.11)"
    if (
        path.startswith("tests/contracts/")
        and name.startswith(
            (
                "test_ci_",
                "test_frontend_guard_steps",
                "test_qg_serial_target",
                "test_standard_lint_tools",
            )
        )
        or path.startswith("tests/scripts/")
        and name.startswith(("test_ci_", "test_check_ci_"))
    ):
        return "OUT CI (bu-29wkwj.3/.8)"
    if name == "test_schema_standin_parity.py":
        return "OUT stand-in parity (bu-rvgzmf.1)"
    if (
        path.startswith("tests/config/")
        and name.startswith(
            (
                "test_init_db_",
                "test_restore_drill_",
                "test_runtime_probe_control_production_mount",
                "test_runtime_probe_control_startup_gate",
                "test_runtime_cli_sandbox_compose",
            )
        )
        or path.startswith("tests/scripts/")
        and name.startswith(
            (
                "test_compose",
                "test_dockerfile",
                "test_k8s",
                "test_pg_dump",
                "test_restore_drill",
                "test_whatsapp_bridge",
                "test_tailnet",
            )
        )
    ):
        return "OUT deployment (bu-dss7he.6/.14, bu-viat6h.4)"
    return "IN"


def census(root: Path) -> tuple[Row, ...]:
    rows = []
    for path in python_files(("tests/**/*.py", "roster/*/tests/**/*.py"), root=root):
        tree = parse_file(path)
        definitions: dict[str, list[ast.AST]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                definitions.setdefault(node.name, []).append(node)
        # Module-bound file contents and paths can hide a read outside a helper.
        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        definitions.setdefault(target.id, []).append(node)
        owners = {}
        for owner, call in calls_with_owner(tree):
            owners.setdefault(owner, []).append(call)

        def tests(node: ast.AST, prefix: str = ""):
            for child in ast.iter_child_nodes(node):
                qualified = prefix
                if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                    qualified = f"{prefix}.{child.name}".lstrip(".")
                    if isinstance(child, ast.FunctionDef) and child.name.startswith("test_"):
                        yield qualified, child
                yield from tests(child, qualified)

        for owner, test in tests(tree):
            closure = [test]
            visited = {id(test)}
            helpers = set()
            for member in closure:
                for node in ast.walk(member):
                    if isinstance(node, ast.Name):
                        for dependency in definitions.get(node.id, ()):
                            if id(dependency) not in visited:
                                visited.add(id(dependency))
                                closure.append(dependency)
                                helpers.add(node.id)
            nodes = [node for member in closure for node in ast.walk(member)]
            if not any(isinstance(node, ast.Assert) for node in ast.walk(test)):
                continue
            args = [a.arg for a in (*test.args.posonlyargs, *test.args.args, *test.args.kwonlyargs)]
            if any(
                re.search(r"(^|_)(pool|db|database|client)($|_)", arg)
                or arg in {"tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory"}
                for arg in args
            ):
                continue
            names = {_name(node) for node in nodes if isinstance(node, ast.Name | ast.Attribute)}
            if any(isinstance(node, ast.Await) for node in nodes) or any(
                name.split(".")[0] in {"tempfile", "subprocess"}
                or name.split(".")[-1] in {"TemporaryDirectory", "NamedTemporaryFile"}
                for name in names
            ):
                continue
            calls = {_name(node.func) for node in nodes if isinstance(node, ast.Call)}
            # Include lexical call ownership so nested test functions remain
            # individually represented, rather than accidentally flattened.
            calls.update(_name(call.func) for call in owners.get(owner, ()))
            reads = any(
                call.split(".")[-1] in {"read_text", "read_bytes", "open"} for call in calls
            )
            scans = any(
                call in {"ast.parse", "ast.walk"} or call.endswith("getsource") for call in calls
            )
            if not reads and not scans:
                continue
            strings = [
                node.value
                for node in nodes
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            if any(re.search(r"\.md\b|openspec|docs/|about/|AGENTS", value) for value in strings):
                kind = "doc"
            elif any(
                re.search(r"\.(?:ya?ml|toml|sql|sh|json)\b|Dockerfile", value) for value in strings
            ):
                kind = "config"
            elif scans:
                kind = "AST/source"
            else:
                kind = "source-text"
            relative = path.relative_to(root).as_posix()
            assert test.end_lineno is not None
            rows.append(
                Row(
                    relative,
                    test.lineno,
                    owner,
                    test.end_lineno - test.lineno + 1,
                    kind,
                    _bucket(relative),
                    ",".join(sorted(helpers)),
                )
            )
    return tuple(sorted(rows, key=lambda row: (row.path, row.line, row.owner)))


def summary(rows: tuple[Row, ...]) -> dict:
    def count(population):
        return {"cases": len(population), "lines": sum(row.lines for row in population)}

    full = count(rows)
    in_scope = count([row for row in rows if row.bucket == "IN"])
    return {
        "method": METHOD,
        "unit": "static test definitions (not collected parameter cases)",
        "full": full,
        "in_scope": in_scope,
        "adopted": ADOPTED,
        "difference_from_adopted": {
            key: {metric: measured[metric] - ADOPTED[key][metric] for metric in measured}
            for key, measured in (("full", full), ("in_scope", in_scope))
        },
        "acceptance": "OPEN: reconstruction is not the missing historical prototype",
        "kinds": {
            kind: count([row for row in rows if row.kind == kind])
            for kind in sorted({row.kind for row in rows})
        },
        "buckets": {
            bucket: count([row for row in rows if row.bucket == bucket])
            for bucket in sorted(Counter(row.bucket for row in rows))
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--tsv", action="store_true")
    args = parser.parse_args()
    rows = census(args.root)
    if args.tsv:
        writer = csv.DictWriter(sys.stdout, fieldnames=Row.__dataclass_fields__, delimiter="\t")
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    else:
        print(json.dumps(summary(rows), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

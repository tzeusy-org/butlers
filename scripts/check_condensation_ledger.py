#!/usr/bin/env python3
"""Read-only survivor admission; no deletion, retirement or CI authority is inferred."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

HEX = re.compile(r"[0-9a-f]{64}")

SCHEMA = "condensation/v1"
PROTECTED = {
    "architecture",
    "wire",
    "privacy",
    "authorization",
    "retry",
    "idempotency",
    "migration",
    "capability",
}


class EvidenceError(ValueError):
    """A fixed admission failure; never include private parameter or error operands."""


def strict_json(path: Path) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise EvidenceError("duplicate-json-key")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError("nonfinite-json")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("unreadable-json") from exc
    if not isinstance(value, dict):
        raise EvidenceError("nonobject-json")
    return value


def canonical(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def public_path(value: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise EvidenceError("invalid-source-path")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or p.as_posix() != value:
        raise EvidenceError("invalid-source-path")
    return Path(value)


def input_record(root: Path, name: str) -> dict:
    path = root / public_path(name)
    if any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
        raise EvidenceError("indirect-source-parent")
    if path.is_symlink():
        target = os.readlink(path)
        if (
            not target
            or os.path.isabs(target)
            or "\\" in target
            or any(ord(c) < 32 or ord(c) == 127 for c in target)
        ):
            raise EvidenceError("unsafe-source-link")
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise EvidenceError("unresolved-source-link") from None
        if root.resolve() not in resolved.parents:
            raise EvidenceError("escaping-source-link")
        return {
            "path": name,
            "sha256": hashlib.sha256(os.fsencode(target)).hexdigest(),
            "mode": "120000",
            "link": target,
        }
    if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:
        raise EvidenceError("missing-or-indirect-input")
    return {
        "path": name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mode": "100755" if path.stat().st_mode & 0o111 else "100644",
    }


def source_records(root: Path, paths: list[str]) -> list[dict]:
    """Bind every tracked object; links resolve only into the tracked closure."""
    records = [input_record(root, p) for p in paths]
    names = set(paths)
    for row in records:
        if row["mode"] != "120000":
            continue
        target = (root / row["path"]).resolve(strict=True)
        relative = str(target.relative_to(root.resolve()))
        if relative not in names and not (
            target.is_dir() and any(p.startswith(relative + "/") for p in names)
        ):
            raise EvidenceError("untracked-source-link-target")
    return records


def tool_record() -> dict:
    """Bind installed proof tools/plugins by actual files, without importing them."""
    names = {"coverage", "pytest"}
    names.update(
        entry.dist.metadata["Name"]
        for entry in importlib.metadata.entry_points(group="pytest11")
        if entry.dist is not None
    )
    rows = []
    prefixes = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
    for name in sorted(names):
        dist = importlib.metadata.distribution(name)
        if not dist.files:
            raise EvidenceError("installed-tool-files-unknown")
        for item in sorted(dist.files, key=str):
            if item.suffix not in {".py", ".so"} and item.name not in {
                "METADATA",
                "entry_points.txt",
            }:
                continue
            path = Path(dist.locate_file(item)).resolve()
            if not path.is_file() or not any(prefix in path.parents for prefix in prefixes):
                raise EvidenceError("installed-tool-source-unknown")
            rows.append(
                {
                    "distribution": name,
                    "path": str(item),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "mode": path.stat().st_mode & 0o777,
                }
            )
    return {
        "python": sys.version,
        "pytest": importlib.metadata.version("pytest"),
        "coverage": importlib.metadata.version("coverage"),
        "closure": canonical(rows),
    }


def git_environment() -> dict:
    # Local source reads must not be redirected to another index/worktree.
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def tracked_paths(root: Path, ref: str | None = None) -> list[str]:
    command = ("ls-tree", "-r", "--name-only", "-z", ref) if ref else ("ls-files", "-z")
    paths = [p for p in git(root, *command).split("\0") if p]
    for path in paths:
        public_path(path)
    return paths


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            stderr=subprocess.DEVNULL,
            text=True,
            env=git_environment(),
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise EvidenceError("git-source-unavailable") from exc


def test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return p.suffix == ".py" and (
        p.parts[0] == "tests"
        or (len(p.parts) > 3 and p.parts[0] == "roster" and p.parts[2] == "tests")
    )


def ast_record(node):
    """A serializable, location-independent tree for conservative context mapping."""
    if isinstance(node, ast.AST):
        return {
            "_node": type(node).__name__,
            **{k: ast_record(v) for k, v in ast.iter_fields(node)},
        }
    if isinstance(node, list):
        return [ast_record(n) for n in node]
    return node


def preserving_context(before, current) -> bool:
    """Qualify an additive shape for executable proof, never for automatic PASS."""
    if before == current:
        return True
    if not isinstance(before, dict) or not isinstance(current, dict):
        return False
    if set(before) != set(current) or before.get("_node") != current.get("_node"):
        return False
    kind = before["_node"]
    # Old assertions and abrupt exits must remain literal, at the same nesting.
    if kind in {"Assert", "Return", "Raise", "Break", "Continue", "Yield", "YieldFrom"}:
        return False
    if kind in {"Import", "ImportFrom"}:
        # A new alias qualifies execution evidence only; existing bindings and
        # their origins remain literal and cannot be shadowed or reordered.
        old, new = before["names"], current["names"]
        names = [a["asname"] or a["name"].split(".")[0] for a in new]
        return (
            all(before[k] == current[k] for k in before if k != "names")
            and contains_ordered(old, new)
            and len(names) == len(set(names))
            and all(a["name"] != "*" for a in new)
        )
    if kind == "arguments":
        if any(before[k] != current[k] for k in before if k not in {"kwonlyargs", "kw_defaults"}):
            return False
        old = list(zip(before["kwonlyargs"], before["kw_defaults"], strict=True))
        new = list(zip(current["kwonlyargs"], current["kw_defaults"], strict=True))
        if not contains_ordered(old, new):
            return False
        names = [a["arg"] for a, _ in new]
        if len(names) != len(set(names)):
            return False
        return all((a, default) in old or literal_probe_value(default) for a, default in new)
    if kind == "Call":
        if before["func"] != current["func"] or before["args"] != current["args"]:
            return False
        old, new = before["keywords"], current["keywords"]
        names = [k["arg"] for k in new]
        return (
            contains_ordered(old, new)
            and None not in names
            and len(names) == len(set(names))
            and all(k in old or literal_probe_value(k["value"]) for k in new)
        )
    for key in before:
        if key in {"body", "orelse", "finalbody"} and isinstance(before[key], list):
            if not preserving_statements(before[key], current[key]):
                return False
        elif isinstance(before[key], dict):
            if not preserving_context(before[key], current[key]):
                return False
        elif before[key] != current[key]:
            return False
    return True


def literal_probe_value(value) -> bool:
    return (
        isinstance(value, dict)
        and value.get("_node") == "Constant"
        and (value.get("value") is None or type(value.get("value")) is bool)
    )


def preserving_statements(before: list, current: list) -> bool:
    def abrupt(value):
        if isinstance(value, dict):
            return value.get("_node") in {
                "Return",
                "Raise",
                "Break",
                "Continue",
                "Yield",
                "YieldFrom",
            } or any(abrupt(v) for v in value.values())
        return isinstance(value, list) and any(abrupt(v) for v in value)

    position = 0
    for statement in current:
        if position < len(before) and preserving_context(before[position], statement):
            position += 1
        elif abrupt(statement):
            return False
    return position == len(before)


def test_shapes(path: str, text: str) -> dict[str, dict]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        raise EvidenceError("test-source-parse-failed") from exc
    imports = [
        ast.dump(n, include_attributes=False)
        for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    context = [
        ast.dump(n, include_attributes=False)
        for n in tree.body
        if not (
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
        )
        and not isinstance(n, ast.ClassDef)
    ]
    context_tree = [
        ast_record(n)
        for n in tree.body
        if not (
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
        )
        and not isinstance(n, ast.ClassDef)
    ]
    markers = [
        ast.dump(n, include_attributes=False)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets)
    ]
    result = {}

    def visit(body, parents, inherited_context, inherited_markers, inherited_tree):
        for node in body:
            if isinstance(node, ast.ClassDef):
                class_header = canonical(
                    {
                        "bases": [ast.dump(n, include_attributes=False) for n in node.bases],
                        "decorators": [
                            ast.dump(n, include_attributes=False) for n in node.decorator_list
                        ],
                        "keywords": [ast.dump(n, include_attributes=False) for n in node.keywords],
                    }
                )
                class_context = [class_header] + [
                    ast.dump(n, include_attributes=False)
                    for n in node.body
                    if not (
                        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name.startswith("test_")
                    )
                    and not isinstance(n, ast.ClassDef)
                ]
                class_tree = [class_header] + [
                    ast_record(n)
                    for n in node.body
                    if not (
                        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name.startswith("test_")
                    )
                    and not isinstance(n, ast.ClassDef)
                ]
                class_markers = [
                    ast.dump(n, include_attributes=False)
                    for n in node.body
                    if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets)
                ]
                visit(
                    node.body,
                    [*parents, node.name],
                    [*inherited_context, *class_context],
                    [*inherited_markers, *class_markers],
                    [*inherited_tree, *class_tree],
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                key = "::".join([path, *parents, node.name])
                if key in result:
                    raise EvidenceError("duplicate-test-binding")
                assertions = [
                    ast.dump(n, include_attributes=False)
                    for n in ast.walk(node)
                    if isinstance(n, ast.Assert)
                    or (
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr.startswith("assert")
                    )
                ]
                result[key] = {
                    "body": ast.dump(node, include_attributes=False),
                    "statements": [ast.dump(n, include_attributes=False) for n in node.body],
                    "assertion_statements": [
                        ast.dump(n, include_attributes=False)
                        for n in node.body
                        if isinstance(n, ast.Assert)
                    ],
                    "assertions": assertions,
                    "args": ast.dump(node.args, include_attributes=False),
                    "args_tree": ast_record(node.args),
                    "statement_tree": [ast_record(n) for n in node.body],
                    "decorators": [
                        ast.dump(n, include_attributes=False) for n in node.decorator_list
                    ],
                    "imports": imports,
                    "context": inherited_context,
                    "context_tree": inherited_tree,
                    "markers": inherited_markers,
                }

    visit(tree.body, [], context, markers, context_tree)
    return result


def contains_ordered(before: list, current: list) -> bool:
    iterator = iter(current)
    return all(any(item == expected for item in iterator) for expected in before)


def preserving_import_bindings(before: str, current: str) -> bool:
    """New import bindings cannot shadow any original lexical name."""
    old_tree, new_tree = ast.parse(before), ast.parse(current)

    def bindings(tree):
        result = []
        for node in tree.body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for alias in node.names:
                if alias.name == "*":
                    return None
                result.append(
                    (
                        type(node).__name__,
                        getattr(node, "module", None),
                        getattr(node, "level", None),
                        alias.name,
                        alias.asname,
                    )
                )
        return result

    old, new = bindings(old_tree), bindings(new_tree)
    if old == new:
        return True
    if old is None or new is None or not contains_ordered(old, new):
        return False
    protected = {n.id for n in ast.walk(old_tree) if isinstance(n, ast.Name)}
    protected |= {n.arg for n in ast.walk(old_tree) if isinstance(n, ast.arg)}
    protected |= {
        n.name
        for n in ast.walk(old_tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    protected |= {a[4] or (a[3].split(".")[0] if a[0] == "Import" else a[3]) for a in old}
    names = [a[4] or (a[3].split(".")[0] if a[0] == "Import" else a[3]) for a in new]
    return len(names) == len(set(names)) and all(
        a in old or names[i] not in protected for i, a in enumerate(new)
    )


def preserving_owner_arguments(before: dict, current: dict) -> bool:
    """Added fixture requests qualify execution; old signatures remain intact."""
    if before == current:
        return True
    return (
        all(before[k] == current[k] for k in before if k != "args")
        and current["args"][: len(before["args"])] == before["args"]
        and len({a["arg"] for a in current["args"]}) == len(current["args"])
        and all(a["annotation"] is None for a in current["args"][len(before["args"]) :])
        and not before["defaults"]
    )


def same_owner_context_accounts(root: Path, base: str, owners: set[str]) -> None:
    """Qualify preserving additive owners for fresh execution, never exemption."""
    losses = destructive_changes(root, base)
    cache = {}
    for owner in owners:
        path = owner.split("::", 1)[0]
        if path not in cache:
            cache[path] = (
                test_shapes(path, git(root, "show", f"{base}:{path}")),
                test_shapes(path, (root / path).read_text()),
            )
        old, new = (side.get(owner) for side in cache[path])
        old_source = git(root, "show", f"{base}:{path}")
        new_source = (root / path).read_text()
        if (
            losses.get(owner)
            not in {
                None,
                "fixture-helper-or-marker-context-loss",
                "import-context-loss",
                "test-execution-structure-change",
                "fixture-or-parameter-change",
            }
            or old is None
            or new is None
        ):
            raise EvidenceError("same-owner-context-not-preserving")
        if (
            not preserving_import_bindings(old_source, new_source)
            or old["decorators"] != new["decorators"]
            or old["markers"] != new["markers"]
            or not preserving_owner_arguments(old["args_tree"], new["args_tree"])
            or not preserving_statements(old["statement_tree"], new["statement_tree"])
            or not preserving_statements(old["context_tree"], new["context_tree"])
        ):
            raise EvidenceError("same-owner-context-not-preserving")


def destructive_changes(root: Path, base: str) -> dict[str, str]:
    base = git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base):
        raise EvidenceError("invalid-base-commit")
    paths = tracked_paths(root, base)
    modes = {
        row.split("\t", 1)[1]: row.split(" ", 1)[0]
        for row in git(root, "ls-tree", "-r", "-z", base).split("\0")
        if row
    }
    before = {}
    current = {}
    for path in paths:
        if not test_path(path):
            continue
        old_shapes = test_shapes(path, git(root, "show", f"{base}:{path}"))
        for shape in old_shapes.values():
            shape["mode"] = modes[path]
        before.update(old_shapes)
        f = root / path
        if f.exists():
            if f.is_symlink():
                raise EvidenceError("indirect-test-source")
            shapes = test_shapes(path, f.read_text())
            mode = input_record(root, path)["mode"]
            for shape in shapes.values():
                shape["mode"] = mode
            current.update(shapes)
    if not before:
        raise EvidenceError("empty-baseline-test-inventory")
    losses = {}
    for key, old in before.items():
        new = current.get(key)
        if new is None:
            losses[key] = "removed-or-moved"
        elif old["mode"] != new["mode"]:
            losses[key] = "source-mode-change"
        elif not contains_ordered(old["assertions"], new["assertions"]):
            losses[key] = "assertion-loss"
        elif old["args"] != new["args"] or old["decorators"] != new["decorators"]:
            losses[key] = "fixture-or-parameter-change"
        elif old["statements"] != new["statements"] and not (
            contains_ordered(old["statements"], new["statements"])
            and all(
                statement in new["assertion_statements"]
                for statement in new["statements"]
                if statement not in old["statements"]
            )
        ):
            losses[key] = "test-execution-structure-change"
        elif not contains_ordered(old["imports"], new["imports"]):
            losses[key] = "import-context-loss"
        elif old["markers"] != new["markers"]:
            losses[key] = "marker-context-change"
        elif not contains_ordered(old["context"], new["context"]):
            losses[key] = "fixture-helper-or-marker-context-loss"
    for path in paths:
        if PurePosixPath(path).name != "conftest.py":
            continue
        old_context = ast.dump(
            ast.parse(git(root, "show", f"{base}:{path}")), include_attributes=False
        )
        target = root / path
        if target.is_symlink():
            raise EvidenceError("indirect-fixture-source")
        new_context = (
            ast.dump(ast.parse(target.read_text()), include_attributes=False)
            if target.is_file()
            else None
        )
        if old_context != new_context:
            ancestor = PurePosixPath(path).parent
            for node in before:
                if (
                    ancestor == PurePosixPath(".")
                    or ancestor in PurePosixPath(node.split("::", 1)[0]).parents
                ):
                    losses.setdefault(node, "ancestral-fixture-context-change")
    return losses


def fields(value, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise EvidenceError("schema-fields")


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise EvidenceError("nonexact-count")


def identities(rows: list, field: str) -> set:
    if not isinstance(rows, list) or not rows:
        raise EvidenceError("empty-population")
    result = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field]:
            raise EvidenceError("invalid-identity")
        if row[field] in result:
            raise EvidenceError("duplicate-identity")
        result.add(row[field])
    return result


def read_artifact(directory: Path, record: dict) -> Path:
    if not isinstance(record, dict) or set(record) != {"path", "sha256", "bytes"}:
        raise EvidenceError("artifact-fields")
    path = directory / public_path(record["path"])
    if path.is_symlink() or not path.is_file() or directory.resolve() not in path.resolve().parents:
        raise EvidenceError("missing-or-indirect-artifact")
    integer(record["bytes"], 1)
    if (
        path.stat().st_size != record["bytes"]
        or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
    ):
        raise EvidenceError("artifact-body-mismatch")
    return path


def coverage_arcs(path: Path, run: dict) -> dict:
    """Read the actual branch SQLite carrier using literal equality and stdlib only."""
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute("SELECT version FROM coverage_schema").fetchall() != [(7,)]:
                raise EvidenceError("unsupported-coverage-schema")
            meta = dict(db.execute("SELECT key,value FROM meta"))
            if meta.get("has_arcs") != "1" or meta.get("version") != run["tools"]["coverage"]:
                raise EvidenceError("coverage-carrier-mode-or-version")
            root = Path(run["measurement_root"])
            if not root.is_absolute():
                raise EvidenceError("measurement-root")
            arcs = {}
            allowed_contexts = {""}
            for case in run["cases"]:
                key = case["key"]
                rows = set()
                for phase in ("setup", "call", "teardown"):
                    context = key + "|" + phase
                    allowed_contexts.add(context)
                    for filename, start, end in db.execute(
                        "SELECT file.path,arc.fromno,arc.tono FROM arc "
                        "JOIN file ON file.id=arc.file_id "
                        "JOIN context ON context.id=arc.context_id WHERE context.context=?",
                        (context,),
                    ):
                        try:
                            relative = Path(filename).relative_to(root).as_posix()
                        except ValueError:
                            raise EvidenceError("foreign-measured-source") from None
                        if (
                            relative not in run["scope"]
                            or type(start) is not int
                            or type(end) is not int
                        ):
                            raise EvidenceError("unbound-branch-source")
                        rows.add((relative, start, end))
                arcs[key] = [list(r) for r in sorted(rows)]
            if any(c not in allowed_contexts for (c,) in db.execute("SELECT context FROM context")):
                raise EvidenceError("unknown-coverage-context")
            return arcs
    except (sqlite3.Error, KeyError, TypeError, OSError):
        raise EvidenceError("unreadable-coverage-carrier") from None


def executable_proof(
    directory: Path, proof: dict, binding: dict, removed: list, survivors: list
) -> None:
    runs = proof["runs"]
    by_id = {r["id"]: r for r in runs}
    if len(by_id) != len(runs):
        raise EvidenceError("duplicate-run")
    scope = binding["scope"]
    scope_inputs = [next(r for r in binding["inputs"] if r["path"] == p) for p in scope]
    if len(scope_inputs) != len(scope) or any(r["mode"] == "120000" for r in scope_inputs):
        raise EvidenceError("unbound-production-scope")
    baselines = {}
    expected_flags = [
        "-n",
        "0",
        "-p",
        "no:cov",
        "-p",
        "no:terminal",
        "-o",
        "addopts=",
        "--import-mode=importlib",
        "-m",
        "not nightly and not bench and not perf",
        "--ignore=tests/benchmarks",
    ]
    for run in runs:
        if run["core"] != "ctrace" or run["tracer"] != "CTracer":
            raise EvidenceError("wrong-actual-run-core")
        saved = strict_json(read_artifact(directory, run["artifact"]))
        if saved != {k: v for k, v in run.items() if k not in {"artifact", "coverage_artifact"}}:
            raise EvidenceError("run-readback-mismatch")
        if (
            run["tools"] != binding["tools"]
            or run["scope"] != scope
            or run["command_flags"] != expected_flags
        ):
            raise EvidenceError("changed-tool-scope-or-command")
        side = run["side"]
        if side not in {"removed", "survivors"} or run["selection"] != binding["selection"][side]:
            raise EvidenceError("changed-selection")
        if any(not HEX.fullmatch(r["key"]) for r in run["cases"]):
            raise EvidenceError("nonopaque-case-key")
        if {r["node"] for r in run["cases"]} != set(run["selection"]):
            raise EvidenceError("missing-case-owner")
        phases = [r["phases"]["call"] for r in run["cases"]]
        if type(run["exit"]) is not int or run["exit"] != (
            1 if "assertion-failed" in phases else 0
        ):
            raise EvidenceError("exit-phase-disagreement")
        actual_arcs = coverage_arcs(read_artifact(directory, run["coverage_artifact"]), run)
        if actual_arcs != run["arcs"]:
            raise EvidenceError("branch-carrier-summary-mismatch")
        if run["id"] == side + "-baseline":
            if run["exit"] != 0 or run["scope_inputs"] != scope_inputs:
                raise EvidenceError("unhealthy-or-changed-baseline")
            baselines[side] = run
    if set(baselines) != {"removed", "survivors"}:
        raise EvidenceError("missing-baseline")
    for side, rows in (("removed", removed), ("survivors", survivors)):
        baseline = baselines[side]
        for row in rows:
            if row["params"] != sum(r["node"] == row["node"] for r in baseline["cases"]):
                raise EvidenceError("declared-case-multiplicity")
    overlap = {r["node"] for r in removed} & {r["node"] for r in survivors}
    for owner in overlap:
        populations = [
            {(r["key"], r["node"]) for r in baselines[side]["cases"] if r["node"] == owner}
            for side in ("removed", "survivors")
        ]
        if populations[0] != populations[1]:
            raise EvidenceError("same-owner-case-population-change")
    mutants = proof["mutation"]["mutants"]
    if proof["mutation"]["generated"] != len(mutants) or len(mutants) > 64:
        raise EvidenceError("mutation-population")
    identities(mutants, "id")
    expected_ids = {"removed-baseline", "survivors-baseline"}
    kills = {"removed": [], "survivors": []}
    lost = []
    for mutant in mutants:
        if (
            mutant["status"] != "RESTORED"
            or mutant["path"] not in scope
            or not HEX.fullmatch(mutant["id"])
        ):
            raise EvidenceError("unrestored-or-unbound-mutant")
        before_input = next(r for r in scope_inputs if r["path"] == mutant["path"])
        if (
            mutant["before_sha256"] != before_input["sha256"]
            or ("100755" if mutant["before_mode"] & 0o111 else "100644") != before_input["mode"]
        ):
            raise EvidenceError("mutation-journal-source-binding")
        before_path = directory / public_path(mutant["before_file"])
        if (
            before_path.is_symlink()
            or not before_path.is_file()
            or hashlib.sha256(before_path.read_bytes()).hexdigest() != mutant["before_sha256"]
        ):
            raise EvidenceError("mutation-journal-before-mismatch")
        killed_owners = {}
        for side in baselines:
            label = mutant["id"] + "-" + side
            expected_ids.update((label, label + "-restored"))
            if label not in by_id or label + "-restored" not in by_id:
                raise EvidenceError("missing-mutation-or-restoration-run")
            run, restored = by_id[label], by_id[label + "-restored"]
            baseline = baselines[side]
            expected = [
                dict(r, sha256=mutant["after_sha256"]) if r["path"] == mutant["path"] else r
                for r in scope_inputs
            ]
            if run["scope_inputs"] != expected or restored["scope_inputs"] != scope_inputs:
                raise EvidenceError("mutant-source-or-restoration-binding")
            population = {(r["key"], r["node"]) for r in baseline["cases"]}
            if {(r["key"], r["node"]) for r in run["cases"]} != population or restored[
                "cases"
            ] != baseline["cases"]:
                raise EvidenceError("mutation-case-population-or-restoration")
            killed_owners[side] = {
                r["node"] for r in run["cases"] if r["phases"]["call"] == "assertion-failed"
            }
            if killed_owners[side]:
                kills[side].append(mutant["id"])
        mapping = {r["node"]: set(r["survivors"]) for r in removed}
        if any(not killed_owners["survivors"] & mapping[n] for n in killed_owners["removed"]):
            lost.append(mutant["id"])
    if set(by_id) != expected_ids:
        raise EvidenceError("unexpected-run-population")
    if (
        any(kills[side] != proof["mutation"]["killed_by_" + side] for side in kills)
        or lost != proof["mutation"]["lost"]
    ):
        raise EvidenceError("mutation-summary-mismatch")
    residue = named_coverage_residue(
        baselines["removed"],
        baselines["survivors"],
        {row["node"]: row["survivors"] for row in removed},
    )
    if proof["coverage"]["residue"] != residue or proof["coverage"]["residue_arcs"] != len(residue):
        raise EvidenceError("coverage-residue-summary-mismatch")


def named_coverage_residue(before: dict, current: dict, mapping: dict) -> list:
    """Unrelated selected owners cannot supply another owner's missing arcs."""

    def owner_arcs(run, owners):
        return {
            tuple(arc)
            for case in run["cases"]
            if case["node"] in owners
            for arc in run["arcs"][case["key"]]
        }

    if not owner_arcs(before, set(mapping)) or not owner_arcs(current, set(current["selection"])):
        raise EvidenceError("empty-measured-branch-population")
    residue = set()
    for owner, named in mapping.items():
        residue |= owner_arcs(before, {owner}) - owner_arcs(current, set(named))
    return [list(arc) for arc in sorted(residue)]


def verify_ledger(
    root: Path, value: dict, *, base: str, current: dict[str, dict], directory: Path
) -> set[str]:
    required = {
        "schema",
        "cluster",
        "bead",
        "contract",
        "removed",
        "survivors",
        "binding",
        "proof",
        "seconds",
    }
    if set(value) != required or value["schema"] != SCHEMA:
        raise EvidenceError("schema-fields")
    for field in ["cluster", "bead"]:
        if not isinstance(value[field], str) or not value[field]:
            raise EvidenceError("missing-owner")
    contract = value["contract"]
    if (
        not isinstance(contract, dict)
        or set(contract) != {"class", "cites"}
        or contract["class"] not in PROTECTED
    ):
        raise EvidenceError("contract-authority")
    if (
        not isinstance(contract["cites"], list)
        or not contract["cites"]
        or not all(isinstance(c, str) and c for c in contract["cites"])
    ):
        raise EvidenceError("missing-contract-citation")
    if contract["class"] == "migration":
        raise EvidenceError("migration-runtime-proof-requires-independent-admission")
    if len(set(contract["cites"])) != len(contract["cites"]):
        raise EvidenceError("duplicate-contract-citation")
    for citation in contract["cites"]:
        if re.fullmatch(r"REQ-(?:[a-z0-9]+-)+[0-9]+", citation):
            definitions = [
                p
                for p in tracked_paths(root)
                if p.startswith("openspec/") and p.endswith(".md") and "/archive/" not in p
            ]
            if not any(
                re.search(r"(?m)^ID: " + re.escape(citation) + r"\s*$", (root / p).read_text())
                for p in definitions
            ):
                raise EvidenceError("unresolved-contract-citation")
        else:
            reference = public_path(citation.split("#", 1)[0])
            if not (root / reference).is_file():
                raise EvidenceError("unresolved-contract-citation")
    removed = identities(value["removed"], "node")
    survivors = identities(value["survivors"], "node")
    overlap = removed & survivors
    if not survivors <= current.keys():
        raise EvidenceError("missing-or-aliased-survivor")
    if overlap:
        same_owner_context_accounts(root, base, overlap)
    for row in value["removed"]:
        fields(row, {"node", "params", "survivors", "reason"})
        integer(row.get("params"), 1)
        if (
            not isinstance(row.get("survivors"), list)
            or not row["survivors"]
            or not set(row["survivors"]) <= survivors
        ):
            raise EvidenceError("unmapped-survivor")
        if not isinstance(row.get("reason"), str) or not row["reason"]:
            raise EvidenceError("missing-reason")
        if row["node"] in overlap and row["survivors"] != [row["node"]]:
            raise EvidenceError("same-owner-context-needs-exact-self-mapping")
    for row in value["survivors"]:
        fields(row, {"node", "params"})
        integer(row.get("params"), 1)
    binding = value["binding"]
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {"base", "head", "tree", "inputs", "inputs_hash", "nonce", "selection", "scope", "tools"}
        or binding.get("base") != git(root, "rev-parse", base)
    ):
        raise EvidenceError("base-identity")
    if binding.get("head") != git(root, "rev-parse", "HEAD"):
        raise EvidenceError("stale-head")
    if binding["tree"] != git(root, "rev-parse", "HEAD^{tree}"):
        raise EvidenceError("stale-tree")
    fields(binding["tools"], {"python", "pytest", "coverage", "closure"})
    if binding["tools"] != tool_record():
        raise EvidenceError("changed-installed-proof-tools")
    inputs = binding.get("inputs")
    identities(inputs, "path")
    actual_paths = set(tracked_paths(root))
    if {r["path"] for r in inputs} != actual_paths:
        raise EvidenceError("incomplete-input-inventory")
    if inputs != source_records(root, tracked_paths(root)):
        raise EvidenceError("changed-input-body-or-mode")
    mandatory = {
        "uv.lock",
        "pyproject.toml",
        "scripts/condense_evidence.py",
        "scripts/check_condensation_ledger.py",
        "scripts/pre_push.py",
    }
    if not mandatory <= {r["path"] for r in inputs}:
        raise EvidenceError("missing-tool-or-config-binding")
    if (
        binding.get("inputs_hash") != canonical(inputs)
        or not isinstance(binding.get("nonce"), str)
        or not HEX.fullmatch(binding["nonce"])
    ):
        raise EvidenceError("input-or-run-identity")
    proof = value["proof"]
    if not isinstance(proof, dict) or proof.get("mode") != "cov+mut":
        raise EvidenceError("unsupported-proof-mode-requires-runtime-review")
    if (
        proof.get("status") != "PASS"
        or proof.get("restored") is not True
        or proof.get("cleanup") is not True
    ):
        raise EvidenceError("unknown-incomplete-or-unrestored")
    fields(proof, {"mode", "status", "restored", "cleanup", "coverage", "mutation", "runs"})
    coverage = proof.get("coverage", {})
    fields(coverage, {"core", "branch", "residue_arcs", "residue", "waived"})
    if coverage.get("core") != "ctrace" or coverage.get("branch") is not True:
        raise EvidenceError("wrong-coverage-core")
    integer(coverage.get("residue_arcs"))
    if coverage["residue_arcs"] != 0 or coverage.get("waived") != []:
        raise EvidenceError("residue-needs-independent-arc-review")
    mutation = proof.get("mutation", {})
    fields(
        mutation,
        {
            "seed",
            "generated",
            "mutants",
            "killed_by_removed",
            "killed_by_survivors",
            "lost",
            "unknown",
        },
    )
    integer(mutation.get("seed"))
    if mutation["seed"] != 0:
        raise EvidenceError("unsupported-mutation-seed")
    integer(mutation.get("generated"), 1)
    if mutation.get("lost") != [] or mutation.get("unknown") != []:
        raise EvidenceError("lost-or-unknown-mutation")
    killed_removed = mutation.get("killed_by_removed")
    killed_survivors = mutation.get("killed_by_survivors")
    if (
        not isinstance(killed_removed, list)
        or not killed_removed
        or not isinstance(killed_survivors, list)
        or not set(killed_removed) <= set(killed_survivors)
    ):
        raise EvidenceError("missing-survivor-kill")
    if len(set(killed_removed)) != len(killed_removed) or len(set(killed_survivors)) != len(
        killed_survivors
    ):
        raise EvidenceError("duplicate-mutation-identity")
    runs = proof.get("runs")
    identities(runs, "id")
    for run in runs:
        fields(
            run,
            {
                "id",
                "complete",
                "cleanup",
                "selected",
                "deselected",
                "cases",
                "arcs",
                "core",
                "tracer",
                "exit",
                "selection",
                "command_flags",
                "measurement_root",
                "scope",
                "scope_inputs",
                "tools",
                "side",
                "artifact",
                "coverage_artifact",
            },
        )
        if (
            run.get("complete") is not True
            or run.get("cleanup") is not True
            or type(run.get("deselected")) is not int
            or run["deselected"] != 0
        ):
            raise EvidenceError("incomplete-run")
        cases = run.get("cases")
        identities(cases, "key")
        if run.get("selected") != len(cases) or type(run.get("selected")) is not int:
            raise EvidenceError("case-multiplicity")
        for case in cases:
            fields(case, {"key", "node", "phases"})
            if set(case.get("phases", {})) != {"setup", "call", "teardown"}:
                raise EvidenceError("incomplete-case-phases")
            if (
                case["phases"]["setup"] != "passed"
                or case["phases"]["teardown"] != "passed"
                or case["phases"]["call"] not in {"passed", "assertion-failed"}
            ):
                raise EvidenceError("nonsemantic-case-outcome")
    fields(value["seconds"], {"before", "after"})
    for seconds in value["seconds"].values():
        if seconds is not None and (
            type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds < 0
        ):
            raise EvidenceError("nonexact-timing")
    executable_proof(directory, proof, binding, value["removed"], value["survivors"])
    return removed


def _verify(root: Path, base: str, ledgers: list[Path]) -> dict:
    head = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    paths = tracked_paths(root)
    relevant = [
        input_record(root, p)
        for p in paths
        if (test_path(p) or Path(p).name == "conftest.py" or p in {"pyproject.toml", "uv.lock"})
        and (root / p).is_file()
    ]
    losses = destructive_changes(root, base)
    current = {}
    for path in tracked_paths(root):
        if test_path(path) and (root / path).is_file():
            current.update(test_shapes(path, (root / path).read_text()))
    admitted = set()
    clusters = set()
    for path in ledgers:
        value = strict_json(path)
        if value.get("cluster") in clusters:
            raise EvidenceError("duplicate-cluster")
        clusters.add(value.get("cluster"))
        members = verify_ledger(root, value, base=base, current=current, directory=path.parent)
        if members & admitted:
            raise EvidenceError("duplicate-removed-alias")
        admitted |= members
    if set(losses) - admitted:
        raise EvidenceError("unproven-test-or-assertion-loss")
    if (
        git(root, "rev-parse", "HEAD") != head
        or git(root, "rev-parse", "HEAD^{tree}") != tree
        or tracked_paths(root) != paths
        or any(input_record(root, r["path"]) != r for r in relevant)
    ):
        raise EvidenceError("source-changed-during-admission")
    return {
        "status": "PASS",
        "losses": len(losses),
        "ledger_clusters": len(clusters),
        "test_definitions": len(current),
        "source_read_only": True,
    }


def verify(root: Path, base: str, ledgers: list[Path]) -> dict:
    try:
        return _verify(root, base, ledgers)
    except EvidenceError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        StopIteration,
        UnicodeError,
        OSError,
        ImportError,
    ):
        raise EvidenceError("invalid-structural-evidence") from None


def ci_binding(root: Path, *, event: str, base: str, expected_head: str, source_head: str) -> str:
    """Admit the provider's exact event checkout before the same complete scan."""
    for value in (base, expected_head, source_head):
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)
            or set(value) == {"0"}
        ):
            raise EvidenceError("missing-ci-source-identity")
        if git(root, "rev-parse", "--verify", f"{value}^{{commit}}") != value:
            raise EvidenceError("ci-commit-unavailable")
    if expected_head != git(root, "rev-parse", "HEAD") or base == expected_head:
        raise EvidenceError("ci-checkout-or-base-mismatch")
    parents = git(root, "rev-list", "--parents", "-n", "1", expected_head).split()[1:]
    if event == "pull_request":
        if parents != [base, source_head]:
            raise EvidenceError("ci-pr-union-mismatch")
    elif event in {"merge_group", "push"}:
        if source_head != expected_head:
            raise EvidenceError("ci-event-head-mismatch")
        # --is-ancestor emits no operands and fails closed through git().
        git(root, "merge-base", "--is-ancestor", base, expected_head)
    else:
        raise EvidenceError("unsupported-ci-event")
    return base


def fresh_plan_ledgers(root: Path, base: str, output: Path) -> list[Path]:
    """Execute current tracked requests, not cached receipt discovery or rebinding."""
    from pre_push import Refusal, run_checked

    paths = [
        p
        for p in tracked_paths(root)
        if p.startswith("tests/condensation-plans/") and p.endswith(".json")
    ]
    ledgers = []
    if not paths:
        return ledgers
    losses = destructive_changes(root, base)
    for index, name in enumerate(paths):
        plan_path = root / name
        record = input_record(root, name)
        if record["mode"] == "120000":
            raise EvidenceError("indirect-condensation-plan")
        plan = strict_json(plan_path)
        fields(
            plan,
            {"base", "scope", "removed", "survivors", "cluster", "bead", "contract", "mapping"},
        )
        if plan["base"] != "event-base":
            raise EvidenceError("plan-needs-fresh-event-base")
        for key in ("scope", "removed", "survivors"):
            rows = plan[key]
            if (
                not isinstance(rows, list)
                or not rows
                or any(not isinstance(row, str) or not row for row in rows)
                or len(rows) != len(set(rows))
            ):
                raise EvidenceError("invalid-fresh-plan-selection")
        if not isinstance(plan["mapping"], dict) or set(plan["mapping"]) != set(plan["removed"]):
            raise EvidenceError("unmapped-fresh-plan-owner")
        for row in plan["mapping"].values():
            fields(row, {"survivors", "reason"})
            if (
                not isinstance(row["reason"], str)
                or not row["reason"]
                or not isinstance(row["survivors"], list)
                or not row["survivors"]
                or any(not isinstance(n, str) for n in row["survivors"])
                or not set(row["survivors"]) <= set(plan["survivors"])
            ):
                raise EvidenceError("unmapped-fresh-plan-survivor")
        fields(plan["contract"], {"class", "cites"})
        if (
            plan["contract"]["class"] not in PROTECTED
            or not isinstance(plan["contract"]["cites"], list)
            or not plan["contract"]["cites"]
            or any(not isinstance(c, str) or not c for c in plan["contract"]["cites"])
        ):
            raise EvidenceError("invalid-fresh-plan-contract")
        if not set(plan["removed"]) & set(losses):
            # A request is relevant only to actual losses at this exact base.
            # Its mere continued presence cannot manufacture an admission or
            # force stale executions on later unrelated changes.
            continue
        # This is a new execution request at the admitted event base. No saved
        # nonce/head/tree/SQLite carrier is edited or inferred from this request.
        plan = dict(plan, base=base)
        config = output / f"plan-{index}.json"
        config.write_text(json.dumps(plan, sort_keys=True) + "\n")
        run = output / f"proof-{index}"
        try:
            run_checked(
                "condensation-proof",
                [
                    sys.executable,
                    str(root / "scripts/condense_evidence.py"),
                    "prove",
                    "--repo-root",
                    str(root),
                    "--config",
                    str(config),
                    "--output",
                    str(run),
                ],
                root,
                timeout=70,
            )
        except Refusal:
            raise EvidenceError("fresh-condensation-proof-refused") from None
        if input_record(root, name) != record:
            raise EvidenceError("plan-changed-during-execution")
        ledgers.append(run / "ledger.json")
    return ledgers


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base", required=True)
    parser.add_argument("--ledger", type=Path, action="append", default=[])
    parser.add_argument("--ci-event", choices=["pull_request", "merge_group", "push"])
    parser.add_argument("--expected-head")
    parser.add_argument("--source-head")
    args = parser.parse_args(argv)
    try:
        root = args.repo_root.resolve()
        base = args.base
        if args.ci_event:
            base = ci_binding(
                root,
                event=args.ci_event,
                base=base,
                expected_head=args.expected_head,
                source_head=args.source_head,
            )
        elif args.expected_head or args.source_head:
            raise EvidenceError("incomplete-ci-binding")
        home = root / ".tmp/condensation-ci"
        home.mkdir(parents=True, exist_ok=True)
        # Durable fresh outputs retain UNKNOWN/refusal and complete real carriers.
        # This directory is not searched on a subsequent admission.
        directory = Path(tempfile.mkdtemp(prefix="run-", dir=home))
        fresh = fresh_plan_ledgers(root, base, directory)
        result = verify(root, base, [*args.ledger, *fresh])
    except EvidenceError as exc:
        print(json.dumps({"status": "REFUSED", "category": str(exc)}))
        return 1
    except (KeyError, TypeError, ValueError, IndexError, StopIteration, UnicodeError, OSError):
        print(json.dumps({"status": "REFUSED", "category": "invalid-structural-evidence"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

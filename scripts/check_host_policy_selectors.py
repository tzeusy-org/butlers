#!/usr/bin/env python3
"""Check the reviewed host-selector inventory; see docs/architecture/host-policy-selectors.md.

This is static source evidence, not proof of runtime authority or configurability.
There is deliberately no update/refreeze option. Inventory changes require review.
"""

from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = "scripts/host-policy-selectors.json"
CATEGORIES = {
    "CANONICAL_IDENTITY_AUTHORITY_OR_PROVENANCE",
    "PROTECTED_HOST_OR_DOMAIN_SELECTOR",
    "SCHEMA_OWNERSHIP",
    "HISTORICAL_RECORD_OR_AVAILABILITY",
    "NON_NAME_ENUM_OR_MODULE",
    "HOST_POLICY_CANDIDATE_NEEDS_SHAPING",
    "EVIDENCED_HOST_POLICY",
}
HOST_POLICY_LIMIT = 0  # No site has been established safely configurable by S3.


def scan(sources: dict[str, str], names: set[str]) -> list[dict]:
    """Count comparisons once; also inventory dynamic names and structural lookups.

    Literal traversal and alias resolution are intentionally separate: historical
    direct/container counts never acquire matches from the additional coverage.
    Simple name assignments resolve flow-insensitively within lexical scopes.
    """
    return [
        row for path, source in sorted(sources.items()) for row in _scan_file(path, source, names)
    ]


def _scan_file(path: str, source: str, names: set[str]) -> list[dict]:
    rows = []
    tree = ast.parse(source, filename=path)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    scopes = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def enclosing(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, scopes):
                return node
        return tree

    bindings = collections.defaultdict(dict)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and node.value is not None:
                    bindings[enclosing(node)].setdefault(target.id, []).append(node.value)

    def resolved(node, scope, seen=frozenset()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return set().union(*(resolved(x, scope, seen) for x in node.elts))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return {
                a + b
                for a in resolved(node.left, scope, seen)
                for b in resolved(node.right, scope, seen)
            }
        if isinstance(node, ast.Name):
            while True:
                key = (scope, node.id)
                if key in seen:
                    return set()
                if node.id in bindings[scope]:
                    return set().union(
                        *(resolved(x, scope, seen | {key}) for x in bindings[scope][node.id])
                    )
                if scope is tree:
                    break
                scope = enclosing(scope)
        return set()

    occurrences = collections.Counter()
    for node in sorted(
        ast.walk(tree), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))
    ):
        direct = container = False
        kind = None
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            direct = any(
                isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value in names
                for x in operands
            )
            container = any(
                isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value in names
                for o in operands
                for x in ast.walk(o)
            )
            dynamic = any(
                isinstance(x, (ast.Name, ast.Attribute))
                and (x.id if isinstance(x, ast.Name) else x.attr) == "required_name"
                for x in ast.walk(node)
            )
            aliases = any(resolved(x, enclosing(node)) & names for x in operands)
            if container or dynamic or aliases:
                kind = "compare" if container else "dynamic_compare" if dynamic else "alias_compare"
        elif isinstance(node, ast.Subscript) and resolved(node.slice, enclosing(node)) & names:
            kind = "lookup"
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "pop", "setdefault"}
            and node.args
            and resolved(node.args[0], enclosing(node)) & names
        ):
            kind = "lookup"
        if kind is None:
            continue
        symbols = []
        parent = node
        while parent in parents:
            parent = parents[parent]
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(parent.name)
        symbol = ".".join(reversed(symbols)) or "<module>"
        expression = ast.unparse(node)
        key = (path, symbol, kind, expression)
        occurrences[key] += 1
        identity = [*key, occurrences[key]]
        rows.append(
            dict(
                id=hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                path=path,
                symbol=symbol,
                kind=kind,
                expression=expression,
                occurrence=occurrences[key],
                line=node.lineno,
                direct=direct,
                container=container,
            )
        )
    return rows


def counts(rows: list[dict]) -> dict:
    return dict(
        direct=sum(r["direct"] for r in rows),
        direct_files=len({r["path"] for r in rows if r["direct"]}),
        container=sum(r["container"] for r in rows),
        supplementary=sum(not r["container"] for r in rows),
        all_categories=dict(collections.Counter(r["category"] for r in rows)),
        direct_categories=dict(collections.Counter(r["category"] for r in rows if r["direct"])),
    )


def check(rows: list[dict], inventory: dict, root: Path, previous: dict | None = None) -> list[str]:
    errors = []
    reviewed = {r["id"]: r for r in inventory["selectors"]}
    actual = {r["id"]: r for r in rows}
    if not rows:
        errors.append("empty selector scan")
    if len(reviewed) != len(inventory["selectors"]):
        errors.append("duplicate inventory identity")
    for identity in actual.keys() - reviewed.keys():
        errors.append(
            f"unclassified selector: {actual[identity]['path']}:{actual[identity]['line']}"
        )
    for identity in reviewed.keys() - actual.keys():
        errors.append(f"lost selector: {reviewed[identity]['path']}::{identity}")
    for identity in actual.keys() & reviewed.keys():
        row = reviewed[identity]
        for field in ("path", "symbol", "kind", "expression", "occurrence", "direct", "container"):
            if row[field] != actual[identity][field]:
                errors.append(f"inventory identity mismatch: {identity}")
        if row.get("category") not in CATEGORIES or not row.get("purpose") or not row.get("reason"):
            errors.append(f"unclassified inventory metadata: {identity}")
        citations = row.get("citations", [])
        if not citations:
            errors.append(f"missing normative citation: {identity}")
        for citation in citations:
            relative = Path(citation["path"])
            path = root / relative
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not relative.as_posix().startswith("openspec/specs/")
                or relative.name != "spec.md"
                or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file()
                or f"### Requirement: {citation['requirement']}" not in path.read_text()
            ):
                errors.append(f"invalid normative citation: {identity}")
    host_count = sum(r.get("category") == "EVIDENCED_HOST_POLICY" for r in reviewed.values())
    if host_count > HOST_POLICY_LIMIT:
        errors.append(f"evidenced host-policy growth: {host_count} > {HOST_POLICY_LIMIT}")
    baseline = {r["id"]: r for r in inventory["baseline"]["selectors"]}
    retired = {r["id"]: r for r in inventory["retired"]}
    for identity, old in baseline.items():
        descendants = [r for r in reviewed.values() if identity in r["baseline_ids"]]
        if not descendants and not retired.get(identity, {}).get("reason"):
            errors.append(f"lost baseline inventory: {identity}")
        if old["category"] == "HOST_POLICY_CANDIDATE_NEEDS_SHAPING":
            if len(descendants) != 1 or descendants[0]["category"] != old["category"]:
                errors.append(f"unresolved policy candidate lost/reclassified: {identity}")
    if previous is not None:
        changes = {r["id"]: r.get("reason") for r in inventory["review_changes"]}
        for old in previous["selectors"]:
            new = reviewed.get(old["id"])
            if (new is None or new["category"] != old["category"]) and not changes.get(old["id"]):
                errors.append(f"unexplained inventory loss/reclassification: {old['id']}")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--inventory", default=INVENTORY)
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        inventory = json.loads((root / args.inventory).read_text())
        names = {p.parent.name for p in (root / "roster").glob("*/butler.toml")}
        if names != set(inventory["roster_names"]):
            raise ValueError("roster vocabulary changed; inventory review required")
        paths = list((root / "src/butlers").rglob("*.py"))
        if any(p.is_symlink() for p in paths):
            raise ValueError("linked Python source is unsupported")
        sources = {str(p.relative_to(root)): p.read_text() for p in paths}
        if not sources:
            raise ValueError("empty Python source scan")
        old = subprocess.run(
            ["git", "-C", str(root), "show", f"{args.base}:{args.inventory}"],
            capture_output=True,
            text=True,
        )
        # Only first delivery lacks the inventory in an otherwise valid base.
        if subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", args.base], capture_output=True
        ).returncode:
            raise ValueError("base revision unavailable")
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "--name-only", args.base, "--", args.inventory],
            capture_output=True,
            text=True,
        )
        if listed.returncode or (listed.stdout.strip() and old.returncode):
            raise ValueError("base inventory unavailable")
        previous = json.loads(old.stdout) if old.returncode == 0 else None
        rows = scan(sources, names)
        errors = check(rows, inventory, root, previous)
        for error in errors:
            print(error)
        if errors:
            return 1
        print(json.dumps(counts(inventory["selectors"]), sort_keys=True))
        print("host-policy-selectors: PASS (static inventory only)")
        return 0
    except (OSError, ValueError, KeyError, SyntaxError) as exc:
        print(f"host-policy-selectors: FAILED ({exc})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

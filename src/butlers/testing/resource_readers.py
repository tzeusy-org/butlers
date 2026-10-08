"""Source-bound public-resource readers, without importing or executing tests.

Literal references deliberately over-approximate consumers. Reviewed dynamic
reader declarations add whole-family ownership; discovery is a completeness
check, never a proof of arbitrary runtime behavior. Unknown/stale declarations
refuse narrowing. The historical reader audit remains independent evidence.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import subprocess
from pathlib import Path

FAMILIES = ("docs/", "about/", "openspec/", ".claude/skills/", "frontend/", "deploy/")
REGISTRY = "scripts/test-resource-readers.json"
DECLARATIONS = "scripts/test-resource-reader-declarations.json"
REFERENCE = re.compile(r"(?:docs|about|openspec|frontend|deploy|\.claude/skills)/[\w./*{}-]+")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def resource_family(path: str) -> str | None:
    if "/" not in path and path.endswith(".md"):
        return "*.md"
    return next((prefix for prefix in FAMILIES if path.startswith(prefix)), None)


def safe_path(path: str) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith("/")
        and not any(part in {".", "..", ""} for part in path.rstrip("/").split("/"))
        and not any(ord(c) < 32 for c in path)
    )


def tracked_sources(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "tests", "roster"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return sorted(
        p.decode()
        for p in result.stdout.split(b"\0")
        if p.endswith(b".py") and (p.startswith(b"tests/") or b"/tests/" in p)
    )


def consumers(root: Path, source: str) -> list[str]:
    """Helpers select their real test-bearing parent, never the helper itself."""
    path = root / source
    if path.name.startswith("test_"):
        return [source]
    parent = path.parent
    while parent.is_relative_to(root) and parent != root:
        found = sorted(str(p.relative_to(root)) for p in parent.rglob("test_*.py"))
        if found:
            return found
        parent = parent.parent
    raise ValueError("READER_UNCLASSIFIED")


IO_OPERATIONS = {
    "read_text",
    "read_bytes",
    "open",
    "glob",
    "rglob",
    "iterdir",
    "get_data",
    "read",
    "readline",
    "readlines",
}


def literal_string(node: ast.AST) -> str | None:
    """Fold only finite literal strings; never evaluate a test expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_string(node.left), literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts = [
            literal_string(v.value) if isinstance(v, ast.FormattedValue) else literal_string(v)
            for v in node.values
        ]
        if all(p is not None for p in parts):
            return "".join(parts)
    return None


def has_io_reference(tree: ast.AST) -> bool:
    """Track supported I/O names and aliases conservatively, including escapes.

    Passing open to partial/a helper or returning an alias cannot prove absence
    of I/O merely because its eventual call uses a different spelling.
    """
    aliases = {"open"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(a.asname or a.name for a in node.names if a.name in IO_OPERATIONS)
    changed = True
    while changed:
        before = len(aliases)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            io = (isinstance(value, ast.Name) and value.id in aliases) or (
                isinstance(value, ast.Attribute) and value.attr in IO_OPERATIONS
            )
            if io:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                aliases.update(t.id for t in targets if isinstance(t, ast.Name))
        changed = before != len(aliases)
    return any(
        (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in aliases)
        or (isinstance(n, ast.Attribute) and n.attr in IO_OPERATIONS)
        or (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "getattr"
            and len(n.args) >= 2
            and literal_string(n.args[1]) in IO_OPERATIONS
        )
        for n in ast.walk(tree)
    )


def discover(root: Path, declarations: dict) -> dict:
    if declarations.get("schema") != "test-resource-declarations.v1":
        raise ValueError("READER_UNCLASSIFIED")
    dynamic = declarations.get("dynamic", {})
    if not isinstance(dynamic, dict):
        raise ValueError("READER_UNCLASSIFIED")
    sources = tracked_sources(root)
    if any(name not in sources for name in dynamic):
        raise ValueError("READER_UNCLASSIFIED")
    bindings: dict[str, set[str]] = {}
    identities = {}
    unresolved = []
    for name in sources:
        raw = (root / name).read_bytes()
        tree = ast.parse(raw)
        # String references are conservative candidates, including docstrings.
        # A split root join is a whole-family candidate, not an exact filename.
        patterns = set()
        for node in ast.walk(tree):
            value = literal_string(node)
            if value is None:
                continue
            patterns.update(match.group().rstrip("./") for match in REFERENCE.finditer(value))
            if value.rstrip("/") in {f.rstrip("/") for f in FAMILIES}:
                patterns.add(value.rstrip("/") + "/**")
            if re.fullmatch(r"[A-Z][\w-]*\.md", value):
                patterns.add(value)
        declared = dynamic.get(name, [])
        if not isinstance(declared, list) or any(
            not isinstance(p, str) or resource_family(p) is None for p in declared
        ):
            raise ValueError("READER_UNCLASSIFIED")
        patterns.update(declared)
        # A variable/dynamic read can consume a public resource without spelling
        # its root in this file. Never let absence of a literal prove absence of
        # a reader. Explicit, body-bound declarations may narrow this conservative
        # whole-family fallback; tests are never imported to discover it.
        if has_io_reference(tree) and name not in dynamic:
            patterns.update(family + "**" for family in FAMILIES)
            patterns.add("*.md")
            unresolved.append(name)
        # Bind every visited body, so a newly introduced reader or a previously
        # non-reading file becoming dynamic cannot survive a stale registry.
        identities[name] = hashlib.sha256(raw).hexdigest()
        if not patterns:
            continue
        selected = consumers(root, name)
        for pattern in patterns:
            if not safe_path(pattern) or resource_family(pattern) is None:
                raise ValueError("READER_UNCLASSIFIED")
            bindings.setdefault(pattern, set()).update(selected)
    body = {
        "schema": "test-resource-readers.v1",
        "declarations_digest": digest(declarations),
        "sources": identities,
        "readers": {k: sorted(v) for k, v in sorted(bindings.items())},
        "families": [*FAMILIES, "*.md"],
        "unresolved_dynamic_readers": unresolved,
    }
    return {**body, "digest": digest(body)}


def load(root: Path) -> dict:
    try:
        registry = json.loads((root / REGISTRY).read_text())
        declarations = json.loads((root / DECLARATIONS).read_text())
        actual = discover(root, declarations)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise ValueError("READER_REGISTRY_STALE") from None
    if registry != actual:
        raise ValueError("READER_REGISTRY_STALE")
    if any(not (root / name).is_file() for names in registry["readers"].values() for name in names):
        raise ValueError("READER_UNCLASSIFIED")
    return registry


def select(path: str, registry: dict) -> list[str]:
    family = resource_family(path)
    if family not in registry["families"]:
        raise ValueError("READER_UNCLASSIFIED")
    matching = {
        name
        for pattern, names in registry["readers"].items()
        if fnmatch.fnmatchcase(path, pattern)
        for name in names
    }
    if matching:
        return sorted(matching)
    # Unknown path in a completely discovered/declared family is not no-reader
    # evidence: select the entire audited family, including directory readers.
    return sorted(
        {
            name
            for pattern, names in registry["readers"].items()
            if resource_family(pattern) == family
            for name in names
        }
    )

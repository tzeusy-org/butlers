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
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            patterns.update(match.group().rstrip("./") for match in REFERENCE.finditer(node.value))
            if node.value.rstrip("/") in {f.rstrip("/") for f in FAMILIES}:
                patterns.add(node.value.rstrip("/") + "/**")
            if re.fullmatch(r"[A-Z][\w-]*\.md", node.value):
                patterns.add(node.value)
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
        reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr
                    in {"read_text", "read_bytes", "open", "glob", "rglob", "iterdir", "get_data"}
                )
                or (isinstance(node.func, ast.Name) and node.func.id == "open")
            )
        ]
        if reads and name not in dynamic:
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

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
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

FAMILIES = ("docs/", "about/", "openspec/", ".claude/skills/", "frontend/", "deploy/")
REGISTRY = "scripts/test-resource-readers.json"
DECLARATIONS = "scripts/test-resource-reader-declarations.json"
REFERENCE = re.compile(r"(?:docs|about|openspec|frontend|deploy|\.claude/skills)/[\w./*{}-]+")
_DISCOVERY_CACHE: OrderedDict[str, dict] = OrderedDict()


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
        ["git", "ls-files", "--stage", "-z", "--", "tests", "roster"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=10,
    )
    sources = []
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        if not path.endswith(b".py") or not (path.startswith(b"tests/") or b"/tests/" in path):
            continue
        # An unresolved merge yields multiple index stages for the same path.
        # Refuse before discovery/cache admission, even if the working body has
        # already been edited into valid Python but has not yet been staged.
        if metadata.rsplit(b" ", 1)[-1] != b"0":
            raise ValueError("READER_UNCLASSIFIED")
        sources.append(path.decode())
    return sorted(sources)


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


def helper_callers(trees: dict[str, ast.AST], helper: str) -> list[str]:
    """Conservative static/literal importer census for a declared helper.

    Include literal nested source and file-loader references. This census is
    not arbitrary dynamic-import proof; a declaration also freezes the actual
    reviewed caller bodies, and undeclared helpers keep their parent fallback.
    """
    module = helper.removesuffix(".py").replace("/", ".")
    short_module = Path(helper).stem
    filename = Path(helper).name
    callers = []
    for name, tree in trees.items():
        if name == helper:
            continue
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= any(alias.name in {module, short_module} for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    parents = name.removesuffix(".py").split("/")[: -node.level]
                    prefix = ".".join([*parents, prefix]).rstrip(".")
                found |= prefix in {module, short_module} or any(
                    f"{prefix}.{alias.name}" == module for alias in node.names
                )
            else:
                literal = literal_string(node)
                if literal is not None:
                    found |= module in literal or helper in literal or filename in literal
        if found:
            callers.append(name)
    return sorted(callers)


def _body_bindings(root: Path, witnesses: object, sources: list[str]) -> bool:
    if not isinstance(witnesses, dict) or not witnesses:
        return False
    return all(
        name in sources
        and isinstance(value, str)
        and hashlib.sha256((root / name).read_bytes()).hexdigest() == value
        for name, value in witnesses.items()
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
    owners = declarations.get("helper_owners", {})
    if not isinstance(owners, dict) or any(name not in sources for name in owners):
        raise ValueError("READER_UNCLASSIFIED")
    # Replay batches share AST work, never source validation. Read/hash EVERY
    # current visited body and physical consumer path on each call. A changed
    # body/declaration/importer or newly present test cannot reuse old discovery.
    raw_bodies = {name: (root / name).read_bytes() for name in sources}
    identities = {name: hashlib.sha256(raw).hexdigest() for name, raw in raw_bodies.items()}
    cache_key = digest(
        {
            "root": str(root.resolve()),
            "declarations": declarations,
            "bodies": identities,
            "consumer_paths": sorted(
                str(p.relative_to(root))
                for directory in (root / "tests", root / "roster")
                for p in directory.rglob("test_*.py")
            ),
        }
    )
    if cache_key in _DISCOVERY_CACHE:
        _DISCOVERY_CACHE.move_to_end(cache_key)
        return deepcopy(_DISCOVERY_CACHE[cache_key])
    trees = {name: ast.parse(raw) for name, raw in raw_bodies.items()}
    bindings: dict[str, set[str]] = {}
    unresolved = []
    for name in sources:
        raw = raw_bodies[name]
        tree = trees[name]
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
        declaration = dynamic.get(name)
        if declaration is not None:
            if (
                not isinstance(declaration, dict)
                or not _body_bindings(root, declaration.get("body_sha256"), sources)
                or name not in declaration["body_sha256"]
            ):
                raise ValueError("READER_UNCLASSIFIED")
            declared = declaration.get("patterns")
        else:
            declared = []
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
        if name in owners:
            ownership = owners[name]
            if (
                not isinstance(ownership, dict)
                or not _body_bindings(root, ownership.get("body_sha256"), sources)
                or name not in ownership["body_sha256"]
            ):
                raise ValueError("READER_UNCLASSIFIED")
            actual_callers = helper_callers(trees, name)
            declared_callers = ownership.get("caller_sources")
            if (
                not actual_callers
                or declared_callers != actual_callers
                or any(caller not in ownership["body_sha256"] for caller in actual_callers)
            ):
                raise ValueError("READER_UNCLASSIFIED")
            selected = sorted(
                {target for caller in actual_callers for target in consumers(root, caller)}
            )
        else:
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
    result = {**body, "digest": digest(body)}
    _DISCOVERY_CACHE[cache_key] = deepcopy(result)
    if len(_DISCOVERY_CACHE) > 8:
        _DISCOVERY_CACHE.popitem(last=False)
    return result


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

"""Conservative, source-bound resource readers; never import a test to discover it.

Discovery is an over-approximation. Unknown file IO retains every public family
and an unproved helper frontier retains every test. Reviewed declarations may
refine those boundaries only while all their source witnesses remain exact.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

from butlers.testing.changed_files import git_environment

FAMILIES = ("docs/", "about/", "openspec/", ".claude/skills/", "frontend/", "deploy/")
REGISTRY = "scripts/test-resource-readers.json"
DECLARATIONS = "scripts/test-resource-reader-declarations.json"
READ_METHODS = {"read_text", "read_bytes", "open", "glob", "rglob", "iterdir"}
UNKNOWN = "\0unresolved"
ALL_TESTS = "@all-tests"
_READ_CACHE: dict[tuple[str, str], tuple[frozenset[str], frozenset[str], bool]] = {}


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate reader evidence key")
            result[key] = value
        return result

    value = json.loads(path.read_text(), object_pairs_hook=unique)
    if type(value) is not dict:
        raise ValueError("reader evidence representation unavailable")
    return value


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def safe_path(path: str) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and len(path) <= 4096
        and not (
            path.startswith("/")
            or "\\" in path
            or any(ord(c) < 32 for c in path)
            or any(p in {".", "..", ""} for p in path.rstrip("/").split("/"))
        )
    )


def family(path: str) -> str | None:
    return next(
        (prefix for prefix in FAMILIES if path.startswith(prefix) or path == prefix.rstrip("/")),
        "*.md" if "/" not in path and path.endswith(".md") else None,
    )


def _git(root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, timeout=30, env=git_environment()
    )
    if result.returncode:
        raise ValueError("reader source index unavailable")
    return [p.decode("utf-8", "strict") for p in result.stdout.split(b"\0") if p]


def sources(root: Path) -> list[str]:
    if _git(root, "ls-files", "-u", "-z"):
        raise ValueError("reader source index unresolved")
    names = _git(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        "tests",
        "roster",
        "scripts",
        "src",
        "conftest.py",
    )
    if any(
        (root / p).is_symlink() or not (root / p).resolve().is_relative_to(root.resolve())
        for p in names
        if p.endswith(".py")
    ):
        raise ValueError("reader source symlink unsupported")
    return sorted(
        {
            p
            for p in names
            if p.endswith(".py")
            and safe_path(p)
            and (root / p).is_file()
            and not (root / p).is_symlink()
        }
    )


def _test(path: str) -> bool:
    return (
        Path(path).name.startswith("test_")
        and path.endswith(".py")
        and (path.startswith("tests/") or (path.startswith("roster/") and "/tests/" in path))
    )


def _values(node: ast.AST, names: dict[str, set[str]], source: str) -> set[str]:
    """Finite path expressions only; unsupported expressions stay unknown."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value} if len(node.value) <= 4096 else {UNKNOWN}
    if isinstance(node, ast.Name):
        return {source} if node.id == "__file__" else names.get(node.id, {UNKNOWN})
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left, right = _values(node.left, names, source), _values(node.right, names, source)
        if UNKNOWN in left | right:
            return {UNKNOWN}
        if len(left) * len(right) > 128:
            return {UNKNOWN}
        return {
            str(PurePosixPath(a) / b) if isinstance(node.op, ast.Div) else a + b
            for a in left
            for b in right
        }
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        values = _values(node.value, names, source)
        return {UNKNOWN} if UNKNOWN in values else {str(PurePosixPath(p).parent) for p in values}
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "parents"
        and isinstance(node.slice, ast.Constant)
        and type(node.slice.value) is int
        and 0 <= node.slice.value < 20
    ):
        result = set()
        for p in _values(node.value.value, names, source):
            if p == UNKNOWN:
                return {UNKNOWN}
            parents = PurePosixPath(p).parents
            if node.slice.value < len(parents):
                result.add(str(parents[node.slice.value]))
        return result
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in {
            "Path",
            "PurePath",
            "PurePosixPath",
        }:
            return _values(node.args[0], names, source) if len(node.args) == 1 else {UNKNOWN}
        if isinstance(node.func, ast.Attribute):
            base = _values(node.func.value, names, source)
            if node.func.attr in {"resolve", "absolute"} and not node.args:
                return base
            if node.func.attr == "joinpath":
                for arg in node.args:
                    values = _values(arg, names, source)
                    if UNKNOWN in base | values:
                        return {UNKNOWN}
                    if len(base) * len(values) > 128:
                        return {UNKNOWN}
                    base = {str(PurePosixPath(a) / b) for a in base for b in values}
                return base
    return {UNKNOWN}


def _bindings(tree: ast.AST, source: str) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {
        n.arg: {UNKNOWN} for n in ast.walk(tree) if isinstance(n, ast.arg)
    }
    assignments = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))]
    # A finite union across scopes over-selects shadowed names; it cannot erase an edge.
    for _ in range(8):
        before = {k: set(v) for k, v in names.items()}
        for node in assignments:
            if node.value is None:
                continue
            values = _values(node.value, names, source)
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    names.setdefault(target.id, set()).update(values)
        if names == before:
            break
    return names


def _reads(tree: ast.AST, source: str) -> tuple[set[str], set[str], bool]:
    names = _bindings(tree, source)
    patterns, candidates, unknown = set(), set(), False
    # Literal/path references are candidates even when another helper performs
    # the IO. A string is not runtime proof, but discarding it can omit a reader.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Constant, ast.BinOp)):
            for value in _values(node, names, source):
                value = value.removeprefix("./")
                if safe_path(value) and family(value):
                    candidates.add(
                        value.rstrip("/") + "/*"
                        if value.rstrip("/") in {p.rstrip("/") for p in FAMILIES}
                        else value
                    )
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    direct_methods = {id(n.func) for n in calls}
    if any(
        isinstance(n, ast.Attribute) and n.attr in READ_METHODS and id(n) not in direct_methods
        for n in ast.walk(tree)
    ):
        unknown = True  # A first-class IO method can escape to another caller.
    open_aliases = {"open"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"io", "builtins"}:
            open_aliases.update(n.asname or n.name for n in node.names if n.name == "open")
    for call in calls:
        method = (
            call.func.attr
            if isinstance(call.func, ast.Attribute)
            else (call.func.id if isinstance(call.func, ast.Name) else "")
        )
        if method not in READ_METHODS | open_aliases:
            continue
        if method in open_aliases and call.args:
            values = _values(call.args[0], names, source)
        elif isinstance(call.func, ast.Attribute):
            values = _values(call.func.value, names, source)
        else:
            values = set()
        if not values:
            unknown = True
        for value in values:
            value = value.removeprefix("./")
            if family(value):
                if method in {"glob", "rglob", "iterdir"}:
                    patterns.add(value.rstrip("/") + "/*")
                else:
                    patterns.add(value)
            elif not safe_path(value) or value in {".", ""}:
                unknown = True
        # A glob result or a function-supplied path may be read in a later call.
        # Its unresolved read keeps the whole family unless explicitly witnessed.
    return patterns, candidates, unknown


def _consumer_frontier(bodies: dict[str, bytes], tests: list[str], source: str) -> set[str] | None:
    """Conservative import consumers, including unresolved dynamic loaders.

    This is a lower bound checked against a reviewed, complete-source-bound
    declaration, never automatic permission to discard an unobserved consumer.
    Unknown loader names retain all tests rather than infer an empty frontier.
    """
    modules: dict[str, set[str]] = {}
    for path in bodies:
        name = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        name = name.removesuffix(".__init__")
        modules.setdefault(name, set()).add(path)
        if path.startswith("scripts/"):
            modules.setdefault(Path(path).stem, set()).add(path)
    edges: dict[str, set[str]] = {path: set() for path in bodies}
    opaque = set()
    for path, body in bodies.items():
        tree = ast.parse(body, filename=path)
        bindings = _bindings(tree, path)
        loader_aliases = {"__import__": "__import__", "eval": "eval", "exec": "exec"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "importlib",
                "importlib.util",
                "runpy",
            }:
                loader_aliases.update(
                    (alias.asname or alias.name, alias.name)
                    for alias in node.names
                    if alias.name
                    in {
                        "import_module",
                        "spec_from_file_location",
                        "run_path",
                        "run_module",
                    }
                )
        direct_calls = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        if any(
            id(node) not in direct_calls
            and (
                isinstance(node, ast.Attribute)
                and node.attr
                in {"import_module", "spec_from_file_location", "run_path", "run_module"}
                or isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in loader_aliases
            )
            for node in ast.walk(tree)
        ):
            # An assigned, passed or container-held loader can escape the
            # finite direct-call analysis. Never certify a smaller frontier.
            opaque.add(path)
        package = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        if Path(path).name != "__init__.py":
            package = package.rpartition(".")[0]
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
                if any(
                    name.split(".")[0] in {"butlers", "tests", "roster", "scripts"}
                    and name not in modules
                    for name in names
                ):
                    opaque.add(path)
            elif isinstance(node, ast.ImportFrom):
                prefix = package.split(".")
                if node.level:
                    prefix = prefix[: len(prefix) - node.level + 1]
                    base = ".".join([*prefix, *([node.module] if node.module else [])])
                else:
                    base = node.module or ""
                if (
                    base.split(".")[0] in {"butlers", "tests", "roster", "scripts"}
                    and base not in modules
                ) or (node.level and not base):
                    opaque.add(path)
                names = [base, *(base + "." + alias.name for alias in node.names)]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Also retain named importlib/monkeypatch/subprocess consumers.
                names = [node.value]
                if node.value in bodies:
                    edges[path].add(node.value)
            elif isinstance(node, ast.Call):
                method = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                method = loader_aliases.get(method, method)
                if method in {
                    "import_module",
                    "__import__",
                    "spec_from_file_location",
                    "run_path",
                    "run_module",
                    "eval",
                    "exec",
                }:
                    argument = 1 if method == "spec_from_file_location" else 0
                    if method in {"eval", "exec"} or len(node.args) <= argument:
                        opaque.add(path)
                    else:
                        values = _values(node.args[argument], bindings, path)
                        resolved = set()
                        for value in values:
                            if method in {"spec_from_file_location", "run_path"}:
                                if value in bodies:
                                    resolved.add(value)
                                else:
                                    opaque.add(path)
                            elif value in modules:
                                resolved.update(modules[value])
                            else:
                                opaque.add(path)
                        if not values:
                            opaque.add(path)
                        edges[path].update(resolved)
            for name in names:
                for end in range(1, len(name.split(".")) + 1):
                    edges[path].update(modules.get(".".join(name.split(".")[:end]), ()))
    # Pytest imports ancestor conftests even without an ordinary import edge.
    for test in tests:
        edges[test].update(
            path
            for path in bodies
            if Path(path).name == "conftest.py"
            and (Path(path).parent == Path(".") or Path(path).parent in Path(test).parents)
        )
    consumers = set()
    for test in tests:
        reached, pending = set(), [test]
        while pending:
            path = pending.pop()
            if path in reached:
                continue
            reached.add(path)
            pending.extend(edges[path] - reached)
        if reached & opaque:
            return None
        if source in reached:
            consumers.add(test)
    return consumers


def discover(root: Path, declarations: dict) -> dict:
    """Return fresh source bindings and every conservative consuming test file."""
    if not isinstance(declarations, dict):
        raise ValueError("reader declaration representation unavailable")
    paths = sources(root)
    bodies = {p: (root / p).read_bytes() for p in paths}
    hashes = {p: hashlib.sha256(b).hexdigest() for p, b in bodies.items()}
    source_bindings = {path: {"sha256": value} for path, value in hashes.items()}
    tests = sorted(p for p in paths if _test(p))
    if not tests or type(declarations.get("schema")) is not int or declarations["schema"] != 1:
        raise ValueError("reader declaration schema unavailable")
    overrides = declarations.get("dynamic", {})
    if not isinstance(overrides, dict) or set(overrides) - set(paths):
        raise ValueError("reader declaration source unavailable")
    literal_data = declarations.get("literal_data", {})
    if not isinstance(literal_data, dict) or set(literal_data) - set(paths):
        raise ValueError("literal-data declaration source unavailable")
    readers: dict[str, set[str]] = {}
    known_patterns: set[str] = set()
    global_patterns: set[str] = set()
    unresolved = []
    for path, body in bodies.items():
        key = (path, hashes[path])
        cached = _READ_CACHE.get(key)
        if cached is None:
            patterns, candidates, unknown = _reads(ast.parse(body, filename=path), path)
            cached = (frozenset(patterns), frozenset(candidates), unknown)
            if len(_READ_CACHE) >= 4096:
                _READ_CACHE.pop(next(iter(_READ_CACHE)))
            _READ_CACHE[key] = cached
        # Every call still rereads/hashes all bodies and all declarations.
        # Cache only immutable AST discovery; never selection or test results.
        patterns, candidates, unknown = set(cached[0]), set(cached[1]), cached[2]
        owners = [path] if _test(path) else tests
        frontier_proved = _test(path)
        if path in literal_data:
            entry = literal_data[path]
            if (
                not isinstance(entry, dict)
                or entry.get("witness") != {"sha256": hashes[path]}
                or not isinstance(entry.get("reason"), str)
                or not entry["reason"].strip()
                or not isinstance(entry.get("patterns"), list)
                or any(not isinstance(p, str) for p in entry["patterns"])
                or not set(entry["patterns"]) <= candidates
                or set(entry["patterns"]) & patterns
            ):
                raise ValueError("literal-data witnesses unavailable")
            candidates.difference_update(entry["patterns"])
        patterns.update(candidates)
        if path in overrides:
            entry = overrides[path]
            if not isinstance(entry, dict):
                raise ValueError("reader declaration representation unavailable")
            witnesses = entry.get("witnesses", {})
            if (
                not isinstance(witnesses, dict)
                or witnesses.get(path) != hashes[path]
                or any(hashes.get(p) != h for p, h in witnesses.items())
                or not isinstance(entry.get("reason"), str)
                or not entry["reason"].strip()
                or not isinstance(entry.get("patterns"), list)
                or any(not safe_path(p) or family(p.rstrip("*")) is None for p in entry["patterns"])
            ):
                raise ValueError("reader declaration witnesses unavailable")
            patterns.update(entry["patterns"])
            unknown = False
            if "consumers" in entry:
                consumers = entry["consumers"]
                frontier = _consumer_frontier(bodies, tests, path)
                if (
                    not isinstance(consumers, list)
                    or not consumers
                    or any(not isinstance(p, str) or p not in tests for p in consumers)
                    or len(consumers) != len(set(consumers))
                    or entry.get("source_set_digest") != digest(source_bindings)
                    or frontier is None
                    or not frontier <= set(consumers)
                ):
                    raise ValueError("reader consumer frontier unavailable")
                owners = consumers
                frontier_proved = True
        known_patterns.update(patterns)
        if unknown:
            # No guessed no-reader edge: unresolved inputs retain the complete
            # public universe, including top-level markdown and all helper owners.
            patterns.update(prefix + "*" for prefix in FAMILIES)
            patterns.add("*.md")
            unresolved.append(path)
        for pattern in patterns:
            readers.setdefault(pattern, set()).update(owners)
            if not frontier_proved:
                global_patterns.add(pattern)
    # Separate source names from explicitly labelled hash values. Bare hashes
    # assigned to token/credential-named source paths resemble secret material
    # to the unchanged contextual scanner; every hash still binds exact bytes.
    payload = {
        "schema": 1,
        "source_hashes": source_bindings,
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "declarations_digest": digest(declarations),
        "tests": tests,
        "readers": {
            k: [ALL_TESTS] if v == set(tests) else sorted(v) for k, v in sorted(readers.items())
        },
        "conservative_unknowns": unresolved,
        "no_readers": [],
    }
    payload["global_patterns"] = sorted(global_patterns)
    no_readers = declarations.get("no_readers", {})
    if not isinstance(no_readers, dict):
        raise ValueError("no-reader declaration unavailable")
    for path, entry in no_readers.items():
        if (
            not isinstance(entry, dict)
            or not safe_path(path)
            or family(path) is None
            or not path.endswith(".md")
            or entry.get("source_set_digest") != digest(source_bindings)
            or not isinstance(entry.get("reason"), str)
            or not entry["reason"].strip()
            or any(fnmatch.fnmatchcase(path, pattern) for pattern in known_patterns)
        ):
            raise ValueError("no-reader audit witnesses unavailable")
        payload["no_readers"].append(path)
    payload["no_readers"].sort()
    return {**payload, "digest": digest(payload)}


def load(root: Path) -> dict:
    """A stale or malformed registry never supplies selection authority."""
    declarations = read_json(root / DECLARATIONS)
    stored = read_json(root / REGISTRY)
    current = discover(root, declarations)
    if digest(stored) != digest(current):
        raise ValueError("reader registry stale")
    return current


def select(path: str, registry: dict) -> list[str]:
    if not safe_path(path) or family(path) is None:
        raise ValueError("resource family unclassified")
    if path in registry["no_readers"]:
        return []
    selected = {
        owner
        for pattern, owners in registry["readers"].items()
        if fnmatch.fnmatchcase(path, pattern)
        for owner in owners
    }
    if not selected:
        selected = {
            owner
            for pattern, owners in registry["readers"].items()
            if family(pattern) == family(path)
            for owner in owners
        }
    if ALL_TESTS in selected:
        selected.remove(ALL_TESTS)
        selected.update(registry["tests"])
    return sorted(selected)


def unbounded(path: str, registry: dict) -> bool:
    """Unproved helper ownership is complete-population fallback, not scope."""
    if path in registry["no_readers"]:
        return False
    matched = [p for p in registry["readers"] if fnmatch.fnmatchcase(path, p)]
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in registry["global_patterns"]) or (
        not matched
        and any(family(pattern) == family(path) for pattern in registry["global_patterns"])
    )

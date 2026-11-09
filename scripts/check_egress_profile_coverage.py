#!/usr/bin/env python3
"""Check reviewed source declarations against workload egress profiles (source only).

Discovery is deliberately conservative: Python AST literals/configuration names and
base_url factory arguments, TOML values, and URL-bearing deployment scalar lines.
The reviewed inventory supplies meaning/reachability; discovery never grants routes.
No imports of production modules, DNS, credentials, network, or cluster are needed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = "scripts/egress-profile-coverage.json"
PROFILES = "deploy/egress-profiles.toml"
SCHEMA = "butlers.egress-profile-coverage/v1"
URL = re.compile(r"(?:https?|wss?)://[^\s\"'<>]*")
CONFIG = re.compile(r"[A-Z][A-Z0-9_]*(?:URL|URI|HOST|ENDPOINT)\Z")
CLASSES = {"external", "internal", "site", "reference", "blocked", "dynamic"}
# These are code-owned discovery boundaries, not inventory-configurable exclusions.
PYTHON_ROOTS = ("src/butlers", "roster")
TOML_ROOTS = ("roster",)
VENDOR_FAMILIES = {
    "codex": {"provider-model", "auth", "http-ws", "plugin-tools"},
    "gemini": {"provider-model", "auth", "code-assist", "plugin-tools"},
    "google-auth-library": {"adc", "oauth", "external-identity", "aws", "impersonation"},
    "js-genai": {"provider-model", "upload", "ws"},
    "opencode": {"provider-model", "catalog", "auth", "http-ws", "plugin-tools"},
}
DEPLOY_SUFFIXES = {".yaml", ".yml", ".sh", ".tpl", ".template"}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def authority(url: str) -> str | None:
    """Only unambiguous complete literal authorities can be compared to profiles."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if not host or any(c in parsed.netloc for c in "{}$\\"):
            return None
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
        return f"{host}:{port}"
    except ValueError:
        return None


def stable_ast(value: object) -> object:
    """Canonicalize optional/empty fields identically on supported Python versions."""
    if isinstance(value, ast.AST):
        return {
            "node": type(value).__name__,
            "fields": {
                key: stable_ast(child)
                for key, child in ast.iter_fields(value)
                if child is not None and child != []
            },
        }
    if isinstance(value, list):
        return [stable_ast(child) for child in value]
    return value


def ast_digest(value: ast.AST) -> str:
    return digest(json.dumps(stable_ast(value), sort_keys=True).encode())


def resolver_digest(root: Path, path: str, symbol: str) -> str | None:
    if path not in {p.relative_to(root).as_posix() for p in root.glob("src/butlers/**/*.py")}:
        return None
    tree = ast.parse((root / path).read_text(encoding="utf-8"))

    def find(body: list[ast.stmt], prefix: str) -> ast.AST | None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = f"{prefix}.{node.name}" if prefix else node.name
                if name == symbol:
                    return node
                child = find(node.body, name)
                if child is not None:
                    return child
        return None

    node = find(tree.body, "")
    return ast_digest(node) if node is not None else None


def declarations(root: Path) -> dict[str, dict]:
    counts: Counter[str] = Counter()
    records: dict[str, dict] = {}

    def add(path: Path, scope: str, kind: str, value: str) -> None:
        relative = path.relative_to(root).as_posix()
        token = digest(value.encode())
        key = f"{relative}::{scope}::{kind}::{token}"
        counts[key] += 1
        records[key] = {
            "path": relative,
            "scope": scope,
            "kind": kind,
            "sha256": token,
            "authorities": sorted({a for u in URL.findall(value) if (a := authority(u))}),
            "config": value if kind == "config" else None,
        }

    for prefix in PYTHON_ROOTS:
        paths = sorted((root / prefix).rglob("*.py"))
        for path in paths:
            if "tests" in path.relative_to(root).parts or "testing" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parents = {
                child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
            }
            for node in ast.walk(tree):
                parent = parents.get(node)
                if isinstance(parent, ast.Expr):
                    owner = parents.get(parent)
                    if isinstance(
                        owner, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                    ):
                        if owner.body[0] is parent:
                            continue  # Actual AST docstrings, not arbitrary string expressions.
                scope = []
                cursor = node
                while cursor in parents:
                    cursor = parents[cursor]
                    if isinstance(cursor, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                        scope.append(cursor.name)
                name = ".".join(reversed(scope)) or "<module>"
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if URL.search(node.value):
                        add(path, name, "url", node.value)
                    if CONFIG.fullmatch(node.value):
                        add(path, name, "config", node.value)
                if isinstance(node, ast.keyword) and node.arg == "base_url":
                    add(path, name, "factory", json.dumps(stable_ast(node.value), sort_keys=True))
    for prefix in TOML_ROOTS:
        for path in sorted((root / prefix).rglob("*.toml")):
            if "tests" in path.parts:
                continue

            def walk(value: object, key: str, owner: Path = path) -> None:
                if isinstance(value, dict):
                    for k, v in value.items():
                        walk(v, f"{key}.{k}")
                elif isinstance(value, list):
                    for i, v in enumerate(value):
                        walk(v, f"{key}[{i}]")
                elif isinstance(value, str) and URL.search(value):
                    add(owner, key, "toml", value)

            walk(tomllib.loads(path.read_text(encoding="utf-8")), "$")
    # Helm templates cannot be parsed as YAML before rendering. Conservatively
    # inventory every URL-bearing noncomment scalar line, including templates;
    # its reviewed disposition is required even if it is only a link/help string.
    deployment_paths = set((root / "deploy").rglob("*")) | set(root.glob("docker-compose*.yml"))
    for path in sorted(deployment_paths):
        if not path.is_file() or path.suffix not in DEPLOY_SUFFIXES:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text and not text.startswith("#") and URL.search(text):
                add(path, "<deployment-scalar>", "deployment", text)
    for key, record in records.items():
        record["count"] = counts[key]
    return dict(sorted(records.items()))


def bundle_pins(root: Path) -> dict[str, str]:
    """Bind non-Python vendor/shared sources without pretending to parse an archive."""
    pins = {}
    base = root / "deploy/runtime-clis"
    for item in sorted(base.rglob("*")):
        # Installed dependency bytes have separate lock/artifact attestations.
        # An ordinary local npm install must not change the source inventory;
        # its owning package/series/private-import declarations remain pinned.
        if "node_modules" in item.relative_to(base).parts:
            continue
        if item.is_file() and item.suffix not in {".md", ".pem"}:
            pins[item.relative_to(root).as_posix()] = digest(item.read_bytes())
    return pins


def topology_pins(root: Path) -> dict[str, str]:
    """A changed role/module declaration invalidates reviewed workload reachability."""
    paths = set((root / "roster").glob("*/butler.toml"))
    paths.update((root / "deploy/helm/butlers/templates").glob("*.yaml"))
    paths.update((root / "deploy/helm/butlers/templates").glob("*.tpl"))
    paths.update(
        root / p
        for p in (
            "src/butlers/connector_catalog.py",
            "src/butlers/modules/__init__.py",
        )
    )
    return {
        p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(paths) if p.is_file()
    }


def vendor_resolvers(root: Path, rows: dict, profiles: dict) -> tuple[list[str], list[dict]]:
    """Check finite reviewed upstream interfaces against immutable archive/patch owners.

    Unpatched locator bytes were reviewed in the pinned archive, not reparsed in CI.
    Archive/series or any bundled input drift invalidates that review. Patched locator
    hashes additionally match the terminal complete-file hashes in the patch series.
    """
    errors, gaps = [], []
    expected = {
        f"{vendor}:{family}"
        for vendor, families in VENDOR_FAMILIES.items()
        if (root / f"deploy/runtime-clis/{vendor}/series.json").is_file()
        for family in families
    }
    if not isinstance(rows, dict) or set(rows) != expected:
        return ["missing/unknown named vendor resolver family"], []
    required = {
        "vendor",
        "family",
        "source",
        "archive_sha256",
        "commit",
        "source_tree_sha256",
        "config",
        "defaults",
        "workloads",
        "resolution",
        "reason",
        "catalogue",
    }
    for key, row in rows.items():
        if not isinstance(row, dict) or set(row) != required:
            errors.append(f"invalid vendor resolver shape: {key}")
            continue
        vendor, family = key.split(":", 1)
        series = json.loads((root / f"deploy/runtime-clis/{vendor}/series.json").read_text())
        if (
            row["vendor"],
            row["family"],
            row["archive_sha256"],
            row["commit"],
            row["source_tree_sha256"],
        ) != (
            vendor,
            family,
            series["archive"]["sha256"],
            series["commit"],
            series["source_tree_sha256"],
        ):
            errors.append(f"vendor archive/series resolver identity drift: {key}")
        source = row["source"]
        if not isinstance(source, dict) or set(source) != {"path", "symbol", "sha256", "binding"}:
            errors.append(f"missing vendor source locator: {key}")
            continue
        path = Path(source["path"])
        if (
            path.is_absolute()
            or ".." in path.parts
            or not source["symbol"]
            or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        ):
            errors.append(f"invalid vendor source locator: {key}")
        latest = {
            f["path"]: f["after_sha256"] for patch in series["patches"] for f in patch["files"]
        }
        binding = "patched" if source["path"] in latest else "archive"
        if source["binding"] != binding or (
            binding == "patched" and source["sha256"] != latest[source["path"]]
        ):
            errors.append(f"vendor owning resolver changed: {key}")
        if (
            not isinstance(row["config"], list)
            or not row["config"]
            or any(not isinstance(c, str) or not c.strip() for c in row["config"])
            or row["resolution"] != "exact-profile-only"
            or not row["reason"]
        ):
            errors.append(f"missing vendor configuration/resolution semantics: {key}")
        workloads = row["workloads"]
        if (
            not isinstance(workloads, list)
            or not workloads
            or len(set(workloads)) != len(workloads)
            or any(w not in profiles for w in workloads)
        ):
            errors.append(f"invalid vendor workload reachability: {key}")
            continue
        for workload in workloads:
            sources = [
                d
                for d in profiles[workload].get("dynamic", [])
                if d.get("source") == f"vendor:{vendor}"
            ]
            if len(sources) != 1 or not sources[0].get("reason"):
                errors.append(f"{workload} lacks declared vendor resolver: {key}")
            for url in row["defaults"]:
                endpoint = authority(url)
                if not endpoint:
                    errors.append(f"invalid vendor default authority: {key}")
                elif endpoint not in profiles[workload].get("external", []):
                    gaps.append({"resolver": key, "workload": workload, "default": endpoint})
        if key == "opencode:catalog":
            catalogue = json.loads(
                (root / "deploy/runtime-clis/opencode/build-inputs.json").read_text()
            )["catalogue"]
            if row["catalogue"] != catalogue:
                errors.append("complete provider/model catalogue identity drift")
        elif row["catalogue"] is not None:
            errors.append(f"unexpected model catalogue: {key}")
    return errors, gaps


def local_resolvers(root: Path, rows: dict, profiles: dict) -> list[str]:
    """Name the supported native Claude boundary, without claiming opaque tool coverage."""
    owners = {
        "claude:provider-base": ("src/butlers/egress/claude.py", "child_environment"),
        "claude:auth-native": (
            "src/butlers/core/runtimes/claude_code.py",
            "ClaudeCodeAdapter.invoke",
        ),
        "claude:plugin-tools": (
            "src/butlers/core/runtimes/claude_code.py",
            "ClaudeCodeAdapter.build_config_file",
        ),
    }
    expected = {k for k, (path, _) in owners.items() if (root / path).is_file()}
    if not isinstance(rows, dict) or set(rows) != expected:
        return ["missing/unknown native caller resolver family"]
    errors = []
    for key, row in rows.items():
        path, symbol = owners[key]
        if not isinstance(row, dict) or set(row) != {
            "path",
            "symbol",
            "sha256",
            "config",
            "workloads",
            "resolution",
            "reason",
        }:
            errors.append(f"invalid native caller resolver: {key}")
            continue
        if (
            row["path"] != path
            or row["symbol"] != symbol
            or row["sha256"] != resolver_digest(root, path, symbol)
        ):
            errors.append(f"native caller resolver drift: {key}")
        if (
            not row["config"]
            or not row["reason"]
            or row["resolution"] != "no-new-route; native-auth-tools-unproven"
        ):
            errors.append(f"missing native caller held semantics: {key}")
        workloads = row["workloads"]
        if (
            not isinstance(workloads, list)
            or not workloads
            or any(w not in profiles for w in workloads)
        ):
            errors.append(f"invalid native caller workload: {key}")
            continue
        for workload in workloads:
            sources = [
                d
                for d in profiles[workload].get("dynamic", [])
                if d.get("source") == "local:claude"
            ]
            if len(sources) != 1 or not sources[0].get("reason"):
                errors.append(f"{workload} lacks explicit native caller declaration: {key}")
    return errors


def check(root: Path, inventory: dict) -> tuple[list[str], dict]:
    errors = []
    if (
        set(inventory)
        != {
            "schema",
            "declarations",
            "bundles",
            "bundle_policy",
            "topology",
            "vendor_resolvers",
            "local_resolvers",
        }
        or inventory.get("schema") != SCHEMA
    ):
        return ["invalid coverage inventory schema/keys"], {}
    observed = declarations(root)
    reviewed = inventory["declarations"]
    if not observed:
        errors.append("zero declarations discovered")
    if not isinstance(reviewed, dict):
        return errors + ["declarations must be an object"], {}
    for key in observed.keys() - reviewed.keys():
        errors.append(f"unreviewed declaration: {key}")
    for key in reviewed.keys() - observed.keys():
        errors.append(f"stale declaration: {key}")
    profiles = tomllib.loads((root / PROFILES).read_text(encoding="utf-8"))["workloads"]
    required = {"observed", "class", "workloads", "reason", "sites", "resolver"}
    gaps = []
    for key in observed.keys() & reviewed.keys():
        row = reviewed[key]
        if (
            not isinstance(row, dict)
            or set(row) != required
            or row.get("observed") != observed[key]
        ):
            errors.append(f"declaration shape/count/content drift: {key}")
            continue
        disposition = row["class"]
        workloads = row["workloads"]
        if (
            disposition not in CLASSES
            or not isinstance(row["reason"], str)
            or not row["reason"].strip()
        ):
            errors.append(f"missing reviewed disposition/reason: {key}")
            continue
        if (
            not isinstance(workloads, list)
            or len(set(workloads)) != len(workloads)
            or any(w not in profiles for w in workloads)
        ):
            errors.append(f"unknown/duplicate workload: {key}")
            continue
        if disposition != "reference" and not workloads:
            errors.append(f"missing workload reachability: {key}")
        if not isinstance(row["sites"], list):
            errors.append(f"sites must be a list: {key}")
            continue
        if disposition == "dynamic":
            resolver = row["resolver"]
            if not isinstance(resolver, dict) or set(resolver) != {
                "path",
                "symbol",
                "sha256",
                "source",
            }:
                errors.append(f"missing named owning resolver: {key}")
                continue
            expected = resolver_digest(root, resolver["path"], resolver["symbol"])
            source = observed[key]["config"] or f"{resolver['path']}:{resolver['symbol']}"
            if expected is None or expected != resolver["sha256"] or resolver["source"] != source:
                errors.append(f"unknown/changed resolver or configuration identity: {key}")
                continue
            for workload in workloads:
                sources = [
                    d for d in profiles[workload].get("dynamic", []) if d.get("source") == source
                ]
                if (
                    len(sources) != 1
                    or not isinstance(sources[0].get("reason"), str)
                    or not sources[0]["reason"].strip()
                ):
                    errors.append(f"{workload} lacks explicit dynamic resolver/source: {key}")
        elif row["resolver"] is not None:
            errors.append(f"resolver is only valid for dynamic declarations: {key}")
        if disposition == "external":
            if not observed[key]["authorities"]:
                errors.append(f"external declaration has no complete literal authority: {key}")
            for workload in workloads:
                for endpoint in observed[key]["authorities"]:
                    if endpoint not in profiles[workload].get("external", []):
                        errors.append(f"{workload} does not declare {endpoint}: {key}")
        if disposition == "site":
            for workload in workloads:
                declared = {s["site"] for s in profiles[workload].get("sites", [])}
                if not row["sites"] or not set(row["sites"]) <= declared:
                    errors.append(f"{workload} does not declare site peer: {key}")
        if disposition == "blocked":
            gaps.append({"declaration": key, "workloads": workloads, "reason": row["reason"]})
            for workload in workloads:
                if set(observed[key]["authorities"]) & set(profiles[workload].get("external", [])):
                    errors.append(f"blocked declaration was admitted without review: {key}")
    if topology_pins(root) != inventory["topology"]:
        errors.append(
            "workload topology drift; review declaration reachability before updating pins"
        )
    actual_pins = bundle_pins(root)
    if actual_pins != inventory["bundles"]:
        errors.append(
            "pinned non-Python vendor/shared bundle drift; "
            "review resolver inventory before updating pins"
        )
    if inventory["bundle_policy"] != {
        "scope": "pinned-source-review-required",
        "resolution": (
            "only exact workload profile origins admitted; "
            "every other configured provider/model/plugin origin remains denied"
        ),
        "limits": (
            "bundle digest drift is detected, not parsed upstream-host completeness "
            "or installed-client/runtime conformance"
        ),
    }:
        errors.append("bundle resolution policy missing/changed")
    vendor_errors, vendor_gaps = vendor_resolvers(root, inventory["vendor_resolvers"], profiles)
    errors.extend(vendor_errors)
    errors.extend(local_resolvers(root, inventory["local_resolvers"], profiles))
    return sorted(errors), {
        "declarations": len(observed),
        "bundle_inputs": len(actual_pins),
        "blocked_declarations": gaps,
        "vendor_resolvers": len(inventory["vendor_resolvers"]),
        "vendor_default_gaps": vendor_gaps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        inventory = json.loads((args.root / INVENTORY).read_text(encoding="utf-8"))
        errors, summary = check(args.root, inventory)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        print(f"egress profile coverage refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(json.dumps({"status": "SOURCE_ACCOUNTING_ONLY", **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

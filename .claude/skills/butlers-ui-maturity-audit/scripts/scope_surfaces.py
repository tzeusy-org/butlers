#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Phase-0 surface inventory for the butlers-ui-maturity-audit skill.

Deterministically enumerates the dashboard surfaces and the artifacts that define their
intended end-state, so the orchestrator can cluster them into user flows (clustering is
judgment and stays with the agent). Emits a markdown table by default.

Usage:
  uv run scripts/scope_surfaces.py [--repo-root PATH] [--since GITREF] [--json]

  --repo-root  repo root (default: cwd)
  --since      git ref to diff dashboard-* specs against (default: origin/main)
  --json       emit JSON instead of markdown

Reads only; never writes. Exits non-zero if router-config.tsx is absent (fail closed).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def _route_map(router: Path) -> list[dict]:
    """Parse `{ path: '...', element: <Page /> }` pairs from router-config.tsx."""
    text = router.read_text(encoding="utf-8", errors="replace")
    routes: list[dict] = []
    for m in re.finditer(r"path:\s*'([^']+)'.*?element:\s*<([A-Za-z0-9_]+)", text, re.DOTALL):
        # keep it line-local: skip matches that swallow multiple route objects
        if m.group(0).count("path:") == 1:
            routes.append({"path": m.group(1), "element": m.group(2)})
    return routes


def _feature_flags(repo: Path) -> list[str]:
    ff = repo / "frontend/src/lib/feature-flags.ts"
    if not ff.exists():
        return []
    return re.findall(r"export const ([A-Z0-9_]+)", ff.read_text(encoding="utf-8", errors="replace"))


def _git(repo: Path, *args: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=30,
        )
        return [ln for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--since", default="origin/main")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    router = repo / "frontend/src/router-config.tsx"
    if not router.exists():
        print(f"FAIL: {router} not found — run from the butlers repo root.", file=sys.stderr)
        return 2

    redesign_briefs = sorted(p.name for p in (repo / "docs/redesigns").glob("*.md")) \
        if (repo / "docs/redesigns").exists() else []

    change_dirs: list[str] = []
    for base in ("openspec/changes", "openspec/changes/archive"):
        d = repo / base
        if d.exists():
            change_dirs += sorted(
                f"{base}/{p.name}" for p in d.iterdir()
                if p.is_dir() and re.search(r"redesign|parity|lifecycle", p.name)
            )

    modified_specs = [
        ln for ln in _git(repo, "diff", "--name-only", args.since, "--", "openspec/specs")
        if "dashboard-" in ln
    ]

    data = {
        "routes": _route_map(router),
        "redesign_briefs": redesign_briefs,
        "openspec_change_dirs": change_dirs,
        "modified_dashboard_specs": modified_specs,
        "feature_flags": _feature_flags(repo),
    }

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"# Surface inventory ({repo})\n")
    print(f"## Routes ({len(data['routes'])})\n")
    print("| path | page element |\n|---|---|")
    for r in data["routes"]:
        print(f"| `{r['path']}` | {r['element']} |")
    print(f"\n## Redesign briefs ({len(redesign_briefs)})")
    for b in redesign_briefs:
        print(f"- docs/redesigns/{b}")
    print(f"\n## OpenSpec change dirs (redesign/parity/lifecycle) ({len(change_dirs)})")
    for c in change_dirs:
        print(f"- {c}")
    print(f"\n## Modified dashboard-* specs vs {args.since} ({len(modified_specs)})")
    for s in modified_specs:
        print(f"- {s}")
    print(f"\n## Feature flags (check prod default in docker-compose.yml)")
    for f in data["feature_flags"]:
        print(f"- {f}")
    print("\n> Cluster these surfaces into user flows (judgment — see references/user-flows.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

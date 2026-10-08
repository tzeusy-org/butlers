"""Stdlib-only conservative classification/planning; no testcase authority."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from butlers.testing.scoped_runner import (  # noqa: E402
    FULL_SUITE_FALLBACK_ALLOWLIST,
    plan_scoped_tests,
)


def route(
    *,
    event: str,
    ref: str,
    files: list[str] | None,
    docs: list[str],
    base: str | None,
    root: Path = ROOT,
    decision: dict | None = None,
) -> dict:
    """Missing classifier input cannot narrow; final unsupported events refuse."""
    if event == "merge_group" or (event == "push" and ref == "refs/heads/main"):
        return {
            "backend": "true",
            "frontend": "true",
            "mode": "full" if event == "merge_group" else "push",
            "test_paths": [],
            "inventory": "true",
        }
    if event != "pull_request":
        raise ValueError("event/ref has no admitted routing policy")
    if not files or any(
        not isinstance(name, str) or name.startswith("/") or ".." in name.split("/")
        for name in files
    ):
        return {
            "backend": "true",
            "frontend": "true",
            "mode": "full",
            "test_paths": [],
            "inventory": "true",
        }
    frontend = any(
        name.startswith(("frontend/", ".github/workflows/", "scripts/ci_", "scripts/check_ci_"))
        for name in files
    )
    result = {
        "backend": "true",
        "frontend": str(frontend).lower(),
        "mode": "full",
        "test_paths": [],
        "inventory": "true",
    }
    # Reader selection must run before a docs classifier can suppress Python.
    # Classifier labels select no tests and carry no completeness authority.
    if not base:
        return result
    try:
        plan = plan_scoped_tests(
            "HEAD",
            base=base,
            repo_dir=root,
            fallback_allowlist=FULL_SUITE_FALLBACK_ALLOWLIST + ("tests/e2e/",),
        )
        if decision is not None:
            decision.update(plan.data())
        if plan.scope == "none" and not plan.backend_applicable and set(files) <= set(docs):
            result.update(backend="false", mode="docs", inventory="false")
        elif (
            plan.scope == "scoped"
            and plan.test_paths
            and all((root / path).exists() for path in plan.test_paths)
        ):
            unchanged = all(
                name.startswith("src/")
                and name.endswith(".py")
                and not name.startswith(
                    ("src/butlers/testing/", "src/butlers/core/", "src/butlers/modules/registry")
                )
                for name in files
            )
            result.update(
                mode="scoped",
                inventory="false" if unchanged else "true",
                test_paths=plan.test_paths,
            )
    except (OSError, ValueError, RuntimeError):
        if decision is not None:
            decision.update(mode="full", reason_codes=["BASE_UNAVAILABLE"])
    return result


def verdict(*, needs: dict, event: str, ref: str) -> bool:
    """Validate the required mode/result pairs; additional needs must succeed."""
    expected = {
        "route",
        "guards",
        "check-preflight",
        "check-unit",
        "check-integration",
        "check-affected",
    }
    if not isinstance(needs, dict) or not expected <= needs.keys():
        return False
    if any(
        not isinstance(value, dict)
        or value.get("result") not in {"success", "skipped"}
        or not isinstance(value.get("outputs"), dict)
        or any(not isinstance(item, str) for item in value["outputs"].values())
        for value in needs.values()
    ):
        return False
    if needs["route"]["result"] != "success" or needs["guards"]["result"] != "success":
        return False
    outputs = needs["route"]["outputs"]
    if outputs.get("backend") not in {"true", "false"} or outputs.get("frontend") not in {
        "true",
        "false",
    }:
        return False
    mode = outputs.get("mode")
    if event == "merge_group":
        if mode != "full" or outputs["backend"] != "true" or outputs["frontend"] != "true":
            return False
    elif event == "push" and ref == "refs/heads/main":
        if mode != "push" or outputs["backend"] != "true" or outputs["frontend"] != "true":
            return False
    elif event == "pull_request":
        if mode not in {"docs", "scoped", "full"} or (mode == "docs") != (
            outputs["backend"] == "false"
        ):
            return False
    else:
        return False
    try:
        paths = json.loads(outputs["test_paths"])
    except (KeyError, ValueError):
        return False
    if not isinstance(paths, list) or any(
        not isinstance(path, str)
        or not path.startswith(("tests/", "roster/"))
        or any(part in {".", ".."} for part in path.split("/"))
        or any(ord(character) < 32 for character in path)
        for path in paths
    ):
        return False
    if bool(paths) != (mode == "scoped"):
        return False
    inventories = (
        {"true", "false"} if mode == "scoped" else {"true" if mode in {"full", "push"} else "false"}
    )
    if outputs.get("inventory") not in inventories:
        return False
    results = {
        "route": "success",
        "guards": "success",
        "check-preflight": "success" if mode in {"full", "scoped"} else "skipped",
        "check-affected": "success" if mode == "scoped" else "skipped",
        "check-unit": "success" if mode == "full" else "skipped",
        "check-integration": "success" if mode == "full" else "skipped",
    }
    return all(job["result"] == results.get(name, "success") for name, job in needs.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verdict", action="store_true")
    args = parser.parse_args()
    try:
        if args.verdict:
            return (
                0
                if verdict(
                    needs=json.loads(os.environ["NEEDS_JSON"]),
                    event=os.environ["EVENT_NAME"],
                    ref=os.environ["REF"],
                )
                else 1
            )
        decision = {}
        result = route(
            event=os.environ["EVENT_NAME"],
            ref=os.environ["REF"],
            files=json.loads(os.environ.get("ALL_FILES") or "null"),
            docs=json.loads(os.environ.get("DOCS_FILES") or "[]"),
            base=os.environ.get("BASE_SHA"),
            decision=decision,
        )
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                for name, value in result.items():
                    stream.write(f"{name}={json.dumps(value) if name == 'test_paths' else value}\n")
        if decision:
            print(json.dumps({"planner": decision}, sort_keys=True))
        print(
            json.dumps(
                {name: value for name, value in result.items() if name != "test_paths"},
                sort_keys=True,
            )
        )
        return 0
    except (OSError, KeyError, ValueError, TypeError):
        print("::error::routing/verdict input unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

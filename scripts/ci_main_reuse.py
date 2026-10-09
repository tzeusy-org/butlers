"""Admit main-push duplicate-check reuse from genuine protected same-tree proof.

GitHub reads and ZIP members stay in memory. No log, exception text, credentials,
actor attributes, test names or provider data are persisted. Past receipts keep
their original identities; a separate current inventory/budget check is required.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import time
import zipfile
from pathlib import Path, PurePosixPath

from ci_frontend_evidence import identity as frontend_identity
from ci_partition import (
    DIMENSIONS,
    ROOT,
    check_budgets,
    checkout_identity,
    exact_json,
    reconcile,
    unique_object,
)
from ci_vitest import reconcile_identity

REPOSITORY = "tzeusy-org/butlers"
SHA = re.compile(r"[0-9a-f]{40}")
REQUIRED_ARTIFACTS = {
    "ci-inventory",
    "ci-vitest-1",
    "ci-vitest-2",
    "ci-frontend-build",
    *(
        f"ci-{lane}-{index}-test-evidence"
        for lane, count in DIMENSIONS.items()
        for index in range(1, count + 1)
    ),
}
REQUIRED_JOBS = {
    "route",
    "guards",
    "frontend-static",
    "frontend",
    "frontend-e2e",
    "frontend-vitest (1)",
    "frontend-vitest (2)",
    "check-preflight",
    "coverage",
    "check",
    *(
        f"check-{lane} ({index})"
        for lane, count in DIMENSIONS.items()
        for index in range(1, count + 1)
    ),
}


def require(value: bool) -> None:
    if not value:
        raise ValueError("protected main reuse unavailable")


def read_json(data: bytes) -> dict:
    value = json.loads(data, object_pairs_hook=unique_object)
    require(type(value) is dict)
    return value


class GitHub:
    def __init__(self) -> None:
        # Keep the entire main-only read inside the existing2min route job.
        self.deadline = time.monotonic() + 90

    def api(self, suffix: str, *, binary: bool = False):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0)
        child = subprocess.run(
            ["gh", "api", f"repos/{REPOSITORY}/{suffix}"],
            capture_output=True,
            timeout=min(10, remaining),
        )
        require(child.returncode == 0 and len(child.stdout) <= 32 * 1024 * 1024)
        if binary:
            return child.stdout
        value = json.loads(child.stdout, object_pairs_hook=unique_object)
        require(type(value) in (dict, list))
        return value

    def rows(self, suffix: str, field: str, *, complete: bool = True) -> list[dict]:
        result = []
        for page in range(1, 4):
            response = self.api(f"{suffix}{'&' if '?' in suffix else '?'}per_page=100&page={page}")
            rows = response[field]
            require(type(rows) is list and all(type(row) is dict for row in rows))
            result.extend(rows)
            if len(rows) < 100:
                return result
        if complete:
            raise ValueError("bounded protected input horizon incomplete")
        return result


def current_context(root: Path) -> dict:
    # The runner's actual event file and independently fetched run metadata must
    # agree. A CLI caller's main string cannot itself authorize reuse.
    require(os.environ.get("GITHUB_EVENT_NAME") == "push")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main")
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY)
    require(os.environ.get("GITHUB_WORKFLOW") == "CI")
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]{0,19}", os.environ.get(name, "")) is not None)
    event = read_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    policy = checkout_identity(root)
    require(event.get("ref") == "refs/heads/main" and event.get("after") == policy["head"])
    require(event.get("repository", {}).get("full_name") == REPOSITORY)
    require(SHA.fullmatch(policy["head"]) is not None and SHA.fullmatch(policy["tree"]) is not None)
    return policy


def metadata(run: dict, *, event: str, workflow_id: int) -> dict:
    require(type(run.get("id")) is int and run["id"] > 0)
    require(type(run.get("run_attempt")) is int and run["run_attempt"] > 0)
    require(
        type(run.get("workflow_id")) is int
        and run["workflow_id"] == workflow_id
        and run.get("name") == "CI"
    )
    require(run.get("repository", {}).get("full_name") == REPOSITORY and run.get("event") == event)
    require(type(run.get("head_sha")) is str and SHA.fullmatch(run["head_sha"]) is not None)
    return {
        "repository": REPOSITORY,
        "workflow": "CI",
        "workflow_id": workflow_id,
        "run": run["id"],
        "attempt": run["run_attempt"],
        "event": event,
        "head": run["head_sha"],
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
    }


def read_member(blob: bytes, name: str) -> dict:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        members = [item for item in archive.infolist() if item.filename == name]
        require(len(members) == 1 and members[0].file_size <= 16 * 1024 * 1024)
        return read_json(archive.read(members[0]))


def verify_build_archive(blob: bytes, build: dict) -> dict:
    """Hash every regular dist member in RAM; no extraction or path traversal."""
    observed = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        require(
            len(archive.infolist()) <= 10000
            and sum(m.file_size for m in archive.infolist()) <= 128 * 1024 * 1024
        )
        for member in archive.infolist():
            name = member.filename
            if name == "receipt.json" or member.is_dir():
                continue
            path = PurePosixPath(name)
            require(path.as_posix() == name and not path.is_absolute() and ".." not in path.parts)
            require(path.parts[0] == "dist" and len(path.parts) > 1)
            # ZIP Unix mode must not introduce symlinks or other special members.
            mode = (member.external_attr >> 16) & 0o170000
            require(mode in (0, 0o100000) and member.file_size <= 16 * 1024 * 1024)
            relative = str(path.relative_to("dist"))
            require(relative not in observed)
            observed[relative] = hashlib.sha256(archive.read(member)).hexdigest()
    require(bool(observed) and exact_json(observed, build["files"]))
    return observed


def validate_package(package: dict, current: dict, *, root: Path = ROOT) -> None:
    require(
        type(package) is dict
        and set(package)
        == {
            "schema",
            "main",
            "protected",
            "protected_tree",
            "merged_main",
            "merged_PR",
            "jobs",
            "inventory",
            "assignment",
            "children",
            "Vitest",
            "build",
            "artifacts",
            "verified_build_files",
        }
    )
    require(package.get("schema") == "ci-protected-main-reuse.v1")
    traces = package["artifacts"]
    require(type(traces) is dict and set(traces) == REQUIRED_ARTIFACTS)
    for trace in traces.values():
        require(type(trace) is dict and set(trace) == {"id", "size", "sha256"})
        require(type(trace["id"]) is int and trace["id"] > 0)
        require(type(trace["size"]) is int and 0 < trace["size"] <= 32 * 1024 * 1024)
        require(
            type(trace["sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", trace["sha256"]) is not None
        )
    require(
        exact_json(
            package.get("main"),
            {
                k: current[k]
                for k in (
                    "repository",
                    "workflow",
                    "run",
                    "attempt",
                    "event",
                    "head",
                    "tree",
                    "config_digest",
                    "lock_digest",
                    "selectors",
                )
            },
        )
    )
    protected = package["protected"]
    require(
        type(protected) is dict
        and set(protected)
        == {
            "repository",
            "workflow",
            "workflow_id",
            "run",
            "attempt",
            "event",
            "head",
            "status",
            "conclusion",
        }
    )
    require(type(protected["workflow_id"]) is int and protected["workflow_id"] > 0)
    require(protected["repository"] == REPOSITORY and protected["workflow"] == "CI")
    require(
        protected["event"] == "merge_group"
        and protected["status"] == "completed"
        and protected["conclusion"] == "success"
    )
    require(
        type(protected["run"]) is int
        and type(protected["attempt"]) is int
        and protected["run"] > 0
        and protected["attempt"] > 0
    )
    require(
        SHA.fullmatch(protected["head"]) is not None
        and package["protected_tree"] == current["tree"]
    )
    require(
        package["merged_main"] == current["head"]
        and type(package["merged_PR"]) is int
        and package["merged_PR"] > 0
    )
    jobs = package["jobs"]
    require(type(jobs) is dict and set(jobs) == REQUIRED_JOBS)
    require(
        all(
            exact_json(value, {"status": "completed", "conclusion": "success"})
            for value in jobs.values()
        )
    )
    inventory, assignment = package["inventory"], package["assignment"]
    expected = {
        **current,
        "head": protected["head"],
        "run": str(protected["run"]),
        "attempt": str(protected["attempt"]),
        "event": "merge_group",
        "python": "3.12.15",
    }
    require(exact_json(inventory.get("identity"), expected))
    # Historical protected receipts are validated explicitly in their original
    # identity, then independently checked against CURRENT source/tree policy.
    # This is never a current-main Python execution claim.
    reconcile(inventory, assignment, package["children"], root=root, current=False)
    front = {
        **frontend_identity(root),
        "head": protected["head"],
        "run": str(protected["run"]),
        "attempt": str(protected["attempt"]),
    }
    reconcile_identity(root, package["Vitest"], front)
    build = package["build"]
    require(
        build.get("schema") == "ci-frontend-build.v1" and exact_json(build.get("identity"), front)
    )
    require(type(build.get("files")) is dict and bool(build["files"]))
    require(exact_json(build["files"], package["verified_build_files"]))
    require(
        all(
            type(name) is str and type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest)
            for name, digest in build["files"].items()
        )
    )


def qualify(*, root: Path = ROOT, api: GitHub | None = None) -> dict:
    current = current_context(root)
    github = api if api is not None else GitHub()
    workflow = github.api("actions/workflows/ci.yml")
    require(workflow.get("path") == ".github/workflows/ci.yml" and type(workflow.get("id")) is int)
    own = github.api(f"actions/runs/{current['run']}/attempts/{current['attempt']}")
    actual_main = metadata(own, event="push", workflow_id=workflow["id"])
    require(actual_main["head"] == current["head"] and own.get("head_branch") == "main")
    require(
        str(actual_main["run"]) == current["run"]
        and str(actual_main["attempt"]) == current["attempt"]
    )
    # Bind GitHub's actual main commit/tree, not a caller's file-count assertion.
    commit = github.api(f"git/commits/{current['head']}")
    require(
        commit.get("sha") == current["head"]
        and commit.get("tree", {}).get("sha") == current["tree"]
    )
    # The associated-PR endpoint returns an array. Keep bounded public fields in
    # memory and never persist its actor/commit message/body attributes.
    associated = github.api(f"commits/{current['head']}/pulls?per_page=100")
    require(type(associated) is list and len(associated) < 100)
    merged = [
        row
        for row in associated
        if type(row) is dict
        and row.get("merged_at")
        and row.get("merge_commit_sha") == current["head"]
        and row.get("base", {}).get("ref") == "main"
        and row.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY
    ]
    require(len(merged) == 1 and type(merged[0].get("number")) is int)
    runs = github.rows(
        "actions/workflows/ci.yml/runs?event=merge_group&status=success",
        "workflow_runs",
        complete=False,
    )
    matches = [
        row
        for row in runs
        if row.get("event") == "merge_group"
        and row.get("status") == "completed"
        and row.get("conclusion") == "success"
        and row.get("head_commit", {}).get("tree_id") == current["tree"]
    ]
    require(bool(matches))
    run = matches[0]
    protected = metadata(run, event="merge_group", workflow_id=workflow["id"])
    attempt = github.api(f"actions/runs/{protected['run']}/attempts/{protected['attempt']}")
    require(
        exact_json(metadata(attempt, event="merge_group", workflow_id=workflow["id"]), protected)
    )
    protected_commit = github.api(f"git/commits/{protected['head']}")
    require(
        protected_commit.get("sha") == protected["head"]
        and protected_commit.get("tree", {}).get("sha") == current["tree"]
    )
    jobs = github.rows(
        f"actions/runs/{protected['run']}/attempts/{protected['attempt']}/jobs", "jobs"
    )
    required = {}
    for job in jobs:
        if job.get("name") in REQUIRED_JOBS:
            require(job["name"] not in required)
            required[job["name"]] = {k: job.get(k) for k in ("status", "conclusion")}
    artifacts = github.rows(f"actions/runs/{protected['run']}/artifacts", "artifacts")
    selected = {}
    for artifact in artifacts:
        if artifact.get("name") in REQUIRED_ARTIFACTS:
            require(artifact["name"] not in selected and artifact.get("expired") is False)
            require(
                type(artifact.get("id")) is int
                and type(artifact.get("size_in_bytes")) is int
                and 0 < artifact["size_in_bytes"] <= 32 * 1024 * 1024
            )
            selected[artifact["name"]] = artifact
    require(set(selected) == REQUIRED_ARTIFACTS)
    archives = {}
    for name, artifact in selected.items():
        blob = github.api(f"actions/artifacts/{artifact['id']}/zip", binary=True)
        require(type(blob) is bytes and len(blob) == artifact["size_in_bytes"])
        require(artifact.get("digest") == "sha256:" + hashlib.sha256(blob).hexdigest())
        archives[name] = blob
    package = {
        "schema": "ci-protected-main-reuse.v1",
        "main": {
            k: current[k]
            for k in (
                "repository",
                "workflow",
                "run",
                "attempt",
                "event",
                "head",
                "tree",
                "config_digest",
                "lock_digest",
                "selectors",
            )
        },
        "protected": protected,
        "protected_tree": current["tree"],
        "merged_main": current["head"],
        "merged_PR": merged[0]["number"],
        "jobs": required,
        "inventory": read_member(archives["ci-inventory"], "inventory.json"),
        "assignment": read_member(archives["ci-inventory"], "assignment.json"),
        "children": {
            name.removeprefix("ci-").removesuffix("-test-evidence"): read_member(
                blob, "shard-observation.json"
            )
            for name, blob in archives.items()
            if name.endswith("-test-evidence")
        },
        "Vitest": [read_member(archives[f"ci-vitest-{s}"], "receipt.json") for s in (1, 2)],
        "build": read_member(archives["ci-frontend-build"], "receipt.json"),
        "artifacts": {
            name: {
                "id": selected[name]["id"],
                "size": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
            for name, blob in archives.items()
        },
    }
    package["verified_build_files"] = verify_build_archive(
        archives["ci-frontend-build"], package["build"]
    )
    validate_package(package, current, root=root)
    return package


def verify_fresh(package: dict, inventory: dict, *, root: Path = ROOT) -> None:
    current = current_context(root)
    validate_package(package, current, root=root)
    check_budgets(inventory, root=root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("qualify", "fresh"))
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "qualify":
            package = qualify()
            args.proof.parent.mkdir(parents=True, exist_ok=True)
            args.proof.write_text(json.dumps(package, sort_keys=True) + "\n")
            with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                stream.write("verified=true\n")
        else:
            require(args.inventory is not None)
            verify_fresh(read_json(args.proof.read_bytes()), read_json(args.inventory.read_bytes()))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
        AttributeError,
    ):
        print("::error::protected same-tree main reuse unavailable")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

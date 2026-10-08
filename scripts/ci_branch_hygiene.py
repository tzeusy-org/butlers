#!/usr/bin/env python3
"""Prepare recoverable branch/cache hygiene; live apply requires an exact root approval.

This is a trusted-operator tool, not an approval service. Its approval record is
external human/coordinator evidence, never a new security principal or signature.
No API stderr, credential-bearing remote URL or PR text is emitted. See the
stale-branch-hygiene runbook before using apply/remote restoration.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from butlers.core.git_custody import CustodyUnavailable, branch_exclusion

REPOSITORY = "tzeusy-org/butlers"
PREFIXES = ("agent/", "qa/", "fix/", "codex/")


class Refusal(RuntimeError):
    """A categorical incomplete/unsafe operation; provider text is never copied."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def instant(value: str) -> datetime:
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise Refusal("timezone_required")
    return date.astimezone(UTC)


def run(argv: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=180, check=True)
        return result.stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise Refusal("command_unavailable") from exc


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwriting an approval, plan or earlier stage receipt.
    with path.open("x") as file:
        json.dump(value, file, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())


class Git:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def call(self, *args: str) -> str:
        return run(["git", *args], self.root).strip()

    def require_public_origin(self) -> None:
        origin = self.call("remote", "get-url", "origin")
        allowed = (f"git@github.com:{REPOSITORY}", f"https://github.com/{REPOSITORY}")
        if origin.removesuffix(".git") not in allowed:
            raise Refusal("origin_repository_mismatch")

    def heads(self) -> dict[str, str]:
        pairs = self.call("ls-remote", "--heads", "origin").splitlines()
        return {line.split()[1].removeprefix("refs/heads/"): line.split()[0] for line in pairs}

    def common(self) -> Path:
        value = Path(self.call("rev-parse", "--git-common-dir"))
        return (self.root / value).resolve() if not value.is_absolute() else value.resolve()

    @contextlib.contextmanager
    def exclusion(self):
        try:
            with branch_exclusion(self.root):
                yield
        except CustodyUnavailable as exc:
            raise Refusal("custody_busy_or_unavailable") from exc

    def worktrees(self) -> list[dict[str, Any]]:
        rows = []
        for block in self.call("worktree", "list", "--porcelain").split("\n\n"):
            values = dict(
                line.split(" ", 1) if " " in line else (line, True) for line in block.splitlines()
            )
            if values.get("worktree"):
                rows.append(
                    {
                        "path": values["worktree"],
                        "sha": values.get("HEAD"),
                        "branch": str(values.get("branch", "")).removeprefix("refs/heads/"),
                        "locked": bool(values.get("locked")),
                    }
                )
        return rows

    def recover(self, name: str, sha: str, store: Path) -> dict[str, Any]:
        store = store.resolve()
        excluded = [self.common(), *(Path(x["path"]).resolve() for x in self.worktrees())]
        if any(store.is_relative_to(x) for x in excluded):
            raise Refusal("recovery_store_must_be_independent")
        store.mkdir(parents=True, exist_ok=True)
        filename = sha + "-" + digest(name)[:12] + ".bundle"
        bundle = store / filename
        if bundle.exists():
            raise Refusal("recovery_artifact_exists")
        with tempfile.TemporaryDirectory(dir=store) as temporary:
            repo = Path(temporary) / "objects.git"
            run(["git", "init", "--bare", str(repo)])
            # First fetch from the public origin into independent storage. It
            # reads the ref only; no checkout/ref in the source tree is altered.
            origin = self.call("remote", "get-url", "origin")
            run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "fetch",
                    "--no-tags",
                    origin,
                    f"refs/heads/{name}:refs/heads/recovery",
                ]
            )
            if run(["git", "-C", str(repo), "rev-parse", "refs/heads/recovery"]).strip() != sha:
                raise Refusal("recovery_head_changed")
            run(["git", "-C", str(repo), "bundle", "create", str(bundle), "--all"])
        value = {
            "path": str(bundle),
            "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "sha": sha,
        }
        self.verify_recovery(value)
        return value

    def verify_recovery(self, value: dict[str, Any]) -> None:
        bundle = Path(value["path"])
        if (
            bundle.is_symlink()
            or hashlib.sha256(bundle.read_bytes()).hexdigest() != value["sha256"]
        ):
            raise Refusal("recovery_digest_mismatch")
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "restored.git"
            run(["git", "clone", "--bare", str(bundle), str(repo)])
            if (repo / "objects/info/alternates").exists():
                raise Refusal("recovery_depends_on_alternates")
            run(["git", "-C", str(repo), "fsck", "--full"])
            if (
                run(["git", "-C", str(repo), "rev-parse", "refs/heads/recovery"]).strip()
                != value["sha"]
            ):
                raise Refusal("recovery_object_mismatch")

    def delete(self, name: str, expected: str) -> None:
        self.call(
            "push",
            f"--force-with-lease=refs/heads/{name}:{expected}",
            "origin",
            f":refs/heads/{name}",
        )

    def restore(self, name: str, recovery: dict[str, Any]) -> None:
        self.verify_recovery(recovery)
        if name in self.heads():
            raise Refusal("replacement_ref_exists")
        # Empty expected value means create-only; even a same-SHA replacement
        # is preserved. A separate restore approval is mandatory at the caller.
        with tempfile.TemporaryDirectory() as temporary:
            restored = Path(temporary) / "restore.git"
            run(["git", "clone", "--bare", recovery["path"], str(restored)])
            run(
                [
                    "git",
                    "-C",
                    str(restored),
                    "push",
                    f"--force-with-lease=refs/heads/{name}:",
                    self.call("remote", "get-url", "origin"),
                    f"refs/heads/recovery:refs/heads/{name}",
                ]
            )


class Github:
    def get(self, path: str, *, pages: bool = False) -> Any:
        args = ["gh", "api", f"repos/{REPOSITORY}/{path}"]
        if pages:
            args.extend(["--paginate", "--slurp"])
        try:
            value = json.loads(run(args))
        except json.JSONDecodeError as exc:
            raise Refusal("provider_malformed") from exc
        if pages:
            if not isinstance(value, list) or not all(isinstance(x, list) for x in value):
                raise Refusal("pagination_incomplete")
            return [row for page in value for row in page]
        return value

    def mutate(self, path: str, method: str, fields: dict[str, Any] | None = None) -> None:
        args = [
            "gh",
            "api",
            f"repos/{REPOSITORY}/{path}" if path else f"repos/{REPOSITORY}",
            "--method",
            method,
        ]
        for key, value in (fields or {}).items():
            args.extend(["-F", f"{key}={str(value).lower()}"])
        run(args)

    def repo(self) -> dict[str, Any]:
        value = json.loads(run(["gh", "api", f"repos/{REPOSITORY}"]))
        if value.get("full_name") != REPOSITORY or value.get("default_branch") != "main":
            raise Refusal("repository_identity_mismatch")
        return {
            "repository": REPOSITORY,
            "default_branch": "main",
            "delete_branch_on_merge": value["delete_branch_on_merge"],
        }

    def caches(self) -> list[dict[str, Any]]:
        # Object pagination differs from list endpoints. Reconcile the declared
        # count and duplicate IDs rather than credit an empty/partial page.
        rows = []
        total = None
        for page in range(1, 10001):
            value = self.get(f"actions/caches?per_page=100&page={page}")
            if total is None:
                total = value["total_count"]
            if total != value["total_count"]:
                raise Refusal("cache_inventory_changed")
            batch = value["actions_caches"]
            rows.extend(batch)
            if len(batch) < 100:
                if len(rows) != total or len({x["id"] for x in rows}) != total:
                    raise Refusal("cache_pagination_incomplete")
                return [
                    {
                        k: x[k]
                        for k in (
                            "id",
                            "ref",
                            "key",
                            "version",
                            "size_in_bytes",
                            "created_at",
                            "last_accessed_at",
                        )
                    }
                    for x in rows
                ]
        raise Refusal("cache_pagination_unbounded")


def custody_read(path: Path | None, now: datetime) -> dict[str, Any]:
    if path is None:
        return {"complete": False, "held_refs": [], "held_shas": [], "terminal_worktrees": []}
    value = json.loads(path.read_text())
    if value.get("repository") != REPOSITORY or not value.get("complete"):
        raise Refusal("custody_incomplete")
    age = now - instant(value["captured_at"])
    if not timedelta(0) <= age <= timedelta(minutes=5):
        raise Refusal("custody_snapshot_expired")
    # This is trusted root's attestation of otherwise non-enumerable foreign
    # custody, supplemented by actual canonical nonclosed and Git worktrees.
    return value


def snapshot(git: Git, api: Github, custody: Path | None, now: datetime) -> dict[str, Any]:
    repo = api.repo()
    heads = git.heads()
    branch_rows = api.get("branches?per_page=100", pages=True)
    if {x["name"]: x["commit"]["sha"] for x in branch_rows} != heads:
        raise Refusal("branch_inventory_changed")
    prs = api.get("pulls?state=all&per_page=100", pages=True)
    canonical = json.loads(run(["bd", "--readonly", "list", "--limit", "0", "--json"], git.root))
    if not isinstance(canonical, list):
        raise Refusal("canonical_custody_unavailable")
    lease = custody_read(custody, now)
    held = set(lease["held_refs"]) | {
        f"agent/{x['id']}" for x in canonical if x["status"] != "closed"
    }
    worktrees = git.worktrees()
    return {
        **repo,
        "captured_at": now.isoformat(),
        "complete": True,
        "custody_complete": lease.get("complete", False),
        "held_refs": sorted(held),
        "held_shas": lease["held_shas"],
        "terminal_worktrees": lease.get("terminal_worktrees", []),
        "ref_activity": lease.get("ref_activity", {}),
        "worktrees": worktrees,
        "branches": [
            {"name": x["name"], "sha": x["commit"]["sha"], "protected": x["protected"]}
            for x in branch_rows
        ],
        "prs": [
            {
                "number": x["number"],
                "state": x["state"],
                "name": x["head"]["ref"],
                "sha": x["head"]["sha"],
                "head_repository": (x["head"]["repo"] or {}).get("full_name"),
                "merged_at": x["merged_at"],
                "closed_at": x["closed_at"],
                "updated_at": x["updated_at"],
                "merge_sha": x["merge_commit_sha"],
            }
            for x in prs
        ],
        "caches": api.caches(),
        "usage": api.get("actions/cache/usage"),
    }


def classify(row: dict[str, Any], state: dict[str, Any], now: datetime, git: Git) -> str:
    name = row["name"]
    sha = row["sha"]
    if not state.get("complete") or not state.get("custody_complete"):
        return "excluded_incomplete_custody"
    if not name.startswith(PREFIXES) or row["protected"] or name == "main":
        return "excluded_protected_or_out_of_scope"
    if name in state["held_refs"] or sha in state["held_shas"]:
        return "excluded_custody"
    attached = [x for x in state["worktrees"] if x["sha"] == sha or x["branch"] == name]
    if any(x["path"] not in state["terminal_worktrees"] or x["locked"] for x in attached):
        return "excluded_worktree"
    matches = [x for x in state["prs"] if x["name"] == name and x["head_repository"] == REPOSITORY]
    if any(x["state"] == "open" for x in matches):
        return "excluded_open_pr"
    main = next(x["sha"] for x in state["branches"] if x["name"] == "main")
    try:
        git.call("merge-base", "--is-ancestor", sha, main)
    except Refusal:
        pass
    else:
        return "reachable_main_candidate"
    exact = [x for x in matches if x["sha"] == sha]
    if not exact:
        return "excluded_unknown_history"
    if any(x["merged_at"] and x["merge_sha"] for x in exact):
        return "merged_candidate"
    activity = state.get("ref_activity", {}).get(name)
    if not activity or activity.get("sha") != sha or not activity.get("source_receipt"):
        return "excluded_unknown_ref_activity"
    if not activity.get("complete"):
        return "excluded_unknown_ref_activity"
    # Conservative: every PR activity and actual remote-ref commit must meet
    # the minimum age. Missing newer head binding is UNKNOWN, never an age fix.
    dates = [instant(x[k]) for x in matches for k in ("updated_at", "closed_at") if x[k]]
    dates.append(instant(activity["last_activity_at"]))
    try:
        dates.append(instant(git.call("show", "-s", "--format=%cI", sha)))
    except Refusal:
        return "excluded_unknown_activity"
    return (
        "abandonment_candidate"
        if dates and max(dates) <= now - timedelta(days=14)
        else "excluded_recent"
    )


def plan(state: dict[str, Any], git: Git, store: Path, now: datetime) -> dict[str, Any]:
    branches = []
    excluded = []
    for row in state["branches"]:
        category = classify(row, state, now, git)
        if category.endswith("_candidate"):
            recovery = git.recover(row["name"], row["sha"], store)
            branches.append(
                {
                    **row,
                    "classification": category,
                    "recovery": recovery,
                    "bound_prs": [
                        x
                        for x in state["prs"]
                        if x["name"] == row["name"] and x["head_repository"] == REPOSITORY
                    ],
                    "worktrees": [
                        x
                        for x in state["worktrees"]
                        if x["branch"] == row["name"] or x["sha"] == row["sha"]
                    ],
                }
            )
        else:
            excluded.append({**row, "classification": category})
    active_refs = {f"refs/heads/{x['name']}" for x in state["prs"] if x["state"] == "open"} | {
        f"refs/heads/{x}" for x in state["held_refs"]
    }
    active_refs |= {f"refs/heads/{x['branch']}" for x in state["worktrees"] if x["branch"]}
    main = [
        x
        for x in state["caches"]
        if x["ref"] == "refs/heads/main" and x["key"].startswith("node-cache-")
    ]
    cache_rows = [
        x
        for x in state["caches"]
        if x["key"].startswith("node-cache-")
        and x["ref"].startswith("refs/heads/")
        and x["ref"] != "refs/heads/main"
        and x["ref"] not in active_refs
    ]
    if not state["custody_complete"]:
        cache_rows = []
    value = {
        "version": 1,
        "repository": REPOSITORY,
        "created_at": now.isoformat(),
        "snapshot_digest": digest(state),
        "branches": branches,
        "excluded": excluded,
        "caches": cache_rows,
        "main_caches": main,
        "setting_before": state["delete_branch_on_merge"],
        "under100_target": 100,
        "cache_byte_target": 10_000_000_000,
        "selected_deferrals": [],
        "no_wall_clock_gain": True,
    }
    value["digest"] = digest(value)
    return value


def authorize(value: dict[str, Any], approval: dict[str, Any], action: str) -> None:
    body = {k: v for k, v in value.items() if k != "digest"}
    if value.get("repository") != REPOSITORY or digest(body) != value.get("digest"):
        raise Refusal("plan_digest_mismatch")
    if (
        approval.get("digest") != value["digest"]
        or approval.get("action") != action
        or not approval.get("approval_reference")
    ):
        raise Refusal("exact_author_approval_required")
    if action == "apply" and {
        x["name"] for x in value["branches"] if x["classification"] == "abandonment_candidate"
    } - set(approval.get("approved_abandonments", [])):
        raise Refusal("exact_abandonment_approval_required")


def apply(
    value: dict[str, Any],
    approval: dict[str, Any],
    git: Git,
    api: Github,
    custody: Path,
    receipts: Path,
    now: datetime,
) -> dict[str, Any]:
    authorize(value, approval, "apply")
    results = []
    with git.exclusion():
        for index, row in enumerate(value["branches"]):
            current = snapshot(git, api, custody, datetime.now(UTC))
            found = next((x for x in current["branches"] if x["name"] == row["name"]), None)
            if found is None:
                results.append({"name": row["name"], "stage": "already_absent"})
                continue
            if (
                found["sha"] != row["sha"]
                or classify(found, current, datetime.now(UTC), git) != row["classification"]
                or [
                    x
                    for x in current["prs"]
                    if x["name"] == row["name"] and x["head_repository"] == REPOSITORY
                ]
                != row["bound_prs"]
            ):
                raise Refusal("branch_drift")
            git.verify_recovery(row["recovery"])
            write_new(
                receipts / f"branch-{index}-before.json",
                {"plan": value["digest"], "row": row, "stage": "before_mutation"},
            )
            for wt in row["worktrees"]:
                if (
                    wt["path"] not in current["terminal_worktrees"]
                    or wt not in current["worktrees"]
                ):
                    raise Refusal("worktree_custody_changed")
                if run(["git", "-C", wt["path"], "status", "--porcelain"]):
                    raise Refusal("worktree_dirty")
                git.call("worktree", "remove", wt["path"])
            local = git.call("for-each-ref", "--format=%(objectname)", f"refs/heads/{row['name']}")
            if local and local != row["sha"]:
                raise Refusal("local_ref_changed")
            if local:
                git.call("update-ref", "-d", f"refs/heads/{row['name']}", row["sha"])
            try:
                git.delete(row["name"], row["sha"])
                stage = "deleted"
            except Refusal:
                try:
                    head = git.heads().get(row["name"])
                    stage = (
                        "absent_after_unknown_ack"
                        if head is None
                        else (
                            "same_after_unknown_ack"
                            if head == row["sha"]
                            else "changed_after_unknown_ack"
                        )
                    )
                except Refusal:
                    stage = "unknown"
            result = {"name": row["name"], "stage": stage}
            write_new(receipts / f"branch-{index}-after.json", result)
            results.append(result)
            if stage not in ("deleted", "absent_after_unknown_ack"):
                raise Refusal("branch_mutation_not_confirmed")
        for index, row in enumerate(value["caches"]):
            current = snapshot(git, api, custody, datetime.now(UTC))
            refreshed = plan_cache_candidates(current)
            if (
                row not in refreshed
                or not value["main_caches"]
                or not all(
                    cache_identity(x) in [cache_identity(y) for y in current["caches"]]
                    for x in value["main_caches"]
                )
            ):
                raise Refusal("cache_drift")
            write_new(
                receipts / f"cache-{index}-before.json",
                {"row": row, "stage": "irreversible_before_mutation"},
            )
            api.mutate(f"actions/caches/{row['id']}", "DELETE")
            if any(x["id"] == row["id"] for x in api.caches()):
                raise Refusal("cache_delete_unconfirmed")
            write_new(
                receipts / f"cache-{index}-after.json",
                {"id": row["id"], "stage": "evicted_rebuild_required"},
            )
        before = api.repo()
        if before["delete_branch_on_merge"] not in (value["setting_before"], True):
            raise Refusal("setting_drift")
        write_new(
            receipts / "setting-before.json",
            {"before": before["delete_branch_on_merge"], "rollback": value["setting_before"]},
        )
        api.mutate("", "PATCH", {"delete_branch_on_merge": True})
        after = snapshot(git, api, custody, datetime.now(UTC))
        result = {
            "branches": results,
            "setting_enabled": after["delete_branch_on_merge"] is True,
            "under100": len(after["branches"]) < 100,
            "cache_under10GB": after["usage"]["active_caches_size_in_bytes"] < 10_000_000_000,
            "main_cache_survivor": bool(value["main_caches"])
            and all(
                cache_identity(x) in [cache_identity(y) for y in after["caches"]]
                for x in value["main_caches"]
            ),
            "cache_hit": "NOT_OBSERVED",
            "elapsed_7_and_14_days": "PENDING",
            "no_wall_clock_gain": True,
            "T0": now.isoformat(),
            "plan_digest": value["digest"],
        }
        write_new(receipts / "apply-readback.json", result)
        return result


def cache_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[k] for k in ("id", "ref", "key", "version", "size_in_bytes"))


def plan_cache_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    active = {f"refs/heads/{x['name']}" for x in state["prs"] if x["state"] == "open"} | {
        f"refs/heads/{x}" for x in state["held_refs"]
    }
    active |= {f"refs/heads/{x['branch']}" for x in state["worktrees"] if x["branch"]}
    return [
        x
        for x in state["caches"]
        if state["custody_complete"]
        and x["key"].startswith("node-cache-")
        and x["ref"].startswith("refs/heads/")
        and x["ref"] != "refs/heads/main"
        and x["ref"] not in active
    ]


def collect_runs(api: Github, start: datetime, end: datetime) -> list[dict[str, Any]]:
    query = {"event": "push", "created": f"{start.isoformat()}..{end.isoformat()}", "per_page": 100}
    first = api.get("actions/workflows/ci.yml/runs?" + urlencode(query))
    total = first["total_count"]
    if total >= 1000:
        if end - start <= timedelta(seconds=1):
            raise Refusal("observation_limit_unresolved")
        middle = start + (end - start) / 2
        left = collect_runs(api, start, middle)
        right = collect_runs(api, middle, end)
        return list({x["id"]: x for x in left + right}.values())
    rows = list(first["workflow_runs"])
    for page in range(2, (total + 99) // 100 + 1):
        value = api.get("actions/workflows/ci.yml/runs?" + urlencode({**query, "page": page}))
        if value["total_count"] != total:
            raise Refusal("observation_changed")
        rows.extend(value["workflow_runs"])
    if len(rows) != total or len({x["id"] for x in rows}) != total:
        raise Refusal("observation_incomplete")
    if any(x.get("event") != "push" or not isinstance(x.get("head_branch"), str) for x in rows):
        raise Refusal("observation_identity_invalid")
    return [
        {
            k: x[k]
            for k in (
                "id",
                "head_branch",
                "head_sha",
                "event",
                "created_at",
                "status",
                "conclusion",
            )
        }
        for x in rows
        if start <= instant(x["created_at"]) < end
    ]


def observe(api: Github, start: datetime, now: datetime) -> dict[str, Any]:
    results = {}
    for days in (7, 14):
        end = start + timedelta(days=days)
        rows = collect_runs(api, start, min(now, end)) if now > start else []
        nonmain = [x for x in rows if x["head_branch"] != "main"]
        positive = any(x["head_branch"] == "main" for x in rows)
        results[str(days)] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "runs": rows,
            "main_reader_positive": positive,
            "verdict": "FAIL" if nonmain else ("PASS" if now >= end and positive else "UNKNOWN"),
        }
    return {"repository": REPOSITORY, "windows": results, "no_wall_clock_gain": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("inventory", "plan", "apply", "restore", "observe", "restore-setting")
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--custody", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    git = Git(args.repo_root)
    api = Github()
    try:
        git.require_public_origin()
        if args.command == "inventory":
            value = snapshot(git, api, args.custody, now)
        elif args.command == "plan":
            if args.store is None:
                raise Refusal("independent_store_required")
            value = plan(snapshot(git, api, args.custody, now), git, args.store, now)
        elif args.command == "observe":
            if not args.input:
                raise Refusal("actual_apply_receipt_required")
            applied = json.loads(args.input.read_text())
            if not applied.get("plan_digest") or args.start and args.start != applied["T0"]:
                raise Refusal("declared_apply_T0_mismatch")
            value = observe(api, instant(applied["T0"]), now)
            value["apply_receipt_digest"] = digest(applied)
        else:
            if args.input is None:
                raise Refusal("exact_plan_required")
            value = json.loads(args.input.read_text())
            if not args.execute:
                value = {
                    "dry_run": True,
                    "command": args.command,
                    "plan_digest": value.get("digest"),
                    "mutations": [],
                }
            else:
                if args.approval is None:
                    raise Refusal("exact_author_approval_required")
                approval = json.loads(args.approval.read_text())
                if args.command == "apply":
                    if args.custody is None:
                        raise Refusal("custody_incomplete")
                    value = apply(
                        value,
                        approval,
                        git,
                        api,
                        args.custody,
                        args.output.parent / ("receipts-" + value["digest"][:12]),
                        now,
                    )
                elif args.command == "restore":
                    authorize(value, approval, "restore")
                    with git.exclusion():
                        for row in value["branches"]:
                            git.restore(row["name"], row["recovery"])
                    value = {"restored": True, "old_workflow_repush_risk": True}
                else:
                    authorize(value, approval, "restore-setting")
                    api.mutate("", "PATCH", {"delete_branch_on_merge": value["setting_before"]})
                    value = {
                        "setting_restored": api.repo()["delete_branch_on_merge"]
                        == value["setting_before"]
                    }
        write_new(args.output, value)
    except (Refusal, KeyError, ValueError, OSError, TypeError):
        print("ci_branch_hygiene: refused_or_unknown")
        return 1
    print("ci_branch_hygiene: receipt_written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

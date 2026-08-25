#!/usr/bin/env python3
"""Merge a reviewed PR and fail closed if its landing base is not exact.

GitHub's REST ``PUT /repos/{owner}/{repo}/pulls/{number}/merge`` endpoint can
pin the pull-request *head* SHA, but it has no matching conditional for the
base branch ref or SHA.  A pull request can therefore be retargeted or its base
branch can advance after final revalidation and before the REST request is
processed.  This helper catches a pre-request mismatch and, because that
remaining race cannot be atomically prevented through the API, audits the
parent and immutable result tree of the resulting squash commit.

This is a merge execution guard, not a replacement for the existing
independent review and terminal-CI gates.  A source Bead may close only when
the JSON result reports ``source_bead_closure_allowed: true``.

Because this helper is the sole final merge route, it is also where the batch
halts when the target branch is already broken (bu-vul8u).  Before issuing the
merge request it consumes ``scripts/main_health_gate.py`` against the exact base
SHA it is about to merge onto.  A PR's own CI can only ever see its own branch,
so two PRs can each be green and still collide once both have landed; the
repository's post-merge detectors see that, and until now nothing read them.
An unacknowledged red target branch, or one with no trustworthy verdict yet,
returns nonzero without sending a merge request.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main_health_gate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


class MergeOutcome(StrEnum):
    """Classified result of one merge attempt."""

    PREMERGE_NOT_OPEN = "premerge-pr-not-open"
    PREMERGE_HEAD_DRIFT = "premerge-head-drift"
    PREMERGE_BASE_REF_DRIFT = "premerge-base-ref-drift"
    PREMERGE_BASE_DRIFT = "premerge-base-drift"
    PREMERGE_TARGET_RED = "premerge-target-branch-red"
    PREMERGE_TARGET_HEALTH_UNKNOWN = "premerge-target-branch-health-unknown"
    MERGE_NOT_COMPLETED = "merge-not-completed"
    MERGE_RESPONSE_MISSING_SHA = "merge-response-missing-sha"
    MERGED_EXACT_BASE = "merged-exact-base"
    POSTMERGE_BASE_REF_DRIFT = "postmerge-base-ref-drift"
    POSTMERGE_BASE_DRIFT = "postmerge-base-drift"
    POSTMERGE_PATCH_DRIFT = "postmerge-patch-drift"
    POSTMERGE_UNEXPECTED_PARENT_SHAPE = "postmerge-unexpected-squash-parent-shape"


@dataclass(frozen=True)
class PullRequestSnapshot:
    """The PR refs and live target branch ref observed before the merge request."""

    state: str
    url: str
    head_sha: str
    base_ref_oid: str
    base_ref_name: str
    current_base_sha: str


@dataclass(frozen=True)
class MergeAudit:
    """Machine-readable result that determines whether a source Bead may close."""

    outcome: MergeOutcome
    expected_head_sha: str
    expected_base_ref_name: str
    expected_base_sha: str
    premerge: PullRequestSnapshot
    postmerge_base_ref_name: str | None = None
    merge_sha: str | None = None
    parent_shas: list[str] | None = None
    expected_patch_tree_sha: str | None = None
    landed_patch_tree_sha: str | None = None
    patch_identity_matches: bool | None = None
    target_branch_health: dict[str, Any] | None = None
    message: str | None = None

    @property
    def source_bead_closure_allowed(self) -> bool:
        return self.outcome is MergeOutcome.MERGED_EXACT_BASE

    @property
    def rebase_and_repeat_required(self) -> bool:
        return self.outcome in {
            MergeOutcome.PREMERGE_HEAD_DRIFT,
            MergeOutcome.PREMERGE_BASE_REF_DRIFT,
            MergeOutcome.PREMERGE_BASE_DRIFT,
        }

    @property
    def target_branch_halt_required(self) -> bool:
        return self.outcome is MergeOutcome.PREMERGE_TARGET_RED

    @property
    def next_action(self) -> str:
        if self.source_bead_closure_allowed:
            return "eligible-to-close-source-bead"
        if self.target_branch_halt_required:
            return "halt-batch-and-repair-target-branch"
        if self.outcome is MergeOutcome.PREMERGE_TARGET_HEALTH_UNKNOWN:
            return "wait-for-target-branch-verdict-then-repeat"
        if self.rebase_and_repeat_required:
            return "rebase-and-repeat-exact-head-review-and-ci"
        if self.outcome in {
            MergeOutcome.POSTMERGE_BASE_REF_DRIFT,
            MergeOutcome.POSTMERGE_BASE_DRIFT,
            MergeOutcome.POSTMERGE_PATCH_DRIFT,
            MergeOutcome.POSTMERGE_UNEXPECTED_PARENT_SHAPE,
        }:
            return "leave-source-bead-open-and-run-postmerge-race-audit"
        return "leave-source-bead-open-and-investigate"

    @property
    def exit_code(self) -> int:
        if self.source_bead_closure_allowed:
            return 0
        if self.outcome is MergeOutcome.PREMERGE_TARGET_RED:
            return 6
        if self.outcome is MergeOutcome.PREMERGE_TARGET_HEALTH_UNKNOWN:
            return 7
        if self.rebase_and_repeat_required:
            return 2
        if self.outcome in {
            MergeOutcome.POSTMERGE_BASE_REF_DRIFT,
            MergeOutcome.POSTMERGE_BASE_DRIFT,
            MergeOutcome.POSTMERGE_PATCH_DRIFT,
            MergeOutcome.POSTMERGE_UNEXPECTED_PARENT_SHAPE,
        }:
            return 4
        return 3

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["outcome"] = self.outcome.value
        payload["source_bead_closure_allowed"] = self.source_bead_closure_allowed
        payload["rebase_and_repeat_required"] = self.rebase_and_repeat_required
        payload["target_branch_halt_required"] = self.target_branch_halt_required
        payload["next_action"] = self.next_action
        return payload


def normalize_repo(repo: str) -> str:
    """Return ``owner/repo`` for GitHub URLs and already-normalized input."""
    value = repo.strip()
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/")
    if value.endswith(".git"):
        value = value[:-4]
    value = value.strip("/")
    if value.count("/") != 1:
        raise ValueError(f"Expected repository as owner/repo, got {repo!r}")
    return value


def run_gh_json(args: list[str]) -> Any:
    """Run ``gh`` and return JSON, surfacing any transport/API error."""
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode:
        stderr = proc.stderr.strip()
        raise RuntimeError(f"gh {' '.join(args[:4])} failed ({proc.returncode}): {stderr}")
    return json.loads(proc.stdout)


_PULL_REQUEST_SNAPSHOT_QUERY = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      state
      url
      headRefOid
      baseRefOid
      baseRefName
    }
  }
}
""".strip()


def _fetch_pull_request_record(repo: str, pr_number: int) -> dict[str, Any]:
    """Return the GraphQL PR record, including its retained target ref after merge."""
    owner, name = normalize_repo(repo).split("/", 1)
    data = run_gh_json(
        [
            "api",
            "graphql",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr_number}",
            "-f",
            f"query={_PULL_REQUEST_SNAPSHOT_QUERY}",
        ]
    )
    try:
        pr = data["data"]["repository"]["pullRequest"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"PR #{pr_number} returned an invalid GraphQL response") from exc
    if not isinstance(pr, dict):
        raise RuntimeError(f"PR #{pr_number} not found in {repo}")
    return pr


def _required_pr_string(pr: dict[str, Any], field: str, pr_number: int) -> str:
    """Read a required GraphQL string field without weakening the merge guard."""
    value = pr.get(field)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"PR #{pr_number} did not return {field}")
    return value


def fetch_pull_request_snapshot(repo: str, pr_number: int) -> PullRequestSnapshot:
    """Fetch the PR head plus the target branch's live object ID."""
    pr = _fetch_pull_request_record(repo, pr_number)
    base_ref_name = _required_pr_string(pr, "baseRefName", pr_number)
    return PullRequestSnapshot(
        state=_required_pr_string(pr, "state", pr_number),
        url=_required_pr_string(pr, "url", pr_number),
        head_sha=_required_pr_string(pr, "headRefOid", pr_number),
        base_ref_oid=_required_pr_string(pr, "baseRefOid", pr_number),
        base_ref_name=base_ref_name,
        current_base_sha=fetch_branch_head_sha(repo, base_ref_name),
    )


def fetch_pull_request_base_ref_name(repo: str, pr_number: int) -> str:
    """Re-read the merged PR's retained target ref name through GraphQL."""
    pr = _fetch_pull_request_record(repo, pr_number)
    return _required_pr_string(pr, "baseRefName", pr_number)


def fetch_branch_head_sha(repo: str, branch_name: str) -> str:
    """Fetch the live target branch tip instead of trusting stale PR metadata."""
    ref = quote(f"heads/{branch_name}", safe="/")
    payload = run_gh_json(["api", f"repos/{normalize_repo(repo)}/git/ref/{ref}"])
    sha = (
        payload.get("object", {}).get("sha")
        if isinstance(payload, dict) and isinstance(payload.get("object"), dict)
        else None
    )
    if not isinstance(sha, str) or not sha:
        raise RuntimeError(f"Branch {branch_name!r} did not return an object SHA")
    return sha


def request_squash_merge(repo: str, pr_number: int, head_sha: str) -> dict[str, Any]:
    """Request a squash merge while retaining GitHub's supported head-SHA pin."""
    response = run_gh_json(
        [
            "api",
            "--method",
            "PUT",
            f"repos/{normalize_repo(repo)}/pulls/{pr_number}/merge",
            "-f",
            "merge_method=squash",
            "-f",
            f"sha={head_sha}",
        ]
    )
    if not isinstance(response, dict):
        raise RuntimeError("GitHub merge endpoint returned a non-object response")
    return response


def fetch_commit_parent_shas(repo: str, commit_sha: str) -> list[str]:
    """Return every parent of a commit, failing rather than weakening the audit."""
    payload = run_gh_json(["api", f"repos/{normalize_repo(repo)}/commits/{commit_sha}"])
    parents = payload.get("parents") if isinstance(payload, dict) else None
    if not isinstance(parents, list):
        raise RuntimeError(f"Commit {commit_sha} did not return a parents list")
    parent_shas: list[str] = []
    for parent in parents:
        sha = parent.get("sha") if isinstance(parent, dict) else None
        if not isinstance(sha, str) or not sha:
            raise RuntimeError(f"Commit {commit_sha} returned a parent without a SHA")
        parent_shas.append(sha)
    return parent_shas


def fetch_commit_tree_sha(repo: str, commit_sha: str) -> str:
    """Return an immutable commit tree SHA for authoritative patch comparison."""
    payload = run_gh_json(["api", f"repos/{normalize_repo(repo)}/commits/{commit_sha}"])
    commit = payload.get("commit") if isinstance(payload, dict) else None
    tree = commit.get("tree") if isinstance(commit, dict) else None
    tree_sha = tree.get("sha") if isinstance(tree, dict) else None
    if not isinstance(tree_sha, str) or not tree_sha:
        raise RuntimeError(f"Commit {commit_sha} did not return a tree SHA")
    return tree_sha


def _audit(
    outcome: MergeOutcome,
    expected_head_sha: str,
    expected_base_ref_name: str,
    expected_base_sha: str,
    premerge: PullRequestSnapshot,
    *,
    merge_sha: str | None = None,
    parent_shas: list[str] | None = None,
    postmerge_base_ref_name: str | None = None,
    expected_patch_tree_sha: str | None = None,
    landed_patch_tree_sha: str | None = None,
    patch_identity_matches: bool | None = None,
    target_branch_health: dict[str, Any] | None = None,
    message: str | None = None,
) -> MergeAudit:
    return MergeAudit(
        outcome=outcome,
        expected_head_sha=expected_head_sha,
        expected_base_ref_name=expected_base_ref_name,
        expected_base_sha=expected_base_sha,
        premerge=premerge,
        postmerge_base_ref_name=postmerge_base_ref_name,
        merge_sha=merge_sha,
        parent_shas=parent_shas,
        expected_patch_tree_sha=expected_patch_tree_sha,
        landed_patch_tree_sha=landed_patch_tree_sha,
        patch_identity_matches=patch_identity_matches,
        target_branch_health=target_branch_health,
        message=message,
    )


def evaluate_target_branch_health(
    repo: str,
    base_sha: str,
    *,
    acknowledged_red_workflows: Sequence[str] = (),
    tree: Path = REPO_ROOT,
) -> tuple[main_health_gate.Decision, dict[str, Any]]:
    """Read the target branch's own post-merge detectors for this exact base.

    The batch driver used to check only the PR being merged, so a detector that
    fired on the previously merged tree had no reader and the batch kept going.
    This is that reader.

    Only hosted verdicts are consulted here; the local guard sweep needs a
    scratch checkout of the merged tree and belongs to
    ``scripts/main_health_gate.py`` run between merges.

    ``acknowledged_red_workflows`` exists so the fix for a red main can still be
    merged. It names individual workflows, never a blanket override, so a
    *different* red still halts the batch.
    """
    report = main_health_gate.evaluate(repo, base_sha, tree=tree, run_local_guards=False)
    acknowledged = tuple(
        name
        for name, verdict in report.workflows.items()
        if verdict is main_health_gate.WorkflowVerdict.RED
        and name in set(acknowledged_red_workflows)
    )
    remaining = {
        name: verdict for name, verdict in report.workflows.items() if name not in acknowledged
    }
    decision = main_health_gate.decide(remaining, guard_failures=(), wait_budget_exhausted=False)
    payload = report.to_dict()
    payload["decision"] = decision.value
    payload["acknowledged_red_workflows"] = list(acknowledged)
    return decision, payload


def merge_after_exact_base_revalidation(
    repo: str,
    pr_number: int,
    *,
    expected_head_sha: str,
    expected_base_ref_name: str,
    expected_base_sha: str,
    acknowledged_red_workflows: Sequence[str] = (),
) -> MergeAudit:
    """Merge only from matching final evidence, then prove the landed patch.

    There is an unavoidable race between this preflight fetch and GitHub
    processing the merge request. The post-merge audit is deliberately
    mandatory: a non-exact parent or nonmatching patch returns a nonzero result
    even though GitHub has already merged the PR.
    """
    premerge = fetch_pull_request_snapshot(repo, pr_number)
    if premerge.state != "OPEN":
        return _audit(
            MergeOutcome.PREMERGE_NOT_OPEN,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            message=f"PR state is {premerge.state}, expected OPEN",
        )
    if premerge.head_sha != expected_head_sha:
        return _audit(
            MergeOutcome.PREMERGE_HEAD_DRIFT,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            message="PR head changed after final revalidation",
        )
    if premerge.base_ref_name != expected_base_ref_name:
        return _audit(
            MergeOutcome.PREMERGE_BASE_REF_DRIFT,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            message="PR target branch changed after final revalidation",
        )
    if premerge.current_base_sha != expected_base_sha:
        return _audit(
            MergeOutcome.PREMERGE_BASE_DRIFT,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            message="Base branch changed after final revalidation",
        )

    health_decision, health_payload = evaluate_target_branch_health(
        repo,
        expected_base_sha,
        acknowledged_red_workflows=acknowledged_red_workflows,
    )
    if health_decision is main_health_gate.Decision.HALT:
        return _audit(
            MergeOutcome.PREMERGE_TARGET_RED,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            target_branch_health=health_payload,
            message="Target branch is already red; halt the batch instead of merging onto it",
        )
    if health_decision is main_health_gate.Decision.WAIT:
        return _audit(
            MergeOutcome.PREMERGE_TARGET_HEALTH_UNKNOWN,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            target_branch_health=health_payload,
            message="Target branch has no trustworthy verdict yet; wait rather than assume green",
        )

    response = request_squash_merge(repo, pr_number, expected_head_sha)
    if response.get("merged") is not True:
        return _audit(
            MergeOutcome.MERGE_NOT_COMPLETED,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            message=str(response.get("message") or "GitHub did not merge the pull request"),
        )
    merge_sha = response.get("sha")
    if not isinstance(merge_sha, str) or not merge_sha:
        return _audit(
            MergeOutcome.MERGE_RESPONSE_MISSING_SHA,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            message="GitHub reported a merge without a commit SHA",
        )

    parent_shas = fetch_commit_parent_shas(repo, merge_sha)
    try:
        postmerge_base_ref_name = fetch_pull_request_base_ref_name(repo, pr_number)
    except (RuntimeError, ValueError) as exc:
        return _audit(
            MergeOutcome.POSTMERGE_BASE_REF_DRIFT,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            merge_sha=merge_sha,
            parent_shas=parent_shas,
            message=f"Could not verify merged PR target branch: {exc}",
        )
    if postmerge_base_ref_name != expected_base_ref_name:
        return _audit(
            MergeOutcome.POSTMERGE_BASE_REF_DRIFT,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            merge_sha=merge_sha,
            parent_shas=parent_shas,
            postmerge_base_ref_name=postmerge_base_ref_name,
            message="GitHub retargeted the pull request after final revalidation",
        )

    try:
        expected_patch_tree_sha = fetch_commit_tree_sha(repo, expected_head_sha)
        landed_patch_tree_sha = fetch_commit_tree_sha(repo, merge_sha)
    except (RuntimeError, ValueError) as exc:
        return _audit(
            MergeOutcome.POSTMERGE_PATCH_DRIFT,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            merge_sha=merge_sha,
            parent_shas=parent_shas,
            postmerge_base_ref_name=postmerge_base_ref_name,
            message=f"Could not verify the reviewed and landed patch identity: {exc}",
        )

    # A diff is determined by its base tree and result tree. The sole-parent
    # check below pins both comparisons to expected_base_sha, so equal immutable
    # tree IDs prove the squash applied the reviewed net patch, including
    # binary, rename, and empty changes without relying on a local checkout.
    patch_identity_matches = expected_patch_tree_sha == landed_patch_tree_sha
    if not patch_identity_matches:
        return _audit(
            MergeOutcome.POSTMERGE_PATCH_DRIFT,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            merge_sha=merge_sha,
            parent_shas=parent_shas,
            postmerge_base_ref_name=postmerge_base_ref_name,
            expected_patch_tree_sha=expected_patch_tree_sha,
            landed_patch_tree_sha=landed_patch_tree_sha,
            patch_identity_matches=False,
            message="GitHub landed a squash tree that differs from the reviewed head tree",
        )

    if parent_shas == [expected_base_sha]:
        return _audit(
            MergeOutcome.MERGED_EXACT_BASE,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            merge_sha=merge_sha,
            parent_shas=parent_shas,
            postmerge_base_ref_name=postmerge_base_ref_name,
            expected_patch_tree_sha=expected_patch_tree_sha,
            landed_patch_tree_sha=landed_patch_tree_sha,
            patch_identity_matches=True,
            message=str(response.get("message") or "Pull Request successfully merged"),
        )
    if len(parent_shas) != 1:
        return _audit(
            MergeOutcome.POSTMERGE_UNEXPECTED_PARENT_SHAPE,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            merge_sha=merge_sha,
            parent_shas=parent_shas,
            postmerge_base_ref_name=postmerge_base_ref_name,
            expected_patch_tree_sha=expected_patch_tree_sha,
            landed_patch_tree_sha=landed_patch_tree_sha,
            patch_identity_matches=True,
            message="Squash merge must produce exactly one parent before a source Bead can close",
        )
    return _audit(
        MergeOutcome.POSTMERGE_BASE_DRIFT,
        expected_head_sha,
        expected_base_ref_name,
        expected_base_sha,
        premerge,
        merge_sha=merge_sha,
        parent_shas=parent_shas,
        postmerge_base_ref_name=postmerge_base_ref_name,
        expected_patch_tree_sha=expected_patch_tree_sha,
        landed_patch_tree_sha=landed_patch_tree_sha,
        patch_identity_matches=True,
        message="GitHub merged the reviewed head onto a different base after preflight",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Perform a SHA-pinned squash merge and verify its parent and patch tree match "
            "the final reviewed evidence."
        )
    )
    parser.add_argument("--repo", default="Tzeusy/butlers", help="GitHub repository as owner/repo")
    parser.add_argument("--pr", type=int, required=True, help="Pull request number")
    parser.add_argument(
        "--expected-head",
        required=True,
        help="headRefOid captured during final exact-head review/CI revalidation",
    )
    parser.add_argument(
        "--expected-base",
        required=True,
        help="live target-branch SHA captured during that same final revalidation",
    )
    parser.add_argument(
        "--expected-base-ref",
        dest="expected_base_ref_name",
        required=True,
        help="target branch name captured during that same final revalidation",
    )
    parser.add_argument(
        "--acknowledge-target-red",
        dest="acknowledged_red_workflows",
        action="append",
        default=[],
        metavar="WORKFLOW_FILENAME",
        help=(
            "merge despite this specific target-branch workflow being red "
            "(repeatable; any other red still halts the batch)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = merge_after_exact_base_revalidation(
            args.repo,
            args.pr,
            expected_head_sha=args.expected_head,
            expected_base_ref_name=args.expected_base_ref_name,
            expected_base_sha=args.expected_base,
            acknowledged_red_workflows=tuple(args.acknowledged_red_workflows),
        )
    except (RuntimeError, ValueError) as exc:
        json.dump(
            {
                "outcome": "merge-tool-error",
                "source_bead_closure_allowed": False,
                "next_action": "leave-source-bead-open-and-investigate",
                "error": str(exc),
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 5

    json.dump(result.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

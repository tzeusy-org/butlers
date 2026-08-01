#!/usr/bin/env python3
"""Merge a reviewed PR and fail closed if its landing base is not exact.

GitHub's REST ``PUT /repos/{owner}/{repo}/pulls/{number}/merge`` endpoint can
pin the pull-request *head* SHA, but it has no matching conditional for the
base branch ref or SHA.  A pull request can therefore be retargeted or its base
branch can advance after final revalidation and before the REST request is
processed.  This helper catches a pre-request mismatch and, because that
remaining race cannot be atomically prevented through the API, audits the
parent of the resulting squash commit.

This is a merge execution guard, not a replacement for the existing
independent review and terminal-CI gates.  A source Bead may close only when
the JSON result reports ``source_bead_closure_allowed: true``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote


class MergeOutcome(StrEnum):
    """Classified result of one merge attempt."""

    PREMERGE_NOT_OPEN = "premerge-pr-not-open"
    PREMERGE_HEAD_DRIFT = "premerge-head-drift"
    PREMERGE_BASE_REF_DRIFT = "premerge-base-ref-drift"
    PREMERGE_BASE_DRIFT = "premerge-base-drift"
    MERGE_NOT_COMPLETED = "merge-not-completed"
    MERGE_RESPONSE_MISSING_SHA = "merge-response-missing-sha"
    MERGED_EXACT_BASE = "merged-exact-base"
    POSTMERGE_BASE_DRIFT = "postmerge-base-drift"
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
    merge_sha: str | None = None
    parent_shas: list[str] | None = None
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
    def next_action(self) -> str:
        if self.source_bead_closure_allowed:
            return "eligible-to-close-source-bead"
        if self.rebase_and_repeat_required:
            return "rebase-and-repeat-exact-head-review-and-ci"
        if self.outcome is MergeOutcome.POSTMERGE_BASE_DRIFT:
            return "leave-source-bead-open-and-run-postmerge-race-audit"
        return "leave-source-bead-open-and-investigate"

    @property
    def exit_code(self) -> int:
        if self.source_bead_closure_allowed:
            return 0
        if self.rebase_and_repeat_required:
            return 2
        if self.outcome in {
            MergeOutcome.POSTMERGE_BASE_DRIFT,
            MergeOutcome.POSTMERGE_UNEXPECTED_PARENT_SHAPE,
        }:
            return 4
        return 3

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["outcome"] = self.outcome.value
        payload["source_bead_closure_allowed"] = self.source_bead_closure_allowed
        payload["rebase_and_repeat_required"] = self.rebase_and_repeat_required
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


def fetch_pull_request_snapshot(repo: str, pr_number: int) -> PullRequestSnapshot:
    """Fetch the PR head plus the target branch's live object ID."""
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
    pr = data["data"]["repository"]["pullRequest"]
    if pr is None:
        raise RuntimeError(f"PR #{pr_number} not found in {repo}")
    base_ref_name = pr["baseRefName"]
    return PullRequestSnapshot(
        state=pr["state"],
        url=pr["url"],
        head_sha=pr["headRefOid"],
        base_ref_oid=pr["baseRefOid"],
        base_ref_name=base_ref_name,
        current_base_sha=fetch_branch_head_sha(repo, base_ref_name),
    )


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


def _audit(
    outcome: MergeOutcome,
    expected_head_sha: str,
    expected_base_ref_name: str,
    expected_base_sha: str,
    premerge: PullRequestSnapshot,
    *,
    merge_sha: str | None = None,
    parent_shas: list[str] | None = None,
    message: str | None = None,
) -> MergeAudit:
    return MergeAudit(
        outcome=outcome,
        expected_head_sha=expected_head_sha,
        expected_base_ref_name=expected_base_ref_name,
        expected_base_sha=expected_base_sha,
        premerge=premerge,
        merge_sha=merge_sha,
        parent_shas=parent_shas,
        message=message,
    )


def merge_after_exact_base_revalidation(
    repo: str,
    pr_number: int,
    *,
    expected_head_sha: str,
    expected_base_ref_name: str,
    expected_base_sha: str,
) -> MergeAudit:
    """Merge only from matching final evidence, then prove the landed parent.

    There is an unavoidable race between this preflight fetch and GitHub
    processing the merge request. The post-merge parent audit is deliberately
    mandatory: a non-exact parent returns a nonzero result even though GitHub
    has already merged the PR.
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
    if parent_shas == [expected_base_sha]:
        return _audit(
            MergeOutcome.MERGED_EXACT_BASE,
            expected_head_sha,
            expected_base_ref_name,
            expected_base_sha,
            premerge,
            merge_sha=merge_sha,
            parent_shas=parent_shas,
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
        message="GitHub merged the reviewed head onto a different base after preflight",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Perform a SHA-pinned squash merge and verify the resulting commit parent matches "
            "the final reviewed base SHA."
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

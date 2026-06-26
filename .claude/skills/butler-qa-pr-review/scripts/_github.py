"""Shared GitHub CLI helpers for the butler-qa-pr-review skill."""

from __future__ import annotations

import json
import subprocess
from typing import Any


def normalize_repo(repo: str) -> str:
    """Normalize a GitHub URL or owner/repo string to owner/repo."""
    value = (repo or "").strip()
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/")
    if value.endswith(".git"):
        value = value[:-4]
    return value.strip("/")


def run_gh_json(args: list[str], *, stdin_obj: dict[str, Any] | None = None) -> Any:
    """Run gh and decode JSON stdout, raising on non-zero exit."""
    input_data = None
    if stdin_obj is not None:
        input_data = json.dumps(stdin_obj).encode("utf-8")

    proc = subprocess.run(
        ["gh", *args],
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(proc.stdout.decode("utf-8"))


PR_THREADS_QUERY = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      id
      url
      title
      reviewDecision
      headRefName
      headRefOid
      baseRefName
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(first: 100) {
            nodes {
              databaseId
              url
              body
              createdAt
              author { login }
              commit { oid }
            }
          }
        }
      }
    }
  }
}
""".strip()


def fetch_pr_threads(repo: str, pr_number: int) -> dict[str, Any]:
    """Fetch PR metadata and review threads through GitHub GraphQL."""
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
            f"query={PR_THREADS_QUERY}",
        ]
    )
    pr = data["data"]["repository"]["pullRequest"]
    if pr is None:
        raise RuntimeError(f"PR #{pr_number} not found in {repo}")
    return pr


def fetch_required_checks(repo: str, pr_number: int) -> list[dict[str, Any]]:
    """Return required PR checks via gh's normalized check output."""
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "checks",
            str(pr_number),
            "--repo",
            normalize_repo(repo),
            "--required",
            "--json",
            "name,bucket,state,link,workflow",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stderr_text = proc.stderr.decode("utf-8", "replace")
    # gh exits non-zero with this message when the branch has no required
    # checks configured (no branch protection). That is a vacuously-satisfied
    # required-checks gate, not an error: treat it as an empty required set.
    if proc.returncode != 0 and "no required checks reported" in stderr_text:
        return []
    if proc.returncode not in (0, 8):
        raise subprocess.CalledProcessError(
            proc.returncode,
            proc.args,
            output=proc.stdout,
            stderr=proc.stderr,
        )
    stdout_text = proc.stdout.decode("utf-8").strip()
    if not stdout_text:
        return []
    return json.loads(stdout_text)

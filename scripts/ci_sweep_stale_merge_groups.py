"""Cancel merge-group CI runs whose merge-queue group no longer exists (bu-29wkwj.16).

Each merge-queue build runs CI on its own ``gh-readonly-queue/main/pr-<n>-<sha>`` ref,
so the CI concurrency group never matches an earlier build and ``cancel-in-progress``
never fires. When the queue rebuilds (main moved, a group failed or timed out), it
deletes the old refs but their runs keep waiting for runners, starving the live
groups. This sweeper force-cancels every queued or running ``merge_group`` run whose
ref is gone. Force-cancel is needed because a plain cancel still schedules the
``if: always()`` fan-in job.

Dry-run by default; ``--apply`` cancels. Needs ``gh`` with ``actions: write``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime, timedelta

QUEUE_PREFIX = "gh-readonly-queue/"
# A just-created run may briefly race its ref's creation or deletion; leave it alone.
MIN_AGE = timedelta(minutes=2)


def stale_runs(runs: list[dict], live_refs: set[str], now: datetime) -> list[dict]:
    """Runs on a merge-queue ref that no longer exists, older than ``MIN_AGE``."""
    return [
        run
        for run in runs
        if run["head_branch"].startswith(QUEUE_PREFIX)
        and run["head_branch"] not in live_refs
        and now - datetime.fromisoformat(run["created_at"]) >= MIN_AGE
    ]


def _gh_pages(path: str) -> list:
    out = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def live_queue_refs(repo: str) -> set[str]:
    pages = _gh_pages(f"repos/{repo}/git/matching-refs/heads/{QUEUE_PREFIX}")
    return {ref["ref"].removeprefix("refs/heads/") for page in pages for ref in page}


def active_merge_group_runs(repo: str) -> list[dict]:
    runs = []
    for status in ("queued", "in_progress"):
        path = f"repos/{repo}/actions/runs?event=merge_group&status={status}&per_page=100"
        runs += [run for page in _gh_pages(path) for run in page["workflow_runs"]]
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--apply", action="store_true", help="force-cancel stale runs")
    args = parser.parse_args()

    # Read runs before refs: a ref created after the run listing cannot make a
    # live run look stale, and a run created after it is simply not seen.
    runs = active_merge_group_runs(args.repo)
    stale = stale_runs(runs, live_queue_refs(args.repo), datetime.now(UTC))
    print(f"active merge_group runs: {len(runs)}; stale: {len(stale)}")
    for run in stale:
        print(f"{run['id']} {run['created_at']} {run['head_branch']}")
        if args.apply:
            path = f"repos/{args.repo}/actions/runs/{run['id']}/force-cancel"
            # A run can finish between listing and cancelling (HTTP 409); not an error.
            subprocess.run(["gh", "api", "-X", "POST", path, "--silent"], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

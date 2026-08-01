"""Regression tests for the exact-base REST PR merge guard.

The REST merge endpoint conditionally accepts only a pull request head SHA. A
base branch can advance after a coordinator's final revalidation, so a
SHA-pinned squash merge must verify its resulting commit parent before a source
Bead is eligible to close.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import merge_pr_exact_base as merge_guard  # noqa: E402

pytestmark = pytest.mark.unit

REPO = "Tzeusy/butlers"
PR_NUMBER = 3652
HEAD_SHA = "95feb463f5f563f287f08c3f2130934709e80ae1"
REVIEWED_BASE_SHA = "257d0942d066e5af6d0981a892fad7e48f7e040d"
ADVANCED_BASE_SHA = "2963db74bad62f44406c78f76dd5eb0cf7272a9e"
MERGE_SHA = "85dcdf2ceac01636327cce505725dea514b5d0c4"


def _snapshot(
    *,
    head_sha: str = HEAD_SHA,
    base_ref_oid: str = REVIEWED_BASE_SHA,
    base_ref_name: str = "main",
    current_base_sha: str = REVIEWED_BASE_SHA,
):
    return merge_guard.PullRequestSnapshot(
        state="OPEN",
        url=f"https://github.com/{REPO}/pull/{PR_NUMBER}",
        head_sha=head_sha,
        base_ref_oid=base_ref_oid,
        base_ref_name=base_ref_name,
        current_base_sha=current_base_sha,
    )


def test_premerge_live_base_drift_skips_rest_merge_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale PR baseRefOid must not hide an advanced target branch."""
    monkeypatch.setattr(
        merge_guard,
        "fetch_pull_request_snapshot",
        lambda *_: _snapshot(
            base_ref_oid=REVIEWED_BASE_SHA,
            current_base_sha=ADVANCED_BASE_SHA,
        ),
    )
    merge_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        merge_guard,
        "request_squash_merge",
        lambda *args: merge_calls.append(args),
    )

    result = merge_guard.merge_after_exact_base_revalidation(
        REPO,
        PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        expected_base_ref_name="main",
        expected_base_sha=REVIEWED_BASE_SHA,
    )

    assert result.outcome is merge_guard.MergeOutcome.PREMERGE_BASE_DRIFT
    assert result.source_bead_closure_allowed is False
    assert result.rebase_and_repeat_required is True
    assert result.premerge.base_ref_oid == REVIEWED_BASE_SHA
    assert result.premerge.current_base_sha == ADVANCED_BASE_SHA
    assert merge_calls == []


def test_premerge_base_ref_retarget_at_same_sha_skips_rest_merge_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-SHA retarget must not merge into an unreviewed branch."""
    monkeypatch.setattr(
        merge_guard,
        "fetch_pull_request_snapshot",
        lambda *_: _snapshot(base_ref_name="release"),
    )
    merge_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        merge_guard,
        "request_squash_merge",
        lambda *args: merge_calls.append(args),
    )

    result = merge_guard.merge_after_exact_base_revalidation(
        REPO,
        PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        expected_base_sha=REVIEWED_BASE_SHA,
        expected_base_ref_name="main",
    )

    assert result.outcome is merge_guard.MergeOutcome.PREMERGE_BASE_REF_DRIFT
    assert result.source_bead_closure_allowed is False
    assert result.rebase_and_repeat_required is True
    assert result.expected_base_ref_name == "main"
    assert result.premerge.base_ref_name == "release"
    assert result.to_dict()["expected_base_ref_name"] == "main"
    assert merge_calls == []


def test_premerge_head_drift_skips_rest_merge_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        merge_guard,
        "fetch_pull_request_snapshot",
        lambda *_: _snapshot(head_sha="a" * 40),
    )
    merge_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        merge_guard,
        "request_squash_merge",
        lambda *args: merge_calls.append(args),
    )

    result = merge_guard.merge_after_exact_base_revalidation(
        REPO,
        PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        expected_base_ref_name="main",
        expected_base_sha=REVIEWED_BASE_SHA,
    )

    assert result.outcome is merge_guard.MergeOutcome.PREMERGE_HEAD_DRIFT
    assert result.source_bead_closure_allowed is False
    assert result.rebase_and_repeat_required is True
    assert merge_calls == []


def test_exact_base_squash_merge_allows_source_bead_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(merge_guard, "fetch_pull_request_snapshot", lambda *_: _snapshot())
    merge_calls: list[tuple[object, ...]] = []
    postmerge_base_ref_name_calls: list[tuple[object, ...]] = []

    def request_squash_merge(*args: object) -> dict[str, object]:
        merge_calls.append(args)
        return {"merged": True, "sha": MERGE_SHA, "message": "Pull Request successfully merged"}

    def fetch_pull_request_base_ref_name(*args: object) -> str:
        postmerge_base_ref_name_calls.append(args)
        return "main"

    monkeypatch.setattr(merge_guard, "request_squash_merge", request_squash_merge)
    monkeypatch.setattr(
        merge_guard,
        "fetch_commit_parent_shas",
        lambda *_: [REVIEWED_BASE_SHA],
    )
    monkeypatch.setattr(
        merge_guard,
        "fetch_pull_request_base_ref_name",
        fetch_pull_request_base_ref_name,
    )

    result = merge_guard.merge_after_exact_base_revalidation(
        REPO,
        PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        expected_base_ref_name="main",
        expected_base_sha=REVIEWED_BASE_SHA,
    )

    assert merge_calls == [(REPO, PR_NUMBER, HEAD_SHA)]
    assert postmerge_base_ref_name_calls == [(REPO, PR_NUMBER)]
    assert result.outcome is merge_guard.MergeOutcome.MERGED_EXACT_BASE
    assert result.merge_sha == MERGE_SHA
    assert result.parent_shas == [REVIEWED_BASE_SHA]
    assert result.postmerge_base_ref_name == "main"
    assert result.source_bead_closure_allowed is True
    assert result.rebase_and_repeat_required is False


def test_postmerge_base_drift_is_classified_and_blocks_source_bead_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(merge_guard, "fetch_pull_request_snapshot", lambda *_: _snapshot())
    postmerge_base_ref_name_calls: list[tuple[object, ...]] = []

    def fetch_pull_request_base_ref_name(*args: object) -> str:
        postmerge_base_ref_name_calls.append(args)
        return "main"

    monkeypatch.setattr(
        merge_guard,
        "request_squash_merge",
        lambda *_: {
            "merged": True,
            "sha": MERGE_SHA,
            "message": "Pull Request successfully merged",
        },
    )
    monkeypatch.setattr(
        merge_guard,
        "fetch_commit_parent_shas",
        lambda *_: [ADVANCED_BASE_SHA],
    )
    monkeypatch.setattr(
        merge_guard,
        "fetch_pull_request_base_ref_name",
        fetch_pull_request_base_ref_name,
    )

    result = merge_guard.merge_after_exact_base_revalidation(
        REPO,
        PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        expected_base_ref_name="main",
        expected_base_sha=REVIEWED_BASE_SHA,
    )

    assert result.outcome is merge_guard.MergeOutcome.POSTMERGE_BASE_DRIFT
    assert result.parent_shas == [ADVANCED_BASE_SHA]
    assert postmerge_base_ref_name_calls == [(REPO, PR_NUMBER)]
    assert result.postmerge_base_ref_name == "main"
    assert result.source_bead_closure_allowed is False
    assert result.rebase_and_repeat_required is False


def test_postmerge_retarget_during_rest_merge_blocks_closure_despite_exact_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retarget during the REST request is unsafe even if its SHA is unchanged."""
    monkeypatch.setattr(merge_guard, "fetch_pull_request_snapshot", lambda *_: _snapshot())
    current_base_ref_name = "main"
    merge_calls: list[tuple[object, ...]] = []
    parent_queries: list[tuple[object, ...]] = []

    def request_squash_merge(*args: object) -> dict[str, object]:
        nonlocal current_base_ref_name
        merge_calls.append(args)
        current_base_ref_name = "release"
        return {"merged": True, "sha": MERGE_SHA}

    def fetch_commit_parent_shas(*args: object) -> list[str]:
        parent_queries.append(args)
        return [REVIEWED_BASE_SHA]

    monkeypatch.setattr(merge_guard, "request_squash_merge", request_squash_merge)
    monkeypatch.setattr(merge_guard, "fetch_commit_parent_shas", fetch_commit_parent_shas)
    monkeypatch.setattr(
        merge_guard,
        "fetch_pull_request_base_ref_name",
        lambda *_: current_base_ref_name,
        raising=False,
    )

    result = merge_guard.merge_after_exact_base_revalidation(
        REPO,
        PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        expected_base_ref_name="main",
        expected_base_sha=REVIEWED_BASE_SHA,
    )

    assert merge_calls == [(REPO, PR_NUMBER, HEAD_SHA)]
    assert parent_queries == [(REPO, MERGE_SHA)]
    assert result.parent_shas == [REVIEWED_BASE_SHA]
    assert result.outcome.value == "postmerge-base-ref-drift"
    assert result.postmerge_base_ref_name == "release"
    assert result.to_dict()["postmerge_base_ref_name"] == "release"
    assert result.source_bead_closure_allowed is False
    assert result.rebase_and_repeat_required is False
    assert result.next_action == "leave-source-bead-open-and-run-postmerge-race-audit"


def test_postmerge_base_ref_lookup_failure_blocks_source_bead_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A merged PR is not closeable when its retained target ref cannot be read."""
    monkeypatch.setattr(merge_guard, "fetch_pull_request_snapshot", lambda *_: _snapshot())
    monkeypatch.setattr(
        merge_guard,
        "request_squash_merge",
        lambda *_: {"merged": True, "sha": MERGE_SHA},
    )
    monkeypatch.setattr(
        merge_guard,
        "fetch_commit_parent_shas",
        lambda *_: [REVIEWED_BASE_SHA],
    )
    monkeypatch.setattr(
        merge_guard,
        "fetch_pull_request_base_ref_name",
        lambda *_: (_ for _ in ()).throw(RuntimeError("GraphQL unavailable")),
        raising=False,
    )

    result = merge_guard.merge_after_exact_base_revalidation(
        REPO,
        PR_NUMBER,
        expected_head_sha=HEAD_SHA,
        expected_base_ref_name="main",
        expected_base_sha=REVIEWED_BASE_SHA,
    )

    assert result.outcome.value == "postmerge-base-ref-drift"
    assert result.postmerge_base_ref_name is None
    assert result.source_bead_closure_allowed is False
    assert result.exit_code == 4
    assert result.next_action == "leave-source-bead-open-and-run-postmerge-race-audit"


def test_rest_request_keeps_sha_pinning_and_squash_method(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def run_gh_json(args: list[str]) -> dict[str, object]:
        captured.append(args)
        return {"merged": True, "sha": MERGE_SHA}

    monkeypatch.setattr(merge_guard, "run_gh_json", run_gh_json)

    response = merge_guard.request_squash_merge(REPO, PR_NUMBER, HEAD_SHA)

    assert response["sha"] == MERGE_SHA
    assert captured == [
        [
            "api",
            "--method",
            "PUT",
            f"repos/{REPO}/pulls/{PR_NUMBER}/merge",
            "-f",
            "merge_method=squash",
            "-f",
            f"sha={HEAD_SHA}",
        ]
    ]

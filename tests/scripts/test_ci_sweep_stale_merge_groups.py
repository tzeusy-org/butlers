"""The stale merge-group sweeper cancels only runs whose queue ref is gone (bu-29wkwj.16)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_sweep_stale_merge_groups as sweep  # noqa: E402

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
LIVE = "gh-readonly-queue/main/pr-5280-2919588a"
GONE = "gh-readonly-queue/main/pr-5280-85e90efa"


def run(branch: str, age: timedelta = timedelta(minutes=30)) -> dict:
    return {"id": hash(branch), "head_branch": branch, "created_at": (NOW - age).isoformat()}


def test_a_run_whose_queue_ref_was_deleted_is_stale() -> None:
    assert sweep.stale_runs([run(LIVE), run(GONE)], {LIVE}, NOW) == [run(GONE)]


def test_a_just_created_run_is_left_alone_even_without_a_ref() -> None:
    assert sweep.stale_runs([run(GONE, timedelta(seconds=30))], set(), NOW) == []


def test_a_run_outside_the_merge_queue_is_never_stale() -> None:
    assert sweep.stale_runs([run("main")], set(), NOW) == []

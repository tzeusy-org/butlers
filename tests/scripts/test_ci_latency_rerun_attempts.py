"""bu-eb32hj: a re-run competitor's earlier-attempt jobs count toward shard-wait attribution."""

from __future__ import annotations

import copy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_latency as latency  # noqa: E402

pytestmark = pytest.mark.unit


def stamp(seconds=0):
    return (datetime(2026, 10, 15, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat()


def sample_run(identifier=1):
    return {
        "id": identifier,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "event": "merge_group",
        "status": "completed",
        "conclusion": "success",
        "created_at": stamp(),
    }


def sample_jobs(row):
    """Seven-job success DAG whose shard (check-unit) is created at t=50."""
    plan = [
        ("route", 0, 2, 10),
        ("guards", 10, 12, 50),
        ("check-unit (1)", 50, 55, 200),
        ("check-preflight", 200, 60, 220),
        ("check", 220, 222, 230),
        ("frontend", 200, 201, 240),
        ("coverage", 230, 232, 270),
    ]
    result = [
        {
            "id": index + 1,
            "run_id": row["id"],
            "run_attempt": 1,
            "head_sha": row["head_sha"],
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "created_at": stamp(ready),
            "started_at": stamp(max(ready, start)),
            "completed_at": stamp(end),
        }
        for index, (name, ready, start, end) in enumerate(plan)
    ]
    return {"total_count": len(result), "jobs": result}


def competitor(identifier, *, attempt):
    return {
        "id": identifier,
        "run_attempt": attempt,
        "event": "pull_request",
        "status": "completed",
        "created_at": stamp(-30),
        "updated_at": stamp(400),
    }


def job(attempt, start, end):
    return {
        "name": "x",
        "run_attempt": attempt,
        "status": "completed",
        "conclusion": "success",
        "started_at": stamp(start),
        "completed_at": stamp(end),
    }


def fetcher(extra_runs, *, attempt_jobs, all_jobs, seen=None):
    """Sample run answers normally; each competitor answers per endpoint shape."""
    rows = [sample_run()]

    def fetch(endpoint):
        if seen is not None:
            seen.append(endpoint)
        if "workflows/ci.yml" in endpoint:
            return {"total_count": 1, "workflow_runs": copy.deepcopy(rows)}
        if "/actions/runs?" in endpoint:
            indexed = [{**copy.deepcopy(r), "updated_at": stamp(300)} for r in rows]
            return {
                "total_count": len(indexed) + len(extra_runs),
                "workflow_runs": indexed + extra_runs,
            }
        if "/runs/1/" in endpoint:
            return sample_jobs(rows[0])
        run_id = int(endpoint.split("/runs/")[1].split("/")[0])
        if "filter=all" in endpoint:
            return all_jobs[run_id]
        return attempt_jobs[run_id]

    return fetch


def observe(fetch):
    return latency.observe(repository="owner/repo", attribution=True, fetch=fetch)


# Attempt 1 was running at t=50 and was then re-run; the latest attempt (2) ran later.
EARLIER_ATTEMPT = {"total_count": 2, "jobs": [job(1, 0, 90), job(2, 100, 150)]}
LATEST_ONLY = {"total_count": 1, "jobs": [job(2, 100, 150)]}


def test_a_rerun_competitors_earlier_attempt_jobs_are_counted():
    seen = []
    fetch = fetcher(
        [competitor(900, attempt=2)],
        attempt_jobs={900: LATEST_ONLY},
        all_jobs={900: EARLIER_ATTEMPT},
        seen=seen,
    )

    report = observe(fetch)

    (row,) = report["samples"]
    assert (row["attribution"], row["concurrent_pull_request_jobs"]) == ("pull_request", 1)
    assert report["attribution"]["job_reads"] == 1
    assert any("/runs/900/jobs?filter=all" in endpoint for endpoint in seen)
    assert not any("/runs/900/attempts/" in endpoint for endpoint in seen)


def test_a_first_attempt_competitor_keeps_the_attempt_job_read():
    seen = []
    first = {"total_count": 1, "jobs": [job(1, 0, 90)]}
    fetch = fetcher([competitor(900, attempt=1)], attempt_jobs={900: first}, all_jobs={}, seen=seen)

    report = observe(fetch)

    assert report["samples"][0]["concurrent_pull_request_jobs"] == 1
    assert any("/runs/900/attempts/1/jobs" in endpoint for endpoint in seen)
    assert not any("filter=all" in endpoint for endpoint in seen)


def test_an_overflowing_rerun_page_is_unattributable_with_a_reason():
    overflow = {"total_count": 101, "jobs": [job(1, 0, 90)]}
    fetch = fetcher([competitor(900, attempt=2)], attempt_jobs={}, all_jobs={900: overflow})

    report = observe(fetch)

    (row,) = report["samples"]
    assert row["attribution"] == "unattributable"
    assert row["unattributable_reason"] == "rerun-attempt"
    assert report["attribution"]["unattributable_reasons"] == {"rerun-attempt": 1}
    # The verdict and error state are untouched by an attribution gap.
    assert report["verdict"] == "INSUFFICIENT_SAMPLES" and report["failure"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "x", "status": "completed", "conclusion": "success"},  # no run_attempt
        {**job(1, 0, 90), "run_attempt": None},
        {**job(1, 0, 90), "run_attempt": 3},  # newer than the run's own latest attempt
    ],
)
def test_ambiguous_attempt_timing_is_unattributable_not_zero(bad):
    page = {"total_count": 1, "jobs": [bad]}
    fetch = fetcher([competitor(900, attempt=2)], attempt_jobs={}, all_jobs={900: page})

    (row,) = observe(fetch)["samples"]

    assert row["attribution"] == "unattributable"
    assert row["unattributable_reason"] == "rerun-attempt"


def test_rerun_reads_count_against_the_same_job_read_budget(monkeypatch):
    monkeypatch.setattr(latency, "CONCURRENT_RUN_READS", 1)
    fetch = fetcher(
        [competitor(900, attempt=2), competitor(901, attempt=2)],
        attempt_jobs={},
        all_jobs={900: EARLIER_ATTEMPT, 901: EARLIER_ATTEMPT},
    )

    report = observe(fetch)

    assert report["attribution"]["job_reads"] == 1
    assert report["attribution"]["job_read_budget"] == 1
    (row,) = report["samples"]
    assert row["unattributable_reason"] == "read-budget"


def test_rerun_attempt_is_a_documented_reason():
    assert "rerun-attempt" in latency.UNATTRIBUTABLE_REASONS
    docs = (Path(__file__).resolve().parents[2] / "docs/operations/ci-latency.md").read_text()
    assert "`rerun-attempt`" in docs
    assert "bu-eb32hj" not in docs


def test_report_md_states_that_rerun_earlier_attempts_are_read(tmp_path):
    fetch = fetcher(
        [competitor(900, attempt=2)],
        attempt_jobs={900: LATEST_ONLY},
        all_jobs={900: EARLIER_ATTEMPT},
    )

    latency.write_report(observe(fetch), tmp_path)

    markdown = (tmp_path / "report.md").read_text()
    assert "earlier attempts" in markdown
    assert "Only each other run's latest attempt" not in markdown

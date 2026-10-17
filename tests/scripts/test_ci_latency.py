"""Timestamp-only observer contracts; no logs, test execution or gate authority."""

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


def run(identifier=1, *, attempt=1, conclusion="success"):
    return {
        "id": identifier,
        "run_attempt": attempt,
        "head_sha": "a" * 40,
        "event": "merge_group",
        "status": "completed",
        "conclusion": conclusion,
        "created_at": stamp(),
    }


def jobs(row, *, inventory=False):
    result = []
    # The shard is ready well before guards under the new inventory DAG.
    for index, (name, ready, start, end) in enumerate(
        [
            ("route", 0, 2, 10),
            ("inventory" if inventory else "guards", 10, 12, 50),
            ("check-unit (1)", 50, 55, 200),
            ("guards" if inventory else "check-preflight", 10 if inventory else 200, 60, 220),
            ("check", 220, 222, 230),
            ("frontend", 200, 201, 240),
            ("coverage", 230, 232, 270),
        ]
    ):
        # Every terminal job has a valid ordered timestamp envelope.
        start = max(ready, start)
        result.append(
            {
                "id": index + 1,
                "run_id": row["id"],
                "run_attempt": row["run_attempt"],
                "head_sha": row["head_sha"],
                "name": name,
                "status": "completed",
                "conclusion": "success",
                "created_at": stamp(ready),
                "started_at": stamp(start),
                "completed_at": stamp(end),
            }
        )
    skipped = copy.deepcopy(result[0])
    skipped.update(
        id=1000,
        name="check-affected",
        conclusion="skipped",
        created_at=stamp(51),
        started_at=stamp(51),
        completed_at=stamp(50),
    )
    result.append(skipped)
    return {"total_count": len(result), "jobs": result}


def fake_api(rows, *, alter=None):
    calls = []

    def fetch(endpoint):
        calls.append(endpoint)
        if "/attempts/" not in endpoint:
            return {"total_count": len(rows), "workflow_runs": copy.deepcopy(rows)}
        row = next(row for row in rows if f"/runs/{row['id']}/" in endpoint)
        value = jobs(row)
        if alter:
            alter(value)
        return value

    return fetch, calls


def test_metrics_use_own_job_ready_time_for_both_dags():
    for inventory in (False, True):
        row = run()
        sample = latency.sample(row, jobs(row, inventory=inventory))
        assert sample["required_elapsed_s"] == 240
        assert sample["workflow_elapsed_s"] == 270
        assert sample["workflow_tail_s"] == 30
        assert sample["max_shard_scheduling_s"] == 5
        assert sample["max_shard_execution_s"] == 145
        assert sample["guards_execution_s"] == (160 if inventory else 38)


def test_objective_counts_only_clean_successes_and_explicit_cohort():
    """REQ-ci-latency-001: measured service budget with honest population admission."""
    rows = [run(i) for i in range(1, 21)]
    fetch, calls = fake_api(rows)
    report = latency.observe(repository="owner/repo", since=stamp(), fetch=fetch)
    assert report["verdict"] == "PASS"
    assert report["statistics"]["required_elapsed_s"]["p95"] == 240
    assert all("/attempts/1/jobs?" in path for path in calls[1:])
    assert latency.observe(repository="owner/repo", fetch=fetch)["verdict"] == "PASS"
    rows[-1]["run_attempt"] = 2
    rows.extend([run(21, conclusion="failure"), run(22, conclusion="cancelled")])
    report = latency.observe(repository="owner/repo", since=stamp(), fetch=fetch)
    assert report["verdict"] == "INSUFFICIENT_SAMPLES"
    assert report["counts"] == {
        "runs": 22,
        "success": 20,
        "failure": 1,
        "cancelled": 1,
        "other_terminal": 0,
        "in_progress": 0,
        "retried_runs": 1,
        "clean_samples": 19,
    }
    assert (
        latency.observe(repository="owner/repo", since=stamp(), target=100, fetch=fetch)[
            "observed_target"
        ]
        == "BREACH"
    )
    assert latency.statistics([1, 2, 3, 4, 100])["p95"] == 100


def test_malformed_or_incomplete_metadata_cannot_pass():
    # One compact table guards API/attempt/time/population admission together.
    for mutate in (
        lambda value: value.update(total_count=101),
        lambda value: value.update(total_count=True),
        lambda value: value["jobs"].pop(),
        lambda value: value["jobs"].append(copy.deepcopy(value["jobs"][0])),
        lambda value: value["jobs"][0].update(run_attempt=2),
        lambda value: value["jobs"][0].update(head_sha="b" * 40),
        lambda value: value["jobs"][0].update(run_id=True),
        lambda value: value["jobs"][0].update(status="in_progress"),
        lambda value: value["jobs"][0].update(started_at=None),
        lambda value: value["jobs"][0].update(completed_at=stamp(-1)),
        lambda value: value["jobs"][4].update(conclusion="failure"),
        lambda value: value["jobs"][4].update(name="missing-check"),
    ):
        fetch, _ = fake_api([run(i) for i in range(1, 21)], alter=mutate)
        report = latency.observe(repository="owner/repo", since=stamp(), fetch=fetch)
        assert report["verdict"] == "ERROR", mutate
        assert report["failure"]["kind"] == "invalid-metadata"
    fetch, _ = fake_api([run()])
    for mutate in (
        lambda value: value.update(total_count=101),
        lambda value: value["workflow_runs"].append(run()),
        lambda value: value["workflow_runs"][0].update(run_attempt=True),
        lambda value: value["workflow_runs"][0].update(event="push"),
    ):

        def malformed(endpoint):
            result = fetch(endpoint)
            mutate(result)
            return result

        assert (
            latency.observe(repository="owner/repo", since=stamp(), fetch=malformed)["verdict"]
            == "ERROR"
        )


def test_bounded_read_and_error_report_are_closed(tmp_path):
    def fail(_):
        raise TimeoutError("sensitive response must never appear")

    report = latency.observe(repository="owner/repo", fetch=fail)
    assert report["verdict"] == "ERROR"
    assert report["failure"]["kind"] == "read-timeout"
    assert "sensitive" not in str(report)
    latency.write_report(report, tmp_path)
    assert "ERROR" in (tmp_path / "report.md").read_text()
    assert (tmp_path / "report.json").is_file()
    import yaml

    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci-latency.yml").read_text())
    job = workflow["jobs"]["observe"]
    assert job["timeout-minutes"] == 6
    assert len(workflow["jobs"]) == 1
    assert "report.md" in job["steps"][1]["run"]
    assert job["steps"][2]["if"] == "always()"
    assert job["steps"][2]["with"]["name"] == "ci-latency"
    assert "ci_latency" not in (root / ".github/workflows/ci-shard-weights.yml").read_text()
    assert (
        latency.observe(
            repository="owner/repo",
            since=stamp(),
            fetch=lambda _: {"total_count": 0, "workflow_runs": []},
        )["verdict"]
        == "INSUFFICIENT_SAMPLES"
    )
    # A default window is intentionally bounded, while a since cohort must be complete.
    fetch, _ = fake_api([run(i) for i in range(1, 101)])

    def window(endpoint):
        result = fetch(endpoint)
        if "workflow_runs" in result:
            result["total_count"] = 1000
        return result

    assert latency.observe(repository="owner/repo", fetch=window)["verdict"] == "PASS"
    assert (
        latency.observe(repository="owner/repo", since=stamp(), fetch=window)["verdict"] == "ERROR"
    )


def test_observer_deadline_stops_the_next_read_and_retains_error(tmp_path, monkeypatch):
    """REQ-ci-latency-001: an exhausted observer cannot start another read."""
    import json

    # Permit the index read, then reach the exact whole-observer deadline.
    # A read-level timeout alone cannot prevent another request after this point.
    fetch, calls = fake_api([run()])
    clock = iter((0, 0, 240))
    with monkeypatch.context() as context:
        context.setattr(latency.time, "monotonic", lambda: next(clock))
        expired = latency.observe(repository="owner/repo", since=stamp(), fetch=fetch)
    assert len(calls) == 1
    assert expired["verdict"] == "ERROR"
    assert expired["failure"] == {
        "stage": "attempt-jobs",
        "run": 1,
        "kind": "read-timeout",
    }
    assert expired["samples"] == []
    deadline_output = tmp_path / "deadline"
    latency.write_report(expired, deadline_output)
    assert json.loads((deadline_output / "report.json").read_text()) == expired
    assert "ERROR" in (deadline_output / "report.md").read_text()


def test_workflow_preserves_observer_exit_and_retained_summary(tmp_path):
    """REQ-ci-latency-001: actual scheduled shell preserves every observer verdict."""
    import json
    import os
    import subprocess

    import yaml

    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci-latency.yml").read_text())
    job = workflow["jobs"]["observe"]
    # Execute the real workflow scalar in a miniature checkout. Only the
    # observer boundary is synthetic; bash exit handling and summary writes
    # are the exact scheduled commands, including their nonzero paths.
    checkout = tmp_path / "workflow"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "ci_latency.py").write_text(
        "import argparse,json,os\n"
        "from pathlib import Path\n"
        "parser=argparse.ArgumentParser()\n"
        "parser.add_argument('--output',type=Path,required=True)\n"
        "parser.add_argument('--since')\n"
        "args=parser.parse_args()\n"
        "code=int(os.environ['OBSERVER_EXIT'])\n"
        "verdict=('PASS','BREACH','INSUFFICIENT_SAMPLES','ERROR')[code]\n"
        "args.output.mkdir(parents=True)\n"
        "(args.output/'report.json').write_text(json.dumps(\n"
        " {'verdict':verdict,'since':args.since})+'\\n')\n"
        "(args.output/'report.md').write_text('Verdict: '+verdict+'\\n')\n"
        "raise SystemExit(code)\n"
    )
    for code, verdict in enumerate(("PASS", "BREACH", "INSUFFICIENT_SAMPLES", "ERROR")):
        runner_temp = tmp_path / f"runner-{code}"
        runner_temp.mkdir()
        summary = runner_temp / "summary.md"
        summary.write_text("Earlier summary\n")
        since = stamp() if code % 2 else ""
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", job["steps"][1]["run"]],
            cwd=checkout,
            env={
                "PATH": os.environ["PATH"],
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_STEP_SUMMARY": str(summary),
                "LATENCY_SINCE": since,
                "OBSERVER_EXIT": str(code),
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == code, (result.stdout, result.stderr)
        retained = runner_temp / "ci-latency"
        assert json.loads((retained / "report.json").read_text()) == {
            "verdict": verdict,
            "since": since or None,
        }
        markdown = (retained / "report.md").read_text()
        assert markdown == f"Verdict: {verdict}\n"
        assert summary.read_text() == "Earlier summary\n" + markdown


def test_gh_transport_refuses_partial_duplicate_or_failed_payloads(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    for result in (
        SimpleNamespace(returncode=1, stdout=b"{}"),
        SimpleNamespace(returncode=0, stdout=b'{"jobs":[],"jobs":[]}'),
        SimpleNamespace(returncode=0, stdout=b'{"jobs":'),
        SimpleNamespace(returncode=0, stdout=b" " * (4 * 1024 * 1024 + 1)),
    ):

        def execute(command, **kwargs):
            assert command == ["gh", "api", "repos/owner/repo/actions/runs"]
            assert kwargs == {"capture_output": True, "timeout": 20}
            return result

        monkeypatch.setattr(subprocess, "run", execute)
        with pytest.raises(ValueError):
            latency.api("repos/owner/repo/actions/runs")

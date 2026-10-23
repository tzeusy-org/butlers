"""Bounded, read-only Actions timestamp report; never a required merge gate.

Only clean first-attempt successes enter the latency objective. Run outcomes and
retries are counted separately. No logs or artifacts are read by this observer.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics as stats
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

REQUIRED = {"check", "guards", "frontend"}
TARGET_SECONDS = 420
MIN_SAMPLES = 20
HORIZON = 100
SAMPLE_LIMIT = 30
#: Runs created this long before a shard was created are still candidates for being in flight.
CONCURRENCY_WINDOW = 2 * 3600
RUN_INDEX_PAGES = 3
#: Job reads beyond the sample runs' own (already fetched) jobs. A normal 30-sample window
#: needs on the order of 100; samples are read newest first, so the oldest lose first.
CONCURRENT_RUN_READS = 120
NOT_ON_A_RUNNER = {"queued", "waiting", "pending", "requested"}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != UTC.utcoffset(result):
        raise ValueError("UTC timestamp")
    return result.timestamp()


def identifier(value):
    if type(value) is not int or not 0 < value < 10**20:
        raise ValueError("identifier")
    return value


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def api(endpoint):
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, timeout=20)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("unavailable bounded read")
    return json.loads(result.stdout, object_pairs_hook=strict_object)


def population(response, key, *, limit, complete):
    if not isinstance(response, dict):
        raise ValueError("response")
    total, rows = response["total_count"], response[key]
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise ValueError("population")
    if len(rows) != min(total, limit) or (complete and total > limit):
        raise ValueError("truncated population")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("row")
    identities = [identifier(row["id"]) for row in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate identity")
    return rows


def sample(run, response):
    jobs = population(response, "jobs", limit=100, complete=True)
    origin = timestamp(run["created_at"])
    lookup, executions, scheduling, shards = {}, [], [], []
    for job in jobs:
        if (
            identifier(job["run_id"]) != run["id"]
            or identifier(job["run_attempt"]) != run["run_attempt"]
            or job["head_sha"] != run["head_sha"]
            or job["status"] != "completed"
            or job["conclusion"] not in {"success", "skipped", "neutral"}
            or not isinstance(job["name"], str)
            or job["name"] in lookup
        ):
            raise ValueError("attempt or terminal job")
        end = timestamp(job["completed_at"])
        if end < origin:
            raise ValueError("completion before run")
        start = None
        if job["conclusion"] == "success":
            ready = timestamp(job["created_at"])
            start = timestamp(job["started_at"])
            if not origin <= ready <= start <= end:
                raise ValueError("timestamp order")
        # Skipped jobs did not execute. GitHub can report their synthetic
        # started_at one second after completed_at; only completion is used.
        lookup[job["name"]] = (job, start, end)
        executions.append(end)
        if job["conclusion"] == "success":
            scheduling.append(start - ready)
        if re.fullmatch(r"check-(?:unit|integration) \([1-9][0-9]*\)", job["name"]):
            if job["conclusion"] != "success":
                raise ValueError("unsuccessful shard")
            shards.append((end - start, start - ready))
    if not REQUIRED <= lookup.keys() or not shards:
        raise ValueError("missing required jobs")
    if any(lookup[name][0]["conclusion"] != "success" for name in REQUIRED):
        raise ValueError("unsuccessful required context")
    required_end = max(lookup[name][2] for name in REQUIRED)
    workflow_end = max(executions)
    return {
        "run": run["id"],
        "attempt": run["run_attempt"],
        "head": run["head_sha"],
        "created_at": run["created_at"],
        "required_elapsed_s": required_end - origin,
        "workflow_elapsed_s": workflow_end - origin,
        "workflow_tail_s": workflow_end - required_end,
        "guards_execution_s": lookup["guards"][2] - lookup["guards"][1],
        "max_shard_execution_s": max(row[0] for row in shards),
        "max_shard_scheduling_s": max(row[1] for row in shards),
        "max_job_scheduling_s": max(scheduling),
    }


def event_class(event):
    if event in {"pull_request", "pull_request_target"}:
        return "pull_request"
    return "merge_group" if event == "merge_group" else "other"


def concurrency(at, runs):
    """Jobs executing on a runner at ``at``, split by the event that triggered their run.

    ``runs`` is ``[(event, jobs)]`` where ``jobs`` is the run's job list, or a string naming
    why it could not be read (``None`` counts as ``unreadable-run``). A job that ran but lacks
    usable timestamps makes the whole sample unattributable rather than letting it silently
    count as zero; the sample records why.
    """
    counts = {"pull_request": 0, "merge_group": 0, "other": 0}
    for event, jobs in runs:
        if jobs is None or isinstance(jobs, str):
            return unattributable(jobs or "unreadable-run")
        for job in jobs:
            try:
                status, conclusion = job["status"], job["conclusion"]
                if status in NOT_ON_A_RUNNER or conclusion == "skipped":
                    continue
                if conclusion == "cancelled" and job["started_at"] is None:
                    continue  # cancelled before it ever started
                start = timestamp(job["started_at"])
                end = None if status == "in_progress" else timestamp(job["completed_at"])
            except (KeyError, TypeError, ValueError):
                return unattributable("missing-timestamps")
            if start <= at and (end is None or at < end):
                counts[event_class(event)] += 1
    present = [name for name, value in counts.items() if value]
    return {
        "concurrent_pull_request_jobs": counts["pull_request"],
        "concurrent_merge_group_jobs": counts["merge_group"],
        "concurrent_other_jobs": counts["other"],
        "attribution": "none" if not present else present[0] if len(present) == 1 else "mixed",
        "unattributable_reason": None,
    }


UNATTRIBUTABLE_REASONS = (
    "read-budget",
    "unreadable-run",
    "over-100-jobs",
    "rerun-attempt",
    "missing-timestamps",
    "index-truncated",
    "stage-failure",
    "deadline",
)


def unattributable(reason):
    assert reason in UNATTRIBUTABLE_REASONS
    return {
        "concurrent_pull_request_jobs": None,
        "concurrent_merge_group_jobs": None,
        "concurrent_other_jobs": None,
        "attribution": "unattributable",
        "unattributable_reason": reason,
    }


def hourly(samples):
    rows = {}
    for row in samples:
        hour = row["created_at"][:13] + ":00:00Z"
        entry = rows.setdefault(
            hour,
            {
                "hour": hour,
                "samples": 0,
                "none": 0,
                "pull_request": 0,
                "merge_group": 0,
                "other": 0,
                "mixed": 0,
                "unattributable": 0,
                "max_shard_scheduling_s": 0,
                "_pr": [],
                "_mg": [],
            },
        )
        entry["samples"] += 1
        entry[row["attribution"]] += 1
        entry["max_shard_scheduling_s"] = max(
            entry["max_shard_scheduling_s"], row["max_shard_scheduling_s"]
        )
        if row["attribution"] != "unattributable":
            entry["_pr"].append(row["concurrent_pull_request_jobs"])
            entry["_mg"].append(row["concurrent_merge_group_jobs"])
    result = []
    for hour in sorted(rows):
        entry = rows[hour]
        pr, mg = entry.pop("_pr"), entry.pop("_mg")
        entry["mean_concurrent_pull_request_jobs"] = round(sum(pr) / len(pr), 2) if pr else None
        entry["mean_concurrent_merge_group_jobs"] = round(sum(mg) / len(mg), 2) if mg else None
        result.append(entry)
    return result


def attribute(read, repository, samples, sample_jobs):
    """Attach in-flight job counts at shard creation to each sample; never raises.

    Reads one all-workflow run index (at most RUN_INDEX_PAGES pages) and at most
    CONCURRENT_RUN_READS job lists of other runs; the sample runs' own jobs are reused.
    """
    summary = {
        "window_s": CONCURRENCY_WINDOW,
        "run_index_pages": 0,
        "run_index_truncated": False,
        "job_reads": 0,
        "job_read_budget": CONCURRENT_RUN_READS,
        "failure": None,
        "unattributable_reasons": {},
    }
    created = {
        row["run"]: min(
            timestamp(job["created_at"])
            for job in sample_jobs[row["run"]]
            if re.fullmatch(r"check-(?:unit|integration) \([1-9][0-9]*\)", job["name"])
        )
        for row in samples
    }
    try:
        floor = min(created.values()) - CONCURRENCY_WINDOW
        window = datetime.fromtimestamp(floor, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        indexed, total = [], 0
        for page in range(1, RUN_INDEX_PAGES + 1):
            query = urlencode({"created": ">=" + window, "per_page": 100, "page": page})
            response = read(f"repos/{repository}/actions/runs?{query}")
            summary["run_index_pages"] += 1
            total = response["total_count"]
            rows = response["workflow_runs"]
            indexed.extend(rows)
            if not rows or len(indexed) >= total:
                break
        summary["run_index_truncated"] = len(indexed) < total
        # The index is newest-first, so truncation loses the oldest runs.
        covered_from = (
            min(timestamp(run["created_at"]) for run in indexed)
            if summary["run_index_truncated"]
            else None
        )
        cache = {row["run"]: sample_jobs[row["run"]] for row in samples}

        def jobs_of(run):
            """The run's jobs, or the reason they are unavailable."""
            key = run["id"]
            if key not in cache:
                if summary["job_reads"] >= CONCURRENT_RUN_READS:
                    cache[key] = "read-budget"
                else:
                    summary["job_reads"] += 1
                    try:
                        # A re-run's in-flight jobs at the instant may belong to an earlier
                        # attempt, so read every attempt in the same single bounded page.
                        rerun = run["run_attempt"] > 1
                        if rerun:
                            endpoint = (
                                f"repos/{repository}/actions/runs/{key}/"
                                "jobs?filter=all&per_page=100"
                            )
                        else:
                            endpoint = (
                                f"repos/{repository}/actions/runs/{key}/attempts/"
                                f"{run['run_attempt']}/jobs?per_page=100"
                            )
                        response = read(endpoint)
                        if not isinstance(response["jobs"], list):
                            cache[key] = "unreadable-run"
                        elif response["total_count"] > 100:
                            cache[key] = "rerun-attempt" if rerun else "over-100-jobs"
                        elif rerun and not all(
                            isinstance(job.get("run_attempt"), int)
                            and 1 <= job["run_attempt"] <= run["run_attempt"]
                            for job in response["jobs"]
                        ):
                            cache[key] = "rerun-attempt"
                        else:
                            cache[key] = response["jobs"]
                    except TimeoutError:
                        cache[key] = "deadline"
                    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
                        cache[key] = "unreadable-run"
            return cache[key]

        # Newest first, so an exhausted read budget costs the oldest samples.
        for row in sorted(samples, key=lambda item: created[item["run"]], reverse=True):
            at = created[row["run"]]
            if covered_from is not None and at - CONCURRENCY_WINDOW < covered_from:
                row.update(unattributable("index-truncated"))
                continue
            candidates = [
                run
                for run in indexed
                if run["id"] != row["run"]
                and timestamp(run["created_at"]) <= at
                and (run["status"] != "completed" or timestamp(run["updated_at"]) >= at)
            ]
            row.update(concurrency(at, [(run["event"], jobs_of(run)) for run in candidates]))
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as exc:
        summary["failure"] = "read-timeout" if isinstance(exc, TimeoutError) else "read-unavailable"
        for row in samples:
            row.update(
                unattributable("deadline" if isinstance(exc, TimeoutError) else "stage-failure")
            )
    for row in samples:
        if row["attribution"] == "unattributable":
            reason = row["unattributable_reason"]
            summary["unattributable_reasons"][reason] = (
                summary["unattributable_reasons"].get(reason, 0) + 1
            )
    summary["hourly"] = hourly(samples)
    return summary


def statistics(values):
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "p50": stats.median(ordered),
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "max": ordered[-1],
    }


def observe(*, repository, since=None, target=TARGET_SECONDS, fetch=api, attribution=False):
    report = {
        "schema": "ci-latency.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "repository": None,
        "cohort": {
            "since": since,
            "kind": "since" if since else "rolling-mixed-rollout",
            "run_horizon": HORIZON,
            "sample_limit": SAMPLE_LIMIT,
        },
        "target_s": target,
        "minimum_samples": MIN_SAMPLES,
        "verdict": "ERROR",
        "observed_target": None,
        "counts": {
            "runs": 0,
            "success": 0,
            "failure": 0,
            "cancelled": 0,
            "other_terminal": 0,
            "in_progress": 0,
            "retried_runs": 0,
            "clean_samples": 0,
        },
        "samples": [],
        "statistics": {},
        "failure": None,
    }
    stage, selected, sample_jobs = "configuration", None, {}
    deadline = time.monotonic() + 240

    def read(endpoint):
        if time.monotonic() >= deadline:
            raise TimeoutError("observer deadline")
        return fetch(endpoint)

    try:
        if not isinstance(repository, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
        ):
            raise ValueError("repository")
        report["repository"] = repository
        if type(target) is not int or not 0 < target <= 86400:
            raise ValueError("target")
        since_time = timestamp(since) if since is not None else None
        stage = "run-index"
        query = {"event": "merge_group", "per_page": HORIZON}
        if since is not None:
            query["created"] = ">=" + since
        response = read(f"repos/{repository}/actions/workflows/ci.yml/runs?{urlencode(query)}")
        rows = population(response, "workflow_runs", limit=HORIZON, complete=since is not None)
        report["cohort"]["index_total_count"] = response["total_count"]
        eligible = []
        for run in rows:
            identifier(run["run_attempt"])
            created = timestamp(run["created_at"])
            if (
                run["event"] != "merge_group"
                or not isinstance(run["head_sha"], str)
                or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
                or (since_time is not None and created < since_time)
            ):
                raise ValueError("run metadata")
            counts = report["counts"]
            counts["runs"] += 1
            counts["retried_runs"] += run["run_attempt"] > 1
            if run["status"] != "completed":
                if run["status"] not in {
                    "queued",
                    "in_progress",
                    "waiting",
                    "requested",
                    "pending",
                }:
                    raise ValueError("run status")
                if run["conclusion"] is not None:
                    raise ValueError("nonterminal conclusion")
                counts["in_progress"] += 1
            else:
                conclusion = run["conclusion"]
                if conclusion not in {
                    "success",
                    "failure",
                    "cancelled",
                    "neutral",
                    "skipped",
                    "timed_out",
                    "action_required",
                    "stale",
                    "startup_failure",
                }:
                    raise ValueError("run conclusion")
                counts[
                    conclusion
                    if conclusion in {"success", "failure", "cancelled"}
                    else "other_terminal"
                ] += 1
                if conclusion == "success" and run["run_attempt"] == 1:
                    eligible.append(run)
        report["cohort"]["eligible_successes"] = len(eligible)
        for run in sorted(eligible, key=lambda row: timestamp(row["created_at"]), reverse=True)[
            :SAMPLE_LIMIT
        ]:
            stage, selected = "attempt-jobs", run["id"]
            response = read(
                f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
            )
            report["samples"].append(sample(run, response))
            sample_jobs[run["id"]] = response["jobs"]
            report["counts"]["clean_samples"] += 1
        if attribution and report["samples"]:
            stage, selected = "concurrency-attribution", None
            report["attribution"] = attribute(read, repository, report["samples"], sample_jobs)
        if report["samples"]:
            report["statistics"] = {
                name: statistics([row[name] for row in report["samples"]])
                for name in report["samples"][0]
                if name.endswith("_s")
            }
            report["observed_target"] = (
                "PASS" if report["statistics"]["required_elapsed_s"]["p95"] < target else "BREACH"
            )
        report["verdict"] = (
            "INSUFFICIENT_SAMPLES"
            if len(report["samples"]) < MIN_SAMPLES
            else report["observed_target"]
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as exc:
        # Fixed diagnostics only: no exception text, response body, command output or logs.
        report["failure"] = {
            "stage": stage,
            "run": selected,
            "kind": "read-timeout"
            if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))
            else "read-unavailable"
            if isinstance(exc, (OSError, subprocess.SubprocessError))
            else "invalid-metadata",
        }
    return report


def write_report(report, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    lines = [
        "# CI latency objective",
        "",
        f"Verdict: **{report['verdict']}**. Objective: p95 < {report['target_s']} seconds; "
        f"at least {report['minimum_samples']} clean successes.",
        "",
        f"Cohort: `{report['cohort']['kind']}`; since: `{report['cohort']['since']}`. "
        f"Newest {SAMPLE_LIMIT} eligible successes in at most {HORIZON} indexed runs.",
        "",
        "Rolling samples may mix rollouts and cannot attribute gains to a particular change. "
        "Use --since after the complete rollout to assess that change.",
        "",
        "Success-only first-attempt latency excludes failed/retried attempts and wait before "
        "Actions creates a run. Counts describe latest indexed run outcomes, not every historical "
        "attempt. Retries are counted and excluded from the objective.",
        "",
        "Required completion: created_at through max completed_at(check, guards, frontend). "
        "Workflow completion: last job completed_at, never workflow updated_at. "
        "Tail: workflow completion minus required completion. Scheduling: job started_at minus "
        "its own created_at; this includes scheduling overhead and does not prove runner quota. "
        "Execution: completed_at minus started_at. p95 uses nearest rank.",
        "",
        "| Count | Value |",
        "| --- | ---: |",
        *(f"| {name} | {value} |" for name, value in report["counts"].items()),
        "",
        "| Metric (seconds) | p50 | p95 | max |",
        "| --- | ---: | ---: | ---: |",
        *(
            f"| {name} | {value['p50']:g} | {value['p95']:g} | {value['max']:g} |"
            for name, value in report["statistics"].items()
        ),
        "",
    ]
    attribution = report.get("attribution")
    if attribution:
        lines += [
            "Shard scheduling wait attribution: jobs executing on a runner when each merge_group "
            "run's shards were created, by triggering event (own run excluded). Unattributable "
            "samples are counted, not dropped, with the cause of each recorded. Each other run's "
            "latest attempt is read, and a re-run's earlier attempts too (one filter=all page). "
            "Extra job reads: "
            f"{attribution['job_reads']} of "
            f"{attribution['job_read_budget']}; run-index pages: {attribution['run_index_pages']}"
            + ("; run index truncated" if attribution["run_index_truncated"] else "")
            + (f"; failure: {attribution['failure']}" if attribution["failure"] else "")
            + ".",
            "",
            "Unattributable causes: "
            + (
                ", ".join(
                    f"{reason} {count}"
                    for reason, count in sorted(attribution["unattributable_reasons"].items())
                )
                or "none"
            )
            + ".",
            "",
            "| Hour (UTC) | Samples | None | PR | merge_group | Other | Mixed | Unattributable "
            "| Max shard wait (s) | Mean PR jobs | Mean merge_group jobs |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            *(
                f"| {row['hour']} | {row['samples']} | {row['none']} | {row['pull_request']} "
                f"| {row['merge_group']} | {row['other']} | {row['mixed']} "
                f"| {row['unattributable']} | {row['max_shard_scheduling_s']:g} "
                f"| {row['mean_concurrent_pull_request_jobs']} "
                f"| {row['mean_concurrent_merge_group_jobs']} |"
                for row in attribution["hourly"]
            ),
            "",
        ]
    if report["failure"]:
        lines.append(
            "Collection error: " + json.dumps(report["failure"]) + ". Partial samples cannot pass."
        )
    if report["verdict"] == "INSUFFICIENT_SAMPLES":
        lines.append(
            f"Provisional observed target: {report['observed_target']}; insufficient evidence."
        )
    lines.append(
        "\nOwner: bu-q1hzqn (CI latency); objective remains open until "
        "natural post-rollout evidence."
    )
    (output / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY", "tzeusy-org/butlers")
    )
    parser.add_argument(
        "--since", help="UTC rollout timestamp; complete after-since cohort, max 100 runs"
    )
    parser.add_argument("--target", type=int, default=TARGET_SECONDS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = observe(
        repository=args.repository, since=args.since, target=args.target, attribution=True
    )
    write_report(report, args.output)
    print(f"CI latency: {report['verdict']} ({report['counts']['clean_samples']} clean samples)")
    return {"PASS": 0, "BREACH": 1, "INSUFFICIENT_SAMPLES": 2, "ERROR": 3}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())

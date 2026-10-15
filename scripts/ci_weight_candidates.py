"""Read bounded successful CI artifacts into an advisory, uncommitted candidate.

The ordinary read-only GitHub token is used by gh, never printed. Only complete
same-run opaque receipts can supply durations. Failed/absent evidence produces an
UNKNOWN receipt, never an update or a membership decision.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import statistics
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ci_partition import (
    DIMENSIONS,
    HEX,
    MAX_ADVISORY_FILE_SECONDS,
    ROOT,
    SELECTORS,
    body_digest,
    exact_json,
    load_advisory_weights,
    reconcile,
    unique_object,
    weight_runtime,
)


class Provenance:
    """Closed diagnostic coordinates, never raw API/exception content or authority."""

    def __init__(self) -> None:
        self.stage = "configuration"
        self.selected: dict | None = None
        self.inputs: list[dict] = []
        self.repository: str | None = None

    @staticmethod
    def identifier(value) -> str | None:
        if type(value) is int and 0 <= value < 10**20:
            return str(value)
        return None

    def begin(self, stage: str, kind: str, **coordinates) -> dict:
        self.stage = stage
        record = {"kind": kind, "state": "attempted", **coordinates}
        self.inputs.append(record)
        return record

    @staticmethod
    def body_digest(value) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    def document(self, failure: Exception | None = None) -> dict:
        kind = None
        if failure is not None:
            kind = (
                "read-timeout"
                if isinstance(failure, subprocess.TimeoutExpired)
                else "invalid-archive"
                if isinstance(failure, zipfile.BadZipFile)
                else "io-unavailable"
                if isinstance(failure, OSError)
                else "missing-field"
                if isinstance(failure, KeyError)
                else "malformed-field"
                if isinstance(failure, TypeError)
                else "evidence-refused"
            )
            if self.inputs and self.inputs[-1]["state"] == "attempted":
                self.inputs[-1]["state"] = "unavailable"
        return {
            "schema": "ci-weight-provenance.v1",
            "repository": self.repository,
            "selection": "selected" if self.selected is not None else "unselected",
            "selected_run": self.selected,
            "stage": self.stage,
            "attempted_inputs": self.inputs,
            "failure": {"stage": self.stage, "kind": kind} if failure is not None else None,
        }


def api(path: str, *, binary: bool = False, timeout: int = 30):
    result = subprocess.run(["gh", "api", path], capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
        raise ValueError("bounded GitHub read unavailable")
    return result.stdout if binary else json.loads(result.stdout)


def member(blob: bytes, filename: str) -> dict:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        matches = [info for info in archive.infolist() if info.filename == filename]
        if len(matches) != 1 or matches[0].file_size > 16 * 1024 * 1024:
            raise ValueError("candidate artifact member unavailable")
        value = json.loads(archive.read(matches[0]), object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError("candidate evidence malformed")
        return value


def covered_run(repository: str, trace: Provenance) -> dict:
    """Select only genuine owned main schedule/manual coverage executions."""
    trace.repository = (
        repository if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) else None
    )
    if trace.repository is None:
        raise ValueError("covered repository unavailable")
    runs = []
    for page in range(1, 4):
        attempt = trace.begin("run-index", "run-index", page=page)
        response = api(
            f"repos/{repository}/actions/workflows/ci-coverage.yml/runs"
            f"?status=success&branch=main&per_page=50&page={page}",
            timeout=5,
        )
        values = response["workflow_runs"]
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError("covered run index unavailable")
        runs.extend(values)
        attempt.update(state="available", rows=len(values), body_sha256=trace.body_digest(response))
        if len(values) < 50:
            break
    trace.stage = "run-selection"
    for run in runs:
        if (
            isinstance(run, dict)
            and run.get("event") in {"schedule", "workflow_dispatch"}
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("head_branch") == "main"
            and run.get("head_repository", {}).get("full_name") == repository
            and run.get("repository", {}).get("full_name") == repository
            and run.get("path") == ".github/workflows/ci-coverage.yml"
            and run.get("name") == "CI coverage assurance"
            and type(run.get("id")) is int
            and 0 < run["id"] < 10**20
            and type(run.get("run_attempt")) is int
            and 0 < run["run_attempt"] < 10**20
            and isinstance(run.get("head_sha"), str)
            and re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
        ):
            return run
    raise ValueError("no owned successful covered run in bounded horizon")


def candidate(
    *, repository: str, output: Path, provenance: Provenance | None = None, covered: bool = False
) -> None:
    trace = provenance if provenance is not None else Provenance()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        trace.repository = repository

    def read(path, *, binary=False):
        return api(path, binary=binary, timeout=5) if covered else api(path, binary=binary)

    prefix = "covered-" if covered else ""
    if covered:
        run = covered_run(repository, trace)
    else:
        runs = []
        # A finite three-page horizon cannot falsely claim the entire run history.
        for page in range(1, 4):
            attempt = trace.begin("run-index", "run-index", page=page)
            response = api(
                f"repos/{repository}/actions/workflows/ci.yml/runs?event=merge_group&status=success&per_page=50&page={page}"
            )
            values = response["workflow_runs"]
            runs.extend(values)
            attempt.update(
                state="available", rows=len(values), body_sha256=trace.body_digest(response)
            )
            if len(values) < 50:
                break
        trace.stage = "run-selection"
        run = next(
            (
                row
                for row in runs
                if row["event"] == "merge_group" and row["conclusion"] == "success"
            ),
            None,
        )
        if run is None:
            raise ValueError("no successful complete candidate run in bounded horizon")
    head = run.get("head_sha")
    trace.selected = {
        "run": trace.identifier(run.get("id")),
        "attempt": trace.identifier(run.get("run_attempt")),
        "head": head if isinstance(head, str) and re.fullmatch(r"[0-9a-f]{40}", head) else None,
    }
    artifacts = []
    for page in range(1, 4):
        attempt = trace.begin("artifact-index", "artifact-index", page=page)
        response = read(
            f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100&page={page}"
        )
        values = response["artifacts"]
        if covered and (not isinstance(values, list) or len(values) > 100):
            raise ValueError("covered artifact index unavailable")
        artifacts.extend(values)
        attempt.update(state="available", rows=len(values), body_sha256=trace.body_digest(response))
        if len(values) < 100:
            break
    required = {
        "ci-inventory",
        *(
            f"ci-{lane}-{index}-test-evidence"
            for lane, count in DIMENSIONS.items()
            for index in range(1, count + 1)
        ),
    }
    selected = {}
    trace.stage = "artifact-selection"
    for row in artifacts:
        if row["name"] in required:
            attempt = trace.begin(
                "artifact-selection",
                "artifact-metadata",
                artifact=row["name"],
                id=trace.identifier(row.get("id")),
                size_bytes=row.get("size_in_bytes")
                if type(row.get("size_in_bytes")) is int and 0 <= row["size_in_bytes"] < 10**20
                else None,
                expired=row.get("expired") if type(row.get("expired")) is bool else None,
            )
            if row["name"] in selected or row["expired"] or row["size_in_bytes"] > 32 * 1024 * 1024:
                attempt.update(
                    state="refused",
                    reason="duplicate"
                    if row["name"] in selected
                    else "expired"
                    if row["expired"]
                    else "oversized",
                )
                raise ValueError("candidate artifact ambiguous/expired/oversized")
            if covered and (
                row["expired"] is not False
                or type(row["size_in_bytes"]) is not int
                or not 0 < row["size_in_bytes"] <= 32 * 1024 * 1024
                or type(row.get("id")) is not int
                or row["id"] <= 0
                or row["workflow_run"]["id"] != run["id"]
                or row["workflow_run"]["head_sha"] != run["head_sha"]
            ):
                raise ValueError("covered artifact source unavailable")
            selected[row["name"]] = row
            attempt["state"] = "selected"
    if set(selected) != required:
        trace.stage = "artifact-population"
        raise ValueError("candidate eleven-child population unavailable")

    def download(name):
        attempt = trace.begin(
            "artifact-download",
            "artifact-download",
            artifact=name,
            id=trace.identifier(selected[name]["id"]),
        )
        blob = read(f"repos/{repository}/actions/artifacts/{selected[name]['id']}/zip", binary=True)
        attempt.update(state="available", bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        return blob

    def read_member(blob, filename, artifact):
        attempt = trace.begin(
            "artifact-member", "archive-member", artifact=artifact, member=filename
        )
        value = member(blob, filename)
        attempt["state"] = "available"
        return value

    blob = download("ci-inventory")
    inventory = read_member(blob, "inventory.json", "ci-inventory")
    assignment = read_member(blob, "assignment.json", "ci-inventory")
    trace.stage = "inventory-binding"
    identity = inventory["identity"]
    if (
        identity["head"] != run["head_sha"]
        or identity["run"] != str(run["id"])
        or identity["attempt"] != str(run["run_attempt"])
    ):
        raise ValueError("candidate run/attempt/source mismatch")
    if covered and (
        identity["repository"] != repository
        or identity["event"] != run["event"]
        or identity["workflow"] != "CI coverage assurance"
    ):
        raise ValueError("covered source event/workflow mismatch")
    # The explicit historical observer does not rewrite checkout identity or
    # turn these prior receipts into current execution evidence.
    if inventory.get("digest") != body_digest(inventory) or assignment.get("digest") != body_digest(
        assignment
    ):
        raise ValueError("candidate body digest mismatch")
    receipts = {
        name.removeprefix("ci-").removesuffix("-test-evidence"): read_member(
            download(name), "shard-observation.json", name
        )
        for name in sorted(required)
        if name != "ci-inventory"
    }
    # Strict inner protocol validation shares the real verifier; only the
    # explicit observer mode relaxes CURRENT checkout equality for historical
    # evidence, while every source/attempt/population binding remains checked.
    trace.stage = "population-reconciliation"
    reconcile(inventory, assignment, receipts, root=ROOT, current=False)
    trace.stage = "tracing-species"
    observed_tracers = [receipt.get("actual_tracers") for receipt in receipts.values()]
    if any(
        type(value) is not list or value not in ([], ["CTracer"], ["SysMonitor"])
        for value in observed_tracers
    ):
        raise ValueError("candidate tracing species incomplete or mixed")
    tracers = {tuple(value) for value in observed_tracers}
    if len(tracers) != 1:
        raise ValueError("candidate tracing species incomplete or mixed")
    trace.stage = "candidate-durations"
    durations = {lane: {} for lane in DIMENSIONS}
    for lane in DIMENSIONS:
        for index in range(1, DIMENSIONS[lane] + 1):
            for name, seconds in receipts[f"{lane}-{index}"]["file_durations_s"].items():
                durations[lane].setdefault(name, []).append(seconds)
    file_digests = inventory.get("file_digests")
    names = {name for files in inventory["lanes"].values() for name in files}
    if (
        not isinstance(file_digests, dict)
        or set(file_digests) != names
        or any(
            not isinstance(value, str) or not HEX.fullmatch(value)
            for value in file_digests.values()
        )
    ):
        raise ValueError("candidate source file digests unavailable")
    tracer = next(iter(tracers))
    if covered and tracer not in {("CTracer",), ("SysMonitor",)}:
        raise ValueError("covered candidate requires actual coverage tracer")
    profile = {
        "collected_at": inventory["collected_at"],
        "runtime": weight_runtime(inventory),
        "file_digests": file_digests,
        "config_digest": identity["config_digest"],
        "lanes": {
            lane: {name: statistics.median(values) for name, values in files.items()}
            for lane, files in durations.items()
        },
        "state": "advisory candidate from reconciled historical execution",
        "origin": identity,
    }
    result = {"schema": "ci-weights.v2", "profiles": {tracer[0] if tracer else "none": profile}}
    trace.stage = "candidate-write"
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{prefix}candidate.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    trace.stage = "candidate-receipt-write"
    (output / f"{prefix}receipt.json").write_text(
        json.dumps(
            {
                "state": "CANDIDATE",
                "run": str(run["id"]),
                "attempt": str(run["run_attempt"]),
                "head": run["head_sha"],
                "provenance": trace.document(),
                **(
                    {
                        "source": {
                            "repository": repository,
                            "workflow": ".github/workflows/ci-coverage.yml",
                            "branch": "main",
                            "event": run["event"],
                            **trace.selected,
                        }
                    }
                    if covered
                    else {}
                ),
            },
            sort_keys=True,
        )
        + "\n"
    )
    trace.stage = "complete"


def restored_profile(blob: bytes, prefix: str, repository: str, root: Path) -> tuple[str, dict]:
    """Validate one fixed candidate pair; its sibling grants no authority."""
    import pytest

    candidate = member(blob, f"{prefix}candidate.json")
    receipt = member(blob, f"{prefix}receipt.json")
    if candidate["schema"] != "ci-weights.v2" or len(candidate["profiles"]) != 1:
        raise ValueError("observer profile unavailable")
    tracer, profile = next(iter(candidate["profiles"].items()))
    origin = profile["origin"]
    coordinates = {key: origin[key] for key in ("run", "attempt", "head")}
    if (
        tracer not in {"none", "CTracer", "SysMonitor"}
        or not isinstance(origin["head"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", origin["head"])
        or any(
            not isinstance(origin[key], str) or not re.fullmatch(r"[1-9][0-9]*", origin[key])
            for key in ("run", "attempt")
        )
        or receipt["state"] != "CANDIDATE"
        or any(receipt[key] != value for key, value in coordinates.items())
        or receipt["provenance"]["selected_run"] != coordinates
        or receipt["provenance"]["failure"] is not None
        or origin["repository"] != repository
        or (not prefix and origin["event"] != "merge_group")
        or profile["config_digest"] != origin["config_digest"]
    ):
        raise ValueError("observer measurement identity unavailable")
    lock = root / "uv.lock"
    runtime = {
        "python": platform.python_version(),
        "pytest_version": pytest.__version__,
        "selectors": SELECTORS,
        "lock_digest": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None,
    }
    measured_at = datetime.fromisoformat(profile["collected_at"])
    now = datetime.now(UTC)
    age = now - measured_at
    if (
        not exact_json(profile["runtime"], runtime)
        or any(origin[key] != runtime[key] for key in ("python", "lock_digest", "selectors"))
        or not timedelta(0) <= age <= timedelta(days=14)
        or set(profile["lanes"]) != set(DIMENSIONS)
    ):
        raise ValueError("observer runtime unavailable")
    names = set()
    for costs in profile["lanes"].values():
        if (
            not isinstance(costs, dict)
            or not costs
            or any(
                not isinstance(name, str)
                or type(value) not in (int, float)
                or not 0 <= value <= MAX_ADVISORY_FILE_SECONDS
                for name, value in costs.items()
            )
        ):
            raise ValueError("observer durations unavailable")
        names.update(costs)
    hashes = profile["file_digests"]
    if (
        not isinstance(hashes, dict)
        or set(hashes) != names
        or any(not isinstance(value, str) or not HEX.fullmatch(value) for value in hashes.values())
    ):
        raise ValueError("observer file digests unavailable")
    if prefix and (
        tracer not in {"CTracer", "SysMonitor"}
        or origin["event"] not in {"schedule", "workflow_dispatch"}
        or origin["workflow"] != "CI coverage assurance"
        or receipt["source"]
        != {
            "repository": repository,
            "workflow": ".github/workflows/ci-coverage.yml",
            "branch": "main",
            "event": origin["event"],
            **coordinates,
        }
    ):
        raise ValueError("covered measurement source unavailable")
    return tracer, profile


def restore(*, repository: str, output: Path, root: Path = ROOT) -> None:
    """Read one trusted main observer artifact; always leave usable seed fallback.

    Three GitHub reads have five-second bounds. This is advisory scheduling,
    never a source of current inventory or executed-item identity.
    """
    weights = load_advisory_weights(root / ".github/ci-test-weights.json")
    status = {"state": "FALLBACK", "reason": "observer candidate unavailable or incompatible"}
    try:
        runs = api(
            f"repos/{repository}/actions/workflows/ci-shard-weights.yml/runs"
            "?status=success&branch=main&per_page=1",
            timeout=5,
        )["workflow_runs"]
        run = runs[0]
        if (
            run["head_branch"] != "main"
            or run["head_repository"]["full_name"] != repository
            or run["path"] != ".github/workflows/ci-shard-weights.yml"
            or run["status"] != "completed"
            or run["conclusion"] != "success"
            or run["event"] not in {"schedule", "workflow_dispatch"}
        ):
            raise ValueError("observer source unavailable")
        artifacts = api(
            f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100",
            timeout=5,
        )["artifacts"]
        matches = [row for row in artifacts if row["name"] == "ci-weight-candidate"]
        if len(matches) != 1:
            raise ValueError("observer artifact unavailable")
        artifact = matches[0]
        if (
            artifact["expired"] is not False
            or type(artifact["size_in_bytes"]) is not int
            or not 0 < artifact["size_in_bytes"] <= 32 * 1024 * 1024
            or artifact["workflow_run"]["id"] != run["id"]
            or artifact["workflow_run"]["head_sha"] != run["head_sha"]
        ):
            raise ValueError("observer artifact identity unavailable")
        blob = api(
            f"repos/{repository}/actions/artifacts/{artifact['id']}/zip", binary=True, timeout=5
        )
        sources = {}
        for prefix in ("", "covered-"):
            label = "covered" if prefix else "uncovered"
            try:
                tracer, profile = restored_profile(blob, prefix, repository, root)
                profiles = (
                    weights.get("profiles", {}) if weights.get("schema") == "ci-weights.v2" else {}
                )
                previous = profiles.get(tracer)
                state = "RESTORED"
                if isinstance(previous, dict) and previous.get("runtime") == profile["runtime"]:
                    try:
                        old = datetime.fromisoformat(previous["collected_at"])
                        new = datetime.fromisoformat(profile["collected_at"])
                        if (
                            timedelta(0) <= datetime.now(UTC) - old <= timedelta(days=14)
                            and old > new
                        ):
                            profile = previous
                            state = "SEED_NEWER"
                    except (KeyError, TypeError, ValueError, OverflowError):
                        pass
                weights = {"schema": "ci-weights.v2", "profiles": {**profiles, tracer: profile}}
                status = {
                    "state": state,
                    "observer_run": str(run["id"]),
                    "profile": tracer,
                    "sample": {
                        key: profile["origin"].get(key) for key in ("head", "run", "attempt")
                    }
                    if isinstance(profile.get("origin"), dict)
                    else None,
                }
                sources[label] = {"state": state, "profile": tracer}
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                IndexError,
                AttributeError,
                OverflowError,
                zipfile.BadZipFile,
            ):
                sources[label] = {"state": "FALLBACK"}
        status["sources"] = sources
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ):
        pass
    output.mkdir(parents=True, exist_ok=True)
    (output / "weights.json").write_text(json.dumps(weights, sort_keys=True) + "\n")
    (output / "restore-receipt.json").write_text(json.dumps(status, sort_keys=True) + "\n")
    print(json.dumps(status, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if args.restore:
        restore(repository=os.environ.get("GITHUB_REPOSITORY", ""), output=args.output)
        return 0
    accepted = False
    for covered in (False, True):
        prefix = "covered-" if covered else ""
        trace = Provenance()
        try:
            # A reused directory cannot lend an old success to a failed source.
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / f"{prefix}candidate.json").unlink(missing_ok=True)
            candidate(
                repository=os.environ["GITHUB_REPOSITORY"],
                output=args.output,
                provenance=trace,
                covered=covered,
            )
            accepted = True
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            IndexError,
            AttributeError,
            OverflowError,
            subprocess.TimeoutExpired,
            zipfile.BadZipFile,
        ) as failure:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / f"{prefix}candidate.json").unlink(missing_ok=True)
            (args.output / f"{prefix}receipt.json").write_text(
                json.dumps(
                    {
                        "state": "UNKNOWN",
                        "reason": "complete bounded evidence unavailable",
                        "provenance": trace.document(failure),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())

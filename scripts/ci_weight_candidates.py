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
import re
import statistics
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from ci_partition import DIMENSIONS, ROOT, body_digest, reconcile


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


def api(path: str, *, binary: bool = False):
    result = subprocess.run(["gh", "api", path], capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
        raise ValueError("bounded GitHub read unavailable")
    return result.stdout if binary else json.loads(result.stdout)


def member(blob: bytes, filename: str) -> dict:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        matches = [info for info in archive.infolist() if info.filename == filename]
        if len(matches) != 1 or matches[0].file_size > 16 * 1024 * 1024:
            raise ValueError("candidate artifact member unavailable")
        value = json.loads(archive.read(matches[0]))
        if not isinstance(value, dict):
            raise ValueError("candidate evidence malformed")
        return value


def candidate(*, repository: str, output: Path, provenance: Provenance | None = None) -> None:
    trace = provenance if provenance is not None else Provenance()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        trace.repository = repository
    runs = []
    # A finite three-page horizon cannot falsely claim the entire run history.
    for page in range(1, 4):
        attempt = trace.begin("run-index", "run-index", page=page)
        response = api(
            f"repos/{repository}/actions/workflows/ci.yml/runs?event=merge_group&status=success&per_page=50&page={page}"
        )
        values = response["workflow_runs"]
        runs.extend(values)
        attempt.update(state="available", rows=len(values), body_sha256=trace.body_digest(response))
        if len(values) < 50:
            break
    trace.stage = "run-selection"
    run = next(
        (row for row in runs if row["event"] == "merge_group" and row["conclusion"] == "success"),
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
        response = api(
            f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100&page={page}"
        )
        values = response["artifacts"]
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
        blob = api(f"repos/{repository}/actions/artifacts/{selected[name]['id']}/zip", binary=True)
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
    tracers = {tuple(receipt.get("actual_tracers", [])) for receipt in receipts.values()}
    if len(tracers) != 1 or next(iter(tracers)) not in {("CTracer",), ("SysMonitor",)}:
        raise ValueError("candidate tracing species incomplete or mixed")
    trace.stage = "candidate-durations"
    durations = {lane: {} for lane in DIMENSIONS}
    for lane in DIMENSIONS:
        for index in range(1, DIMENSIONS[lane] + 1):
            for name, seconds in receipts[f"{lane}-{index}"]["file_durations_s"].items():
                durations[lane].setdefault(name, []).append(seconds)
    result = {
        "schema": "ci-weights.v1",
        "collected_at": datetime.now(UTC).isoformat(),
        "config_digest": identity["config_digest"],
        "lanes": {
            lane: {name: statistics.median(values) for name, values in files.items()}
            for lane, files in durations.items()
        },
        "state": "uncommitted advisory candidate; reviewed PR required",
        "origin": identity,
    }
    trace.stage = "candidate-write"
    output.mkdir(parents=True, exist_ok=True)
    (output / "candidate.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    trace.stage = "candidate-receipt-write"
    (output / "receipt.json").write_text(
        json.dumps(
            {
                "state": "CANDIDATE",
                "run": str(run["id"]),
                "attempt": str(run["run_attempt"]),
                "head": run["head_sha"],
                "provenance": trace.document(),
            },
            sort_keys=True,
        )
        + "\n"
    )
    trace.stage = "complete"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trace = Provenance()
    try:
        candidate(repository=os.environ["GITHUB_REPOSITORY"], output=args.output, provenance=trace)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as failure:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "receipt.json").write_text(
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
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

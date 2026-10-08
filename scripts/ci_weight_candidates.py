"""Read bounded successful CI artifacts into an advisory, uncommitted candidate.

The ordinary read-only GitHub token is used by gh, never printed. Only complete
same-run opaque receipts can supply durations. Failed/absent evidence produces an
UNKNOWN receipt, never an update or a membership decision.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from ci_partition import DIMENSIONS, ROOT, body_digest, reconcile


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


def candidate(*, repository: str, output: Path) -> None:
    runs = []
    # A finite three-page horizon cannot falsely claim the entire run history.
    for page in range(1, 4):
        values = api(
            f"repos/{repository}/actions/workflows/ci.yml/runs?event=merge_group&status=success&per_page=50&page={page}"
        )["workflow_runs"]
        runs.extend(values)
        if len(values) < 50:
            break
    run = next(
        (row for row in runs if row["event"] == "merge_group" and row["conclusion"] == "success"),
        None,
    )
    if run is None:
        raise ValueError("no successful complete candidate run in bounded horizon")
    artifacts = []
    for page in range(1, 4):
        values = api(
            f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100&page={page}"
        )["artifacts"]
        artifacts.extend(values)
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
    for row in artifacts:
        if row["name"] in required:
            if row["name"] in selected or row["expired"] or row["size_in_bytes"] > 32 * 1024 * 1024:
                raise ValueError("candidate artifact ambiguous/expired/oversized")
            selected[row["name"]] = row
    if set(selected) != required:
        raise ValueError("candidate eleven-child population unavailable")

    def download(name):
        return api(f"repos/{repository}/actions/artifacts/{selected[name]['id']}/zip", binary=True)

    blob = download("ci-inventory")
    inventory = member(blob, "inventory.json")
    assignment = member(blob, "assignment.json")
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
        name.removeprefix("ci-").removesuffix("-test-evidence"): member(
            download(name), "shard-observation.json"
        )
        for name in required
        if name != "ci-inventory"
    }
    # Strict inner protocol validation shares the real verifier; only the
    # explicit observer mode relaxes CURRENT checkout equality for historical
    # evidence, while every source/attempt/population binding remains checked.
    reconcile(inventory, assignment, receipts, root=ROOT, current=False)
    tracers = {tuple(receipt.get("actual_tracers", [])) for receipt in receipts.values()}
    if len(tracers) != 1 or next(iter(tracers)) not in {("CTracer",), ("SysMonitor",)}:
        raise ValueError("candidate tracing species incomplete or mixed")
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
    output.mkdir(parents=True, exist_ok=True)
    (output / "candidate.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    (output / "receipt.json").write_text(
        json.dumps(
            {
                "state": "CANDIDATE",
                "run": str(run["id"]),
                "attempt": str(run["run_attempt"]),
                "head": run["head_sha"],
            },
            sort_keys=True,
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        candidate(repository=os.environ["GITHUB_REPOSITORY"], output=args.output)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ):
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "receipt.json").write_text(
            '{"state":"UNKNOWN","reason":"complete bounded evidence unavailable"}\n'
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

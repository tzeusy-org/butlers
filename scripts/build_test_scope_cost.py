#!/usr/bin/env python3
"""Build an uninstalled cost candidate from independently authenticated artifacts.

This stdlib consumer checks source/schema compatibility. Authentication of the
referenced GitHub artifacts and installation require independent source review;
this command never writes the authoritative profile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from butlers.testing import scope_cost  # noqa: E402

HEX = re.compile(r"[0-9a-f]{64}")


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate measurement field")
        result[key] = value
    return result


def load(path):
    raw = path.read_bytes()
    body = json.loads(raw, object_pairs_hook=unique)
    if type(body) is not dict:
        raise ValueError("measurement must be an object")
    return body, hashlib.sha256(raw).hexdigest()


def candidate(full, affected, heavy, *, root=ROOT):
    """No filename, count or caller species label certifies actual execution."""
    artifacts = [load(path) for path in (full, affected, heavy)]
    references, samples, contexts, overheads = [], {}, [], []
    for index, species in enumerate(("full", "affected")):
        body, artifact_hash = artifacts[index]
        if (
            body.get("schema") != "scope-measurement.v1"
            or body.get("species") != species
            or body.get("verified") is not True
            or body.get("complete") is not True
            or body.get("configuration") != scope_cost.environment(root)["configuration"]
            or not scope_cost.compatible_context(root, body.get("context"))
            or type(body.get("files")) is not dict
            or not body["files"]
            or not all(
                type(body.get(key)) is str and HEX.fullmatch(body[key])
                for key in (
                    "complete_reference_digest",
                    "selected_reference_digest",
                    "execution_digest",
                )
            )
        ):
            raise ValueError("complete current measurement proof unavailable")
        contexts.append(body["context"])
        scope_cost.finite(body["elapsed_seconds"])
        overheads.append(scope_cost.finite(body["outside_phase_union_seconds"]))
        reference = {key: body[key] for key in ("head", "run", "attempt")}
        if (
            type(reference["run"]) is not int
            or reference["run"] < 1
            or type(reference["attempt"]) is not int
            or reference["attempt"] < 1
            or type(reference["head"]) is not str
            or not re.fullmatch(r"[0-9a-f]{40}", reference["head"])
        ):
            raise ValueError("measurement source identity unavailable")
        references.append({**reference, "artifact_sha256": artifact_hash, "species": species})
        for path, sample in body["files"].items():
            if (
                not scope_cost.canonical_file(path)
                or type(sample) is not dict
                or sample.get("sha256") != scope_cost.file_digest(root / path)
            ):
                raise ValueError("measurement file source mismatch")
            duration = scope_cost.finite(sample["serial_seconds"])
            if index and path not in samples:
                raise ValueError("affected file is absent from full reference")
            if index and samples[path]["sha256"] != sample["sha256"]:
                raise ValueError("full and affected file source mismatch")
            previous = samples.get(path, {})
            samples[path] = {
                "sha256": sample["sha256"],
                "serial_seconds": max(duration, previous.get("serial_seconds", 0)),
                "references": previous.get("references", []) + [index],
            }
    if contexts[0] != contexts[1]:
        raise ValueError("measurement environments differ")
    heavy_body, heavy_hash = artifacts[2]
    if (
        heavy_body.get("schema") != "ci-lanes-heavy-timings.v1"
        or heavy_body.get("complete") is not True
        or heavy_body.get("selector") != "ci_lanes.py/pytest-split/5+6"
        or not isinstance(heavy_body.get("samples"), list)
        or not heavy_body["samples"]
    ):
        raise ValueError("authenticated current heavy timing population unavailable")
    jobs = heavy_body.get("measurement_jobs")
    if type(jobs) is not dict or set(jobs) != {"full", "affected"}:
        raise ValueError("authenticated complete measurement job elapsed required")
    for index, species in enumerate(("full", "affected")):
        job = jobs[species]
        if (
            type(job) is not dict
            or any(
                job.get(key) != references[index][key]
                for key in ("head", "run", "attempt", "artifact_sha256")
            )
            or job.get("conclusion") != "success"
        ):
            raise ValueError("measurement job provenance mismatch")
        elapsed = scope_cost.finite(job["elapsed_seconds"])
        observation_elapsed = scope_cost.finite(artifacts[index][0]["elapsed_seconds"])
        if elapsed < observation_elapsed:
            raise ValueError("job elapsed cannot omit setup or finalization")
        overheads[index] += elapsed - observation_elapsed
    durations = []
    groups = {}
    for sample in heavy_body["samples"]:
        if (
            type(sample) is not dict
            or type(sample.get("run")) is not int
            or sample["run"] < 1
            or type(sample.get("attempt")) is not int
            or sample["attempt"] < 1
            or type(sample.get("job")) is not int
            or sample["job"] < 1
            or sample.get("lane") not in {"unit", "integration"}
            or type(sample.get("group")) is not int
            or not 1 <= sample["group"] <= (5 if sample["lane"] == "unit" else 6)
            or sample.get("conclusion") != "success"
        ):
            raise ValueError("heavy timing identity unavailable")
        key = (sample["run"], sample["attempt"])
        group = (sample["lane"], sample["group"])
        if group in groups.setdefault(key, set()):
            raise ValueError("duplicate heavy timing group")
        groups[key].add(group)
        durations.append(scope_cost.finite(sample["elapsed_seconds"]))
    expected = {
        (lane, group)
        for lane, count in (("unit", 5), ("integration", 6))
        for group in range(1, count + 1)
    }
    if any(value != expected for value in groups.values()):
        raise ValueError("complete current5+6 timing groups required")
    # Nearest-rank empirical p90, without interpolation or worker division.
    p90 = sorted(durations)[math.ceil(len(durations) * 0.9) - 1]
    if p90 <= 0:
        raise ValueError("positive heavy timing population required")
    return {
        "schema": "scope-cost.v1",
        "state": "measured",
        "context": contexts[0],
        "references": references,
        "files": samples,
        "setup_startup_finalization_seconds": max(overheads),
        "heavy_shard_p90_seconds": p90,
        "heavy_artifact_sha256": heavy_hash,
        "admission": "CANDIDATE ONLY: authenticate references and review before installation",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("full", "affected", "heavy", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() == (ROOT / scope_cost.PROFILE).resolve():
            raise ValueError("candidate builder cannot install authoritative profile")
        body = candidate(args.full, args.affected, args.heavy)
        args.output.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "candidate": True,
                    "files": len(body["files"]),
                    "installation": "independent review required",
                }
            )
        )
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print("cost measurement candidate refused", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

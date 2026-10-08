"""Finite measured-cost admission, independent of assignment's median defaults."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import subprocess
from pathlib import Path

PROFILE = "scripts/test-scope-cost-profile.json"


def environment(root: Path) -> dict:
    """Record actual runtime/hardware separately from source/configuration.

    An offline candidate builder may validate source on a different host, but
    must preserve the independently observed paired runtime instead of stamping
    its own hardware onto those measurements.
    """
    names = {
        "pyproject.toml",
        "uv.lock",
        "conftest.py",
        ".github/workflows/ci.yml",
        ".github/workflows/migration-chain-main.yml",
        "scripts/test-resource-readers.json",
        "scripts/test-resource-reader-declarations.json",
    }
    for directory in ("src", "scripts", "alembic"):
        names.update(str(p.relative_to(root)) for p in (root / directory).rglob("*.py"))
    for directory in ("tests", "roster"):
        names.update(str(p.relative_to(root)) for p in (root / directory).rglob("conftest.py"))
    values = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        if (root / name).is_file()
        else None
        for name in sorted(names)
    }
    model = Path("/proc/cpuinfo")
    models = (
        sorted(
            {
                line.split(":", 1)[1].strip()
                for line in model.read_text().splitlines()
                if line.startswith("model name")
            }
        )
        if model.is_file()
        else []
    )
    runtime = {
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "cpu_affinity": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "cpu_model_digest": hashlib.sha256(json.dumps(models).encode()).hexdigest()
        if models
        else None,
        "runner_environment": os.environ.get("RUNNER_ENVIRONMENT", "local"),
        "runner_label": os.environ.get("CI_COST_RUNNER_LABEL"),
        "runner_image_os": os.environ.get("ImageOS"),
        "runner_image_version": os.environ.get("ImageVersion"),
        "expected_workers": os.environ.get("CI_COST_EXPECTED_WORKERS"),
        "worker_override": os.environ.get("PYTEST_XDIST_AUTO_WORKERS"),
        "coverage_policy": os.environ.get(
            "CI_COVERAGE", os.environ.get("CI_COST_COVERAGE_POLICY", "1")
        ),
        "coverage_core": os.environ.get("CI_COVERAGE_CORE", "ctrace"),
    }
    return {
        "runtime": runtime,
        "configuration": hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest(),
    }


def context(root: Path) -> str:
    return hashlib.sha256(json.dumps(environment(root), sort_keys=True).encode()).hexdigest()


def runner_class(observed: dict) -> dict:
    """Explicit observed hosted class, retaining CPU models in a separate ledger.

    This is a bounded empirical envelope across *observed* models, not a claim
    that those CPUs are identical or that an unseen model has measured costs.
    The offline builder validates this observed identity, never its local host.
    """
    runtime = observed.get("runtime", {})
    expected = {
        "python",
        "system",
        "machine",
        "logical_cpus",
        "cpu_affinity",
        "cpu_model_digest",
        "runner_environment",
        "runner_label",
        "runner_image_os",
        "runner_image_version",
        "expected_workers",
        "worker_override",
        "coverage_policy",
        "coverage_core",
    }
    if set(runtime) != expected or not re.fullmatch(
        r"[0-9a-f]{64}", str(observed.get("configuration"))
    ):
        raise ValueError("cost runtime/configuration incompatible")
    if (
        runtime["runner_environment"] != "github-hosted"
        or runtime["runner_label"] != "ubuntu-latest"
        or not re.fullmatch(r"ubuntu\d+", str(runtime["runner_image_os"]))
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", str(runtime["runner_image_version"]))
        or runtime["system"] != "Linux"
        or runtime["machine"] != "x86_64"
        or type(runtime["logical_cpus"]) is not int
        or type(runtime["cpu_affinity"]) is not int
        or not 0 < runtime["cpu_affinity"] <= runtime["logical_cpus"]
        or runtime["coverage_policy"] not in {"0", "1"}
        or runtime["coverage_core"] not in {"ctrace", "sysmon"}
        or not isinstance(runtime["expected_workers"], str)
        or not runtime["expected_workers"].isdecimal()
        or not 0 < int(runtime["expected_workers"]) <= 32
        or (
            runtime["worker_override"] is not None
            and runtime["worker_override"] != runtime["expected_workers"]
        )
        or not re.fullmatch(r"\d+\.\d+\.\d+", str(runtime["python"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(runtime["cpu_model_digest"]))
    ):
        raise ValueError("cost runtime/configuration incompatible")
    return {
        "configuration": observed["configuration"],
        "runtime": {key: value for key, value in runtime.items() if key != "cpu_model_digest"},
        "hardware_policy": "observed-model-empirical-max.v1",
    }


def measurement_species(receipt: dict) -> str:
    """Missing worker diagnostics never mean coverage-disabled or tracer None."""
    runtime = receipt["cost_environment"]["runtime"]
    runner_class(receipt["cost_environment"])
    workers = receipt.get("effective_workers")
    count = int(runtime["expected_workers"])
    expected = [f"gw{index}" for index in range(count)]
    resources = receipt.get("worker_resources")
    if (
        receipt.get("worker_diagnostics_consistent") is not True
        or workers != sorted(expected)
        or not isinstance(resources, dict)
        or set(resources) != set(expected)
    ):
        raise ValueError("cost worker diagnostics incomplete")
    species = set()
    for row in resources.values():
        if (
            not isinstance(row, dict)
            or not {"tracer", "coverage_enabled", "collector_present"} <= row.keys()
        ):
            raise ValueError("cost worker diagnostics incomplete")
        if runtime["coverage_policy"] == "0":
            if (
                row["tracer"] is not None
                or row["coverage_enabled"] is not False
                or row["collector_present"] is not False
            ):
                raise ValueError("cost instrumentation incompatible")
            species.add("untraced")
        else:
            expected_tracer = "CTracer" if runtime["coverage_core"] == "ctrace" else "SysMonitor"
            if (
                row["tracer"] != expected_tracer
                or row["coverage_enabled"] is not True
                or row["collector_present"] is not True
            ):
                raise ValueError("cost instrumentation incompatible")
            species.add(expected_tracer)
    result = species.pop()
    if receipt.get("actual_tracers") != ([] if result == "untraced" else [result]):
        raise ValueError("cost instrumentation incompatible")
    return result


def whole_file_cost(observations: list[dict]) -> float:
    """Sum disjoint lanes per full run, then max complete whole-file samples.

    A file may contain both unit and integration items. Maxing just those
    partial lane costs would silently under-estimate an affected whole-file run.
    Every constituent job/model remains in the provenance list.
    """
    groups: dict[tuple, list[float]] = {}
    seen = set()
    for sample in observations:
        measurement = sample["measurement"]
        job = measurement["job"]
        if job != "affected" and not re.fullmatch(r"(?:unit-[1-5]|integration-[1-6])", job):
            raise ValueError("cost file contribution incompatible")
        identity = (measurement["run"], measurement["attempt"], job)
        if identity in seen:
            raise ValueError("cost file contribution duplicated")
        seen.add(identity)
        key = (
            measurement["run"],
            measurement["attempt"],
            "affected" if job == "affected" else "heavy",
        )
        groups.setdefault(key, []).append(sample["seconds"])
    try:
        result = max(math.fsum(values) for values in groups.values())
    except OverflowError as error:
        raise ValueError("cost file contribution non-finite") from error
    if not math.isfinite(result):
        raise ValueError("cost file contribution non-finite")
    return result


def compatible_profile(profile: dict, observed: dict) -> bool:
    """Validate the explicit class and ledger before using any predicted cost."""
    classification = runner_class(observed)
    if profile.get("runner_class") != classification:
        return False
    if (
        profile.get("context")
        != hashlib.sha256(json.dumps(classification, sort_keys=True).encode()).hexdigest()
    ):
        return False
    ledger = profile.get("hardware_ledger")
    if (
        not isinstance(ledger, dict)
        or not ledger
        or observed["runtime"]["cpu_model_digest"] not in ledger
    ):
        return False
    species = (
        "untraced"
        if observed["runtime"]["coverage_policy"] == "0"
        else ("CTracer" if observed["runtime"]["coverage_core"] == "ctrace" else "SysMonitor")
    )
    if profile["reference"].get("tracer") != species:
        return False
    for model, observations in ledger.items():
        if (
            not re.fullmatch(r"[0-9a-f]{64}", model)
            or not isinstance(observations, list)
            or not observations
        ):
            return False
        for row in observations:
            if (
                runner_class(row["environment"]) != classification
                or row["environment"]["runtime"]["cpu_model_digest"] != model
            ):
                return False
            if (
                not str(row["run"]).isdecimal()
                or not str(row["attempt"]).isdecimal()
                or not re.fullmatch(r"[0-9a-f]{40}", row["source"])
            ):
                return False
            if (
                type(row["workers"]) is not int
                or row["workers"] != int(observed["runtime"]["expected_workers"])
                or row["instrumentation"] != species
            ):
                return False
    for row in profile["files"].values():
        samples = row.get("observations")
        if not isinstance(samples, list) or not samples:
            return False
        for sample in samples:
            if (
                sample["model"] not in ledger
                or sample["measurement"] not in ledger[sample["model"]]
            ):
                return False
            number = sample["seconds"]
            if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
                return False
        if row["seconds"] != whole_file_cost(samples):
            return False
    return True


def predict(root: Path, paths: list[str]) -> dict:
    """Missing/incompatible measurements widen; never guess an upper bound."""
    unknown = {
        "prediction_state": "unknown",
        "predicted_seconds": None,
        "ceiling_seconds": None,
        "cost_profile_digest": None,
        "reason": "COST_UNKNOWN",
    }
    try:
        raw = (root / PROFILE).read_bytes()
        profile = json.loads(raw)
        if profile.get("schema") == "test-scope-cost.v2":
            if not compatible_profile(profile, environment(root)):
                return unknown
        elif profile.get("schema") != "test-scope-cost.v1" or profile.get("context") != context(
            root
        ):
            return unknown
        source = profile["source_head"]
        if not isinstance(source, str) or not re.fullmatch(r"[a-f0-9]{40}", source):
            return unknown
        found = subprocess.run(
            ["git", "cat-file", "-e", source + "^{commit}"],
            cwd=root,
            capture_output=True,
            timeout=10,
        )
        if found.returncode:
            return unknown
        reference = profile["reference"]
        species = (
            {"CTracer", "SysMonitor", "untraced"}
            if profile["schema"] == "test-scope-cost.v2"
            else {"CTracer", "SysMonitor"}
        )
        if reference["workers"] != "auto" or reference["tracer"] not in species:
            return unknown
        samples = reference["heavy_shard_seconds"]
        if not isinstance(samples, list) or not samples:
            return unknown
        setup = reference["affected_setup_seconds"]
        numbers = [setup, *samples, *(row["seconds"] for row in profile["files"].values())]
        if any(type(n) not in (float, int) or not math.isfinite(n) or n < 0 for n in numbers):
            return unknown
        if not profile["files"] or not reference["runs"] or len(reference["runs"]) != len(samples):
            return unknown
        if len(set(reference["runs"])) != len(reference["runs"]):
            return unknown
        # Reference setup includes startup/finalization, never divided by workers.
        p90 = sorted(samples)[math.ceil(len(samples) * 0.9) - 1]
        ceiling = min(300.0, p90)
        if ceiling <= 0:
            return unknown
        selected = set()
        for path in paths:
            candidate = root / path
            if candidate.is_file():
                selected.add(path)
            elif candidate.is_dir():
                selected.update(str(p.relative_to(root)) for p in candidate.rglob("test_*.py"))
            else:
                return unknown
        if not selected:
            return unknown
        total = float(setup)
        provisional = []
        maximum = max(row["seconds"] for row in profile["files"].values())
        for path in sorted(selected):
            row = profile["files"].get(path)
            if row is None:
                # A genuinely new file may use a labelled observed maximum;
                # a changed measured file is incompatible, not freshly measured.
                previous = subprocess.run(
                    ["git", "cat-file", "-e", f"{source}:{path}"],
                    cwd=root,
                    capture_output=True,
                    timeout=10,
                )
                if previous.returncode == 0:
                    return unknown
                provisional.append(path)
                total += maximum
            else:
                if row["sha256"] != hashlib.sha256((root / path).read_bytes()).hexdigest():
                    return unknown
                total += row["seconds"]
        if not math.isfinite(total):
            return unknown
        return {
            "prediction_state": "provisional-new-file" if provisional else "measured-compatible",
            "predicted_seconds": total,
            "ceiling_seconds": ceiling,
            "cost_profile_digest": hashlib.sha256(raw).hexdigest(),
            "provisional_files": provisional,
            "reason": "COST_EXCEEDED" if total > ceiling else None,
        }
    except (OSError, KeyError, ValueError, TypeError, subprocess.SubprocessError):
        return unknown

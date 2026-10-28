"""Finite empirical affected-cost admission; advisory shard weights are unrelated.

A checked-in UNKNOWN profile is the safe initial state. Candidate generation
does not authenticate hosted artifacts or install a profile: independent review
must bind the actual observations before the source-owned profile can change.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import platform
import re
import sys
from pathlib import Path, PurePosixPath

PROFILE = "scripts/test-scope-cost-profile.json"
CONFIG = (
    "pyproject.toml",
    "uv.lock",
    "conftest.py",
    ".github/workflows/ci.yml",
    "scripts/ci_affected.py",
    "scripts/ci_affected_collector.py",
    "scripts/ci_shard_observer.py",
    "scripts/ci_evidence.py",
    "scripts/ci_lanes.py",
    "scripts/ci_scope_measurement.py",
    "src/butlers/testing/scope_cost.py",
    "src/butlers/testing/scoped_runner.py",
    ".test_durations",
    ".github/actions/ci-python-environment/action.yml",
)
CONTEXT_FIELDS = {
    "python",
    "system",
    "machine",
    "implementation",
    "configuration",
    "runner_policy",
    "image",
    "cpu_models",
    "cpu_count",
    "affinity",
    "workers",
    "instrumentation",
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cost evidence key")
        result[key] = value
    return result


def compatible_context(root: Path, context: object) -> bool:
    if type(context) is not dict or set(context) != CONTEXT_FIELDS:
        return False
    if any(
        type(context[k]) is not str or not context[k]
        for k in (
            "python",
            "system",
            "machine",
            "implementation",
            "runner_policy",
            "image",
            "instrumentation",
        )
    ):
        return False
    if any(type(context[k]) is not int or context[k] < 1 for k in ("workers", "cpu_count")):
        return False
    if (
        context["instrumentation"] not in {"untraced", "CTracer", "SysMonitor"}
        or type(context["cpu_models"]) is not list
        or not context["cpu_models"]
        or any(type(model) is not str or not model for model in context["cpu_models"])
        or type(context["affinity"]) is not list
        or not context["affinity"]
        or any(type(cpu) is not int or cpu < 0 for cpu in context["affinity"])
        or len(set(context["affinity"])) != len(context["affinity"])
    ):
        return False
    return all(context[k] == value for k, value in environment(root).items())


def canonical_file(path: object) -> bool:
    return (
        type(path) is str
        and path.startswith(("tests/", "roster/"))
        and path.endswith(".py")
        and "\\" not in path
        and "::" not in path
        and not any(ord(c) < 32 for c in path)
        and not any(part in {"", ".", ".."} for part in path.split("/"))
    )


def file_digest(path: Path) -> str:
    if (
        path.is_symlink()
        or any(parent.is_symlink() for parent in path.parents)
        or not path.is_file()
    ):
        raise ValueError("cost source unavailable")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value: object) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError("cost duration unavailable")
    return float(value)


def environment(root: Path) -> dict:
    """Public local context only; never guess a hosted runner or tracer class."""
    return {
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "configuration": {p: file_digest(root / p) for p in CONFIG},
        "implementation": sys.implementation.name,
    }


def current_resources(root: Path) -> dict:
    """Actual public process/runner resources; no future-worker observation."""
    image = os.environ.get("ImageOS", "") + "/" + os.environ.get("ImageVersion", "")
    if image.startswith("/") or image.endswith("/") or os.environ.get("RUNNER_OS") != "Linux":
        raise ValueError("actual hosted image unavailable")
    models = sorted(
        {
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name") and ":" in line
        }
    )
    affinity = sorted(os.sched_getaffinity(0))
    if not models or not affinity or not os.cpu_count():
        raise ValueError("actual CPU observation unavailable")
    return {
        **environment(root),
        "runner_policy": "ubuntu-latest",
        "image": image,
        "cpu_models": models,
        "cpu_count": os.cpu_count(),
        "affinity": affinity,
    }


def planning_observation(root: Path) -> dict:
    """Keep observed resources separate from source-pinned planned execution.

    This is a prediction contract, never evidence that future workers ran. The
    affected entrypoint must recheck actual receipt workers/tracers afterwards.
    """
    if any(
        os.environ.get(name)
        for name in (
            "PYTEST_ADDOPTS",
            "PYTEST_XDIST_AUTO_WORKERS",
            "COVERAGE_PROCESS_START",
            "PYTHONTRACEMALLOC",
        )
    ):
        raise ValueError("execution override is not a compatible prediction")
    tree = ast.parse((root / "conftest.py").read_bytes())
    declarations = [
        n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_DEFAULT_XDIST_AUTO_WORKERS" for t in n.targets
        )
        and isinstance(n.value, ast.Constant)
    ]
    if declarations != [3] or type(declarations[0]) is not int:
        raise ValueError("source-pinned affected worker contract unavailable")
    return {
        "actual_resources": current_resources(root),
        "planned_execution": {"workers": 3, "instrumentation": "untraced"},
        "kind": "resources-observed/execution-planned",
    }


def actual_execution_context(root: Path, receipt: dict) -> dict:
    workers, resources = receipt.get("effective_workers"), receipt.get("worker_resources")
    if (
        type(workers) is not list
        or not workers
        or any(type(worker) is not str or not worker for worker in workers)
        or len(workers) != len(set(workers))
        or type(resources) is not dict
        or set(resources) != set(workers)
        or any(type(resources[w]) is not dict or "tracer" not in resources[w] for w in workers)
    ):
        raise ValueError("actual workers or instrumentation unavailable")
    for runtime in resources.values():
        if (
            set(runtime) != {"cpu_s", "max_rss", "rss_units", "tracer"}
            or runtime["rss_units"] != "KiB on Linux; bytes on macOS"
        ):
            raise ValueError("actual worker resource representation unavailable")
        finite(runtime["cpu_s"])
        finite(runtime["max_rss"])
    tracers = {resources[w]["tracer"] for w in workers}
    if tracers == {None} and receipt.get("actual_tracers") == []:
        tracer = "untraced"
    elif len(tracers) == 1 and next(iter(tracers)) in {"CTracer", "SysMonitor"}:
        tracer = next(iter(tracers))
        if receipt.get("actual_tracers") != [tracer]:
            raise ValueError("actual instrumentation disagrees")
    else:
        raise ValueError("actual instrumentation is incompatible")
    return {**current_resources(root), "workers": len(workers), "instrumentation": tracer}


def predict(
    root: Path,
    paths: list[str],
    *,
    observed: dict | None = None,
    new_files: tuple[str, ...] = (),
    planning: bool = False,
) -> dict:
    """Sum serial whole-file costs plus actual overhead; never divide by workers.

    `observed` is emitted by the owning measurement/execution producer, not a
    label inferred from the machine used to build the candidate. Missing worker,
    CPU or instrumentation observation refuses even if Python happens to match.
    """
    unknown = {
        "state": "unknown",
        "reason": "COST_UNKNOWN",
        "predicted_seconds": None,
        "ceiling_seconds": None,
        "profile_digest": None,
        "provisional_files": [],
    }
    try:
        raw = (root / PROFILE).read_bytes()
        profile = json.loads(raw, object_pairs_hook=unique_object)
        unknown["profile_digest"] = hashlib.sha256(raw).hexdigest()
        if (
            not isinstance(profile, dict)
            or profile.get("schema") != "scope-cost.v1"
            or profile.get("state") != "measured"
        ):
            return unknown
        context = profile["context"]
        provenance = {"kind": "execution-observed", "context": observed}
        if observed is None and planning:
            provenance = planning_observation(root)
            observed = {**provenance["actual_resources"], **provenance["planned_execution"]}
        if (
            not isinstance(observed, dict)
            or observed != context
            or not compatible_context(root, context)
        ):
            return unknown
        references = profile["references"]
        if not isinstance(references, list) or not references:
            return unknown
        for ref in references:
            if (
                set(ref) != {"head", "run", "attempt", "artifact_sha256", "species"}
                or type(ref["run"]) is not int
                or ref["run"] < 1
                or type(ref["attempt"]) is not int
                or ref["attempt"] < 1
                or ref["species"] not in {"full", "affected"}
                or not isinstance(ref["head"], str)
                or not re.fullmatch(r"[0-9a-f]{40}", ref["head"])
                or not isinstance(ref["artifact_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", ref["artifact_sha256"])
            ):
                return unknown
        if {r["species"] for r in references} != {"full", "affected"}:
            return unknown
        samples = profile["files"]
        if not isinstance(samples, dict) or not samples:
            return unknown
        maxima = []
        for path, sample in samples.items():
            if (
                not canonical_file(path)
                or not isinstance(sample, dict)
                or not isinstance(sample["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", sample["sha256"])
                or not sample["references"]
                or any(
                    type(i) is not int or not 0 <= i < len(references) for i in sample["references"]
                )
            ):
                return unknown
            maxima.append(finite(sample["serial_seconds"]))
        overhead = finite(profile["setup_startup_finalization_seconds"])
        p90 = finite(profile["heavy_shard_p90_seconds"])
        if p90 <= 0:
            return unknown
        predicted, provisional = overhead, []
        for path in sorted(set(paths)):
            parsed = PurePosixPath(path)
            if (
                parsed.is_absolute()
                or parsed.as_posix() != path
                or ".." in parsed.parts
                or "\\" in path
                or "::" in path
                or not path.endswith(".py")
                or not path.startswith(("tests/", "roster/"))
            ):
                return unknown
            if path in samples:
                if file_digest(root / path) != samples[path]["sha256"]:
                    return unknown
                predicted += finite(samples[path]["serial_seconds"])
            elif path in new_files and (root / path).is_file():
                predicted += max(maxima)
                provisional.append(path)
            else:
                return unknown
        predicted = finite(predicted)
        ceiling = min(300.0, p90)
        return {
            "state": "provisional" if provisional else "measured",
            "reason": "COST_EXCEEDED" if predicted > ceiling else None,
            "predicted_seconds": predicted,
            "ceiling_seconds": ceiling,
            "profile_digest": unknown["profile_digest"],
            "provisional_files": provisional,
            "context_provenance": provenance,
        }
    except (KeyError, TypeError, ValueError, OSError, OverflowError, AttributeError, SyntaxError):
        return unknown

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
        "cpu_model_digest": hashlib.sha256(json.dumps(models).encode()).hexdigest(),
        "runner_environment": os.environ.get("RUNNER_ENVIRONMENT", "local"),
    }
    return {
        "runtime": runtime,
        "configuration": hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest(),
    }


def context(root: Path) -> str:
    return hashlib.sha256(json.dumps(environment(root), sort_keys=True).encode()).hexdigest()


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
        if profile.get("schema") != "test-scope-cost.v1" or profile.get("context") != context(root):
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
        if reference["workers"] != "auto" or reference["tracer"] not in {"CTracer", "SysMonitor"}:
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

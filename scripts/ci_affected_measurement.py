"""Observe the existing affected command; no selection or measurement authority."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from butlers.testing.resource_readers import safe_path  # noqa: E402
from butlers.testing.scope_cost import context  # noqa: E402


def main() -> int:
    directory = Path(os.environ["TEST_EVIDENCE_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    paths = json.loads(os.environ["TEST_PATHS_JSON"])
    if (
        not isinstance(paths, list)
        or not paths
        or any(not safe_path(p) or not p.startswith(("tests/", "roster/")) for p in paths)
    ):
        raise ValueError("affected selection unavailable")
    files = sorted(
        {
            str(p.relative_to(ROOT))
            for name in paths
            for p in (
                [ROOT / name] if (ROOT / name).is_file() else (ROOT / name).rglob("test_*.py")
            )
        }
    )
    if not files:
        raise ValueError("affected selection empty")
    head = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, timeout=10).decode().strip()
    )
    command = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "pytest",
        *paths,
        "-q",
        "--tb=short",
        "-n",
        "auto",
        "--dist",
        "loadfile",
        "--cov=src/butlers",
        "-p",
        "scripts.ci_shard_observer",
        "--junitxml=" + str(directory / "raw-junit.xml"),
    ]
    env = os.environ.copy()
    env.update(
        CI_SHARD_CONTEXT=json.dumps(
            {
                "kind": "affected-cost.v1",
                "files": files,
                "nonce": secrets.token_hex(16),
                "source_head": head,
                "cost_context": context(ROOT),
                "worker_policy": "auto",
                "run": os.environ.get("GITHUB_RUN_ID"),
                "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                "file_hashes": {
                    p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files
                },
                "command": command,
            }
        ),
        CI_SHARD_STARTED=str(time.monotonic()),
        CI_SHARD_RECEIPT=str(directory / "affected-observation.json"),
    )
    # GNU timeout owns the process group and its descendants. An outer bound
    # also reaps the whole group if timeout itself becomes unavailable.
    proc = subprocess.Popen(
        ["timeout", "--signal=TERM", "--kill-after=10", "300", *command],
        cwd=ROOT,
        env=env,
        start_new_session=True,
    )

    def interrupted(_signal, _frame):
        raise InterruptedError("affected command interrupted")

    previous = {
        value: signal.signal(value, interrupted) for value in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        return proc.wait(timeout=315)
    finally:
        for value, handler in previous.items():
            signal.signal(value, handler)
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())

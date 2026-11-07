"""Shared fixtures and helpers for adapter integration tests.

Provides:
- ``run_cli()``: Invoke a CLI binary and return (stdout, stderr, returncode)
- ``parse_jsonl_events()``: Parse JSON-lines output into event dicts
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def gemini_settings_staging_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stage Gemini settings under the test's tmp_path; yields the real staging function.

    The real staging root must be root-owned (gemini-cli skips system settings
    otherwise), which an unprivileged test run cannot provide.
    """
    from butlers.core.runtimes import gemini

    real = gemini._settings_staging_root
    monkeypatch.setattr(gemini, "_settings_staging_root", lambda: tmp_path)
    yield real


def run_cli(
    binary: str,
    args: list[str],
    prompt: str,
    timeout: int = 120,
    cwd: str = "/tmp",
) -> tuple[str, str, int]:
    """Run a CLI binary with args and prompt, returning (stdout, stderr, returncode).

    Parameters
    ----------
    binary:
        CLI binary name or path (e.g. "codex", "opencode").
    args:
        CLI arguments between the binary and the prompt (e.g. ["exec", "--json"]).
    prompt:
        The prompt string, appended as the final positional argument.
    timeout:
        Maximum seconds to wait for the process.
    cwd:
        Working directory for the subprocess.
    """
    result = subprocess.run(
        [binary, *args, prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    return result.stdout, result.stderr, result.returncode


def parse_jsonl_events(stdout: str) -> list[dict]:
    """Parse JSON-lines from CLI stdout into a list of event dicts.

    Non-JSON lines are silently skipped.
    """
    events: list[dict] = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except (json.JSONDecodeError, ValueError):
            pass
    return events

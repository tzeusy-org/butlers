"""Regression guard: the frontend-dev compose service must not rewrite the
tracked ``frontend/package-lock.json`` on startup (bu-0zvsd).

``frontend-dev`` bind-mounts the host ``./frontend`` directory, so whatever its
startup command does to the lockfile writes through to the tracked file. Running
``npm install`` there re-resolves and prunes the lockfile (observed churn: 12
insertions / 105 deletions on minimatch / brace-expansion / balanced-match),
leaving every launcher worktree dirty. Two invariants keep the lockfile the
single source of truth:

  1. the startup command uses ``npm ci`` (installs strictly FROM the lockfile,
     never rewrites it; fails loud on a lock/package.json mismatch) rather than
     ``npm install``; and
  2. the image pins Node 24 to match CI (``.github/workflows/ci.yml``
     ``node-version: "24"`` + its own ``npm ci`` step) -- Node 22's ``npm ci``
     rejected this Node-24-generated lockfile outright.

This is a cheap static tripwire (no Docker) so a future edit reverting either
invariant fails in the normal test suite instead of silently re-dirtying the
lockfile at every launch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit


def _frontend_dev_service() -> dict:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "frontend-dev" in services, "frontend-dev service missing from docker-compose.yml"
    return services["frontend-dev"]


def test_frontend_dev_uses_npm_ci_not_npm_install() -> None:
    command = _frontend_dev_service()["command"]
    assert isinstance(command, str)
    assert "npm ci" in command, (
        "frontend-dev startup must use `npm ci` (installs from the lockfile, never "
        "rewrites it). See bu-0zvsd."
    )
    assert "npm install" not in command, (
        "frontend-dev startup must NOT use `npm install` -- it re-resolves and "
        "rewrites the host-mounted, tracked frontend/package-lock.json, dirtying "
        "the launcher worktree. Use `npm ci`. See bu-0zvsd."
    )


def test_frontend_dev_node_pinned_to_ci_major() -> None:
    image = _frontend_dev_service()["image"]
    assert image.startswith("node:24"), (
        f"frontend-dev image must pin Node 24 to match CI (npm ci only succeeds "
        f"against the Node-24-generated lockfile); got {image!r}. See bu-0zvsd."
    )

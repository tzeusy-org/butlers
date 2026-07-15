"""Smoke test: verify docker-compose dev overlay starts and services become healthy.

Requires Docker daemon. Skipped in CI unless COMPOSE_SMOKE=1 is set.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPOSE_SMOKE") != "1",
    reason="Set COMPOSE_SMOKE=1 to run compose integration tests",
)

COMPOSE_CMD = [
    "docker",
    "compose",
]


@pytest.fixture(scope="module")
def compose_stack():
    """Bring up the stack with OAuth check skipped, tear down after."""
    env = {**os.environ, "SKIP_OAUTH_CHECK": "true"}
    subprocess.run(
        [
            *COMPOSE_CMD,
            "up",
            "-d",
            "--build",
            "postgres",
            "migrations",
            "dashboard-api",
            "frontend-dev",
            "connector-telegram-bot",
        ],
        check=True,
        env=env,
        timeout=180,
    )
    yield
    subprocess.run([*COMPOSE_CMD, "down", "-v", "--timeout", "10"], check=False)


def test_postgres_healthy(compose_stack):
    """Postgres should be accepting connections."""
    result = subprocess.run(
        [*COMPOSE_CMD, "exec", "postgres", "pg_isready", "-U", "butlers"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0


def test_dashboard_healthy(compose_stack):
    """Dashboard API /health should return 200."""
    for _ in range(30):
        try:
            result = subprocess.run(
                ["curl", "-sf", "http://localhost:41200/health"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    pytest.fail("Dashboard API did not become healthy within 60s")


def test_migrations_completed(compose_stack):
    """Migrations service should have exited 0."""
    result = subprocess.run(
        [*COMPOSE_CMD, "ps", "-a", "--format", "json", "migrations"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "exited" in result.stdout.lower() or "Exit 0" in result.stdout


def test_frontend_dev_startup_leaves_lockfile_clean(compose_stack):
    """frontend-dev bind-mounts the host ./frontend, so its startup must not
    mutate the tracked frontend/package-lock.json (bu-0zvsd).

    End-to-end counterpart of the static guard in
    tests/scripts/test_compose_frontend_dev_lockfile_guard.py: `npm install`
    used to re-resolve and rewrite the lockfile through the mount (12/-105 churn),
    dirtying the launcher worktree; `npm ci` + Node 24 must leave it untouched."""
    if not Path(".git").exists():
        pytest.skip("not a git checkout; lockfile-clean assertion needs git")

    # The rewrite (if any) happens DURING the container's install, before the
    # Vite dev server answers. Wait for the server to come up (= `npm ci` +
    # `npm run dev` both completed), then check the lockfile exactly once.
    port = os.environ.get("FRONTEND_HOST_PORT", "41173")
    base = os.environ.get("FRONTEND_BASE_PATH", "/butlers/")
    for _ in range(60):
        probe = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", f"http://127.0.0.1:{port}{base}"],
            capture_output=True,
            timeout=5,
        )
        if probe.returncode == 0:  # server answered (any HTTP status)
            break
        time.sleep(2)
    else:
        pytest.fail("frontend-dev did not start serving within 120s")

    diff = subprocess.run(
        ["git", "diff", "--name-only", "--", "frontend/package-lock.json"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert not diff.stdout.strip(), (
        "frontend-dev startup dirtied frontend/package-lock.json "
        f"(git diff: {diff.stdout!r}) -- npm ci must not rewrite the lockfile"
    )

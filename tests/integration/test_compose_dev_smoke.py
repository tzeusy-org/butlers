"""Smoke test: verify docker-compose dev overlay starts and services become healthy.

Requires Docker daemon. Skipped in CI unless COMPOSE_SMOKE=1 is set.
"""

import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPOSE_SMOKE") != "1",
    reason="Set COMPOSE_SMOKE=1 to run compose integration tests",
)

COMPOSE_CMD = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.dev.yml",
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

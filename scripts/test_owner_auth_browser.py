"""Run native HTTPS owner-auth evidence with a disposable PostgreSQL instance.

Usage: uv run --no-sync python scripts/test_owner_auth_browser.py
Requires built frontend/dist and installed Playwright Chromium. No production
lifespan, credentials, database, proxy or browser profile is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import asyncpg
from testcontainers.postgres import PostgresContainer

ROOT = Path(__file__).resolve().parents[1]


async def initialize(env: dict[str, str]) -> None:
    connection = await asyncpg.connect(
        host=env["POSTGRES_HOST"],
        port=int(env["POSTGRES_PORT"]),
        user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        database=env["POSTGRES_DB"],
        ssl=False,
    )
    try:
        await connection.execute(
            (ROOT / "alembic/versions/core/core_240_dashboard_owner_auth.sql").read_text()
        )
        await connection.execute("CREATE ROLE owner_auth_browser_runtime LOGIN NOINHERIT")
        # PostgreSQL identifiers are fixed; only this synthetic secret is quoted.
        password = env["DASHBOARD_AUTH_DB_PASSWORD"].replace("'", "''")
        await connection.execute(
            "ALTER ROLE owner_auth_browser_runtime PASSWORD '" + password + "'"
        )
        await connection.execute("GRANT dashboard_auth_api TO owner_auth_browser_runtime")
    finally:
        await connection.close()


def main() -> int:
    # Avoid forwarding ambient owner/provider credentials to synthetic children.
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "UV_CACHE_DIR", "PLAYWRIGHT_BROWSERS_PATH")
        if key in os.environ
    }
    password = secrets.token_urlsafe(32)
    with PostgresContainer(
        "postgres:16-alpine",
        username="owner_auth_test",
        password=password,
        dbname="test_owner_auth_browser",
    ) as database:
        env.update(
            {
                "POSTGRES_HOST": database.get_container_host_ip(),
                "POSTGRES_PORT": str(database.get_exposed_port(5432)),
                "DASHBOARD_AUTH_DB_USER": "owner_auth_browser_runtime",
                "DASHBOARD_AUTH_DB_PASSWORD": secrets.token_urlsafe(32),
                "POSTGRES_USER": "owner_auth_test",
                "POSTGRES_PASSWORD": password,
                "POSTGRES_DB": "test_owner_auth_browser",
                "POSTGRES_SSLMODE": "disable",
                "DASHBOARD_AUTH_ORIGIN": "https://butlers.example.test",
                "DASHBOARD_AUTH_RP_ID": "butlers.example.test",
                "DASHBOARD_AUTH_DEPLOYMENT": "browser-test",
                "DASHBOARD_AUTH_TRUSTED_PROXY_PEERS": "127.0.0.1",
                "PYTHONPATH": str(ROOT),
                "OWNER_AUTH_TEST_ISOLATED": "1",
            }
        )
        asyncio.run(initialize(env))
        host = [sys.executable, str(ROOT / "tests/api/owner_auth_browser_host.py")]
        subprocess.run(
            [*host, "reconcile-mode", "--confirm-revoke"],
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        env["OWNER_AUTH_TEST_API_URL"] = f"http://127.0.0.1:{port}"
        env["OWNER_AUTH_TEST_HOST_COMMAND"] = json.dumps(host)
        for scenario in ("native passkey", "configured key"):
            if scenario == "configured key":
                env["DASHBOARD_API_KEY"] = secrets.token_urlsafe(32)
                env["OWNER_AUTH_TEST_CONFIGURED_KEY"] = env["DASHBOARD_API_KEY"]
                subprocess.run(
                    [*host, "reconcile-mode", "--confirm-revoke"],
                    env=env,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
            with tempfile.TemporaryFile() as server_log:
                server = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "tests.api.owner_auth_browser_server:create_test_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-proxy-headers",
                        "--no-access-log",
                        "--log-level",
                        "error",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=server_log,
                    stderr=server_log,
                )
                try:
                    for _ in range(100):
                        if server.poll() is not None:
                            raise RuntimeError("Isolated auth server failed to start")
                        try:
                            with urllib.request.urlopen(
                                env["OWNER_AUTH_TEST_API_URL"] + "/api/health", timeout=0.2
                            ) as response:
                                if response.status == 200:
                                    break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        raise RuntimeError("Isolated auth server readiness timed out")
                    result = subprocess.run(
                        ["npm", "run", "test:owner-auth", "--", "--grep", scenario],
                        cwd=ROOT / "frontend",
                        env=env,
                        check=False,
                    )
                    if result.returncode:
                        return result.returncode
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

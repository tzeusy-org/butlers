"""Integration tests for scripts/one_db_data_migration.py."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def postgres_container():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as postgres:
        yield postgres


def _dsn(container, db_name: str) -> str:
    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    user = container.username
    password = container.password
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _create_db(container, db_name: str) -> str:
    async def _run() -> str:
        admin = await asyncpg.connect(_dsn(container, "postgres"))
        try:
            safe_name = db_name.replace('"', '""')
            await admin.execute(f'CREATE DATABASE "{safe_name}"')
        finally:
            await admin.close()
        return _dsn(container, db_name)

    return asyncio.run(_run())


def _new_db_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _run_cmd(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _script_path() -> Path:
    return Path(__file__).parents[2] / "scripts" / "one_db_data_migration.py"


async def _create_state_table(dsn: str, schema: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{schema}"."state" (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL DEFAULT '{{}}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
    finally:
        await conn.close()


async def _seed_state_row(dsn: str, schema: str, key: str, payload: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"""
            INSERT INTO "{schema}"."state" (key, value, version)
            VALUES ($1, $2::jsonb, 1)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value
            """,
            key,
            payload,
        )
    finally:
        await conn.close()


async def _count_rows(dsn: str, schema: str, table: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        value = await conn.fetchval(f'SELECT COUNT(*)::BIGINT FROM "{schema}"."{table}"')
        return int(value or 0)
    finally:
        await conn.close()


async def _create_core_and_shared_tables(dsn: str, schema: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{schema}"."state" (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL DEFAULT '{{}}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{schema}"."scheduled_tasks" (
                id UUID PRIMARY KEY,
                name TEXT NOT NULL,
                cron TEXT NOT NULL,
                prompt TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'db',
                enabled BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{schema}"."sessions" (
                id UUID PRIMARY KEY,
                prompt TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                tool_calls JSONB NOT NULL DEFAULT '[]',
                started_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{schema}"."route_inbox" (
                id UUID PRIMARY KEY,
                route_envelope JSONB NOT NULL,
                lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    finally:
        await conn.close()


async def _create_shared_table(dsn: str, schema: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{schema}"."butler_secrets" (
                secret_key TEXT PRIMARY KEY,
                secret_value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    finally:
        await conn.close()


async def _seed_core_rows(dsn: str, schema: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"""
            INSERT INTO "{schema}"."state" (key, value, version)
            VALUES ('feature_flags', '{{"enabled": true}}'::jsonb, 1)
            ON CONFLICT (key) DO NOTHING
            """
        )
        await conn.execute(
            f"""
            INSERT INTO "{schema}"."scheduled_tasks" (id, name, cron, prompt)
            VALUES (
                '11111111-1111-1111-1111-111111111111',
                'daily-digest',
                '0 9 * * *',
                'daily summary'
            )
            ON CONFLICT (id) DO NOTHING
            """
        )
        await conn.execute(
            f"""
            INSERT INTO "{schema}"."sessions" (id, prompt, trigger_source, tool_calls)
            VALUES (
                '22222222-2222-2222-2222-222222222222',
                'hello',
                'manual',
                '[]'::jsonb
            )
            ON CONFLICT (id) DO NOTHING
            """
        )
        await conn.execute(
            f"""
            INSERT INTO "{schema}"."route_inbox" (id, route_envelope, lifecycle_state)
            VALUES (
                '33333333-3333-3333-3333-333333333333',
                '{{"kind":"route"}}'::jsonb,
                'accepted'
            )
            ON CONFLICT (id) DO NOTHING
            """
        )
    finally:
        await conn.close()


async def _seed_shared_rows(dsn: str, schema: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"""
            INSERT INTO "{schema}"."butler_secrets" (secret_key, secret_value, category)
            VALUES ('google_client_id', 'abc123', 'google_oauth')
            ON CONFLICT (secret_key) DO NOTHING
            """
        )
    finally:
        await conn.close()


def test_migrate_dry_run_does_not_write_target(postgres_container) -> None:
    source_db = _create_db(postgres_container, _new_db_name("source_general"))
    target_db = _create_db(postgres_container, _new_db_name("target"))

    asyncio.run(_create_state_table(source_db, "public"))
    asyncio.run(_seed_state_row(source_db, "public", "dry-run-key", '{"dry_run": true}'))
    asyncio.run(_create_state_table(target_db, "general"))

    env = os.environ.copy()
    env["TEST_TARGET_DSN"] = target_db
    env["TEST_SOURCE_GENERAL_DSN"] = source_db

    result = _run_cmd(
        [
            sys.executable,
            str(_script_path()),
            "migrate",
            "--target-env",
            "TEST_TARGET_DSN",
            "--source-env",
            "general=TEST_SOURCE_GENERAL_DSN",
            "--core-table",
            "state",
            "--no-include-shared",
            "--dry-run",
        ],
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "DRY-RUN summary" in result.stdout
    assert asyncio.run(_count_rows(target_db, "general", "state")) == 0


def test_run_migrates_and_verifies_core_and_shared_tables(
    postgres_container,
    tmp_path: Path,
) -> None:
    source_general = _create_db(postgres_container, _new_db_name("source_general"))
    source_shared = _create_db(postgres_container, _new_db_name("source_shared"))
    target_db = _create_db(postgres_container, _new_db_name("target"))

    asyncio.run(_create_core_and_shared_tables(source_general, "public"))
    asyncio.run(_create_shared_table(source_shared, "public"))
    asyncio.run(_seed_core_rows(source_general, "public"))
    asyncio.run(_seed_shared_rows(source_shared, "public"))

    asyncio.run(_create_core_and_shared_tables(target_db, "general"))
    asyncio.run(_create_shared_table(target_db, "shared"))

    report_path = tmp_path / "migration-report.json"
    env = os.environ.copy()
    env["TEST_TARGET_DSN"] = target_db
    env["TEST_SOURCE_GENERAL_DSN"] = source_general
    env["TEST_SOURCE_SHARED_DSN"] = source_shared

    result = _run_cmd(
        [
            sys.executable,
            str(_script_path()),
            "run",
            "--target-env",
            "TEST_TARGET_DSN",
            "--source-env",
            "general=TEST_SOURCE_GENERAL_DSN",
            "--shared-source-env",
            "TEST_SOURCE_SHARED_DSN",
            "--report-path",
            str(report_path),
        ],
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "VERIFY summary" in result.stdout
    report = json.loads(report_path.read_text())
    assert report["status"] == "ok"
    assert report["summary"]["tables_failed"] == 0

    assert asyncio.run(_count_rows(target_db, "general", "state")) == 1
    assert asyncio.run(_count_rows(target_db, "general", "scheduled_tasks")) == 1
    assert asyncio.run(_count_rows(target_db, "general", "sessions")) == 1
    assert asyncio.run(_count_rows(target_db, "general", "route_inbox")) == 1
    assert asyncio.run(_count_rows(target_db, "shared", "butler_secrets")) == 1


def test_verify_fails_loudly_on_mismatch(postgres_container, tmp_path: Path) -> None:
    source_db = _create_db(postgres_container, _new_db_name("source_general"))
    target_db = _create_db(postgres_container, _new_db_name("target"))
    report_path = tmp_path / "verify-report.json"

    asyncio.run(_create_state_table(source_db, "public"))
    asyncio.run(_create_state_table(target_db, "general"))
    asyncio.run(_seed_state_row(source_db, "public", "sync_key", '{"value": 1}'))
    asyncio.run(_seed_state_row(target_db, "general", "sync_key", '{"value": 2}'))

    env = os.environ.copy()
    env["TEST_TARGET_DSN"] = target_db
    env["TEST_SOURCE_GENERAL_DSN"] = source_db

    result = _run_cmd(
        [
            sys.executable,
            str(_script_path()),
            "verify",
            "--target-env",
            "TEST_TARGET_DSN",
            "--source-env",
            "general=TEST_SOURCE_GENERAL_DSN",
            "--core-table",
            "state",
            "--no-include-shared",
            "--report-path",
            str(report_path),
        ],
        env=env,
    )

    assert result.returncode == 2
    assert "PARITY CHECK FAILED" in result.stderr
    report = json.loads(report_path.read_text())
    assert report["status"] == "error"
    assert any(entry.get("status") == "mismatch" for entry in report["results"])


def test_rollback_clears_target_tables(postgres_container) -> None:
    source_db = _create_db(postgres_container, _new_db_name("source_general"))
    target_db = _create_db(postgres_container, _new_db_name("target"))

    asyncio.run(_create_state_table(source_db, "public"))
    asyncio.run(_create_state_table(target_db, "general"))
    asyncio.run(_seed_state_row(source_db, "public", "rollback_key", '{"value": "x"}'))

    env = os.environ.copy()
    env["TEST_TARGET_DSN"] = target_db
    env["TEST_SOURCE_GENERAL_DSN"] = source_db

    migrate = _run_cmd(
        [
            sys.executable,
            str(_script_path()),
            "migrate",
            "--target-env",
            "TEST_TARGET_DSN",
            "--source-env",
            "general=TEST_SOURCE_GENERAL_DSN",
            "--core-table",
            "state",
            "--no-include-shared",
        ],
        env=env,
    )
    assert migrate.returncode == 0, migrate.stderr
    assert asyncio.run(_count_rows(target_db, "general", "state")) == 1

    rollback = _run_cmd(
        [
            sys.executable,
            str(_script_path()),
            "rollback",
            "--target-env",
            "TEST_TARGET_DSN",
            "--source-env",
            "general=TEST_SOURCE_GENERAL_DSN",
            "--core-table",
            "state",
            "--no-include-shared",
            "--confirm-rollback",
            "ROLLBACK",
        ],
        env=env,
    )
    assert rollback.returncode == 0, rollback.stderr
    assert asyncio.run(_count_rows(target_db, "general", "state")) == 0

"""Real-Postgres contract for the shared expected-signals ledger."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.core.expected_signals import upsert_expected_signal

pytestmark = pytest.mark.integration

_MIGRATION_PATHS = [
    Path(__file__).resolve().parents[2] / "alembic/versions/core/core_210_expected_signals.py",
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_211_expected_signal_endpoint_identity.py",
]


def _load_migration(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run_migration(pool, path: Path, direction: str) -> None:
    statements: list[str] = []
    module = _load_migration(path)
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with patch.object(module, "op", mocked_op):
        getattr(module, direction)()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.asyncio(loop_scope="session")
async def test_migration_upsert_concurrency_and_downgrade(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        for path in _MIGRATION_PATHS:
            await _run_migration(pool, path, "upgrade")
        await pool.execute(
            """
            CREATE TABLE public._expected_signal_test_liveness (
                connector_type text NOT NULL,
                endpoint_identity text NOT NULL,
                state text NOT NULL,
                last_heartbeat_at timestamptz
            )
            """
        )
        await pool.execute(
            """
            CREATE VIEW public.v_qa_connector_state AS
            SELECT connector_type, endpoint_identity, state, last_heartbeat_at
            FROM public._expected_signal_test_liveness
            """
        )
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        await pool.execute(
            "INSERT INTO public._expected_signal_test_liveness VALUES ($1, $2, 'healthy', $3)",
            "google_health",
            "google_health:user:owner",
            now,
        )

        async def write(observed_at: datetime) -> None:
            await upsert_expected_signal(
                pool,
                signal_key="health:measurement-gap:weight",
                producer="connector:google_health",
                producer_endpoint_identity="google_health:user:owner",
                expected_cadence=timedelta(days=14),
                last_observed_at=observed_at,
                now=now,
            )

        await asyncio.gather(write(now), write(now - timedelta(days=20)))
        rows = await pool.fetch(
            "SELECT signal_key, producer_role, producer_endpoint_identity, measurability "
            "FROM public.expected_signals"
        )
        assert len(rows) == 1
        assert rows[0]["signal_key"] == "health:measurement-gap:weight"
        assert rows[0]["producer_role"]
        assert rows[0]["producer_endpoint_identity"] == "google_health:user:owner"
        assert rows[0]["measurability"] in {"present", "absent"}

        policies = await pool.fetchval(
            "SELECT count(*) FROM pg_policy WHERE polrelid = 'public.expected_signals'::regclass"
        )
        assert policies == 3

        await pool.execute("CREATE ROLE expected_signals_health_test")
        await pool.execute("CREATE ROLE expected_signals_finance_test")
        await pool.execute(
            "GRANT SELECT, INSERT, UPDATE ON public.expected_signals "
            "TO expected_signals_health_test, expected_signals_finance_test"
        )
        async with pool.acquire() as connection:
            await connection.execute("SET ROLE expected_signals_health_test")
            try:
                await upsert_expected_signal(
                    connection,
                    signal_key="health:measurement-gap:blood_pressure",
                    producer="owner",
                    expected_cadence=timedelta(days=7),
                    last_observed_at=now,
                    now=now,
                )
            finally:
                await connection.execute("RESET ROLE")

            await connection.execute("SET ROLE expected_signals_finance_test")
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await upsert_expected_signal(
                        connection,
                        signal_key="health:measurement-gap:blood_pressure",
                        producer="owner",
                        expected_cadence=timedelta(days=30),
                        last_observed_at=now,
                        now=now,
                    )
            finally:
                await connection.execute("RESET ROLE")

        await pool.execute(
            "REVOKE ALL ON public.expected_signals "
            "FROM expected_signals_health_test, expected_signals_finance_test"
        )
        await pool.execute("DROP ROLE expected_signals_health_test")
        await pool.execute("DROP ROLE expected_signals_finance_test")

        await pool.execute("DROP VIEW public.v_qa_connector_state")
        await pool.execute("DROP TABLE public._expected_signal_test_liveness")
        for path in reversed(_MIGRATION_PATHS):
            await _run_migration(pool, path, "downgrade")
        assert await pool.fetchval("SELECT to_regclass('public.expected_signals')") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_endpoint_migration_marks_legacy_connector_rows_unmeasurable(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await _run_migration(pool, _MIGRATION_PATHS[0], "upgrade")
        await pool.execute(
            """
            INSERT INTO public.expected_signals (
                signal_key, producer, expected_cadence_seconds, last_observed_at,
                measurability, unmeasurable_reason, evaluated_at
            ) VALUES ('health:measurement-gap:weight', 'connector:google_health',
                      1209600, now(), 'absent', NULL, now())
            """
        )

        await _run_migration(pool, _MIGRATION_PATHS[1], "upgrade")

        row = await pool.fetchrow(
            "SELECT producer_endpoint_identity, measurability, unmeasurable_reason "
            "FROM public.expected_signals WHERE signal_key = 'health:measurement-gap:weight'"
        )
        assert row is not None
        assert row["producer_endpoint_identity"] is None
        assert row["measurability"] == "unmeasurable"
        assert row["unmeasurable_reason"] == "producer_endpoint_missing"

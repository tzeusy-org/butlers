"""Real-role PostgreSQL contract tests for the shared cost-claim ledger."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from alembic import command
from butlers.core.cost_claims import assert_claim
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_CORE_239_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "core"
    / "core_239_cost_claims.py"
)


def _replay_core_239(db_url: str) -> None:
    """Execute the migration's actual upgrade SQL against an existing schema."""
    spec = importlib.util.spec_from_file_location("core_239_replay", _CORE_239_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(migration, "op", fake_op):
        migration.upgrade()

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def db_name() -> str:
    return migration_db_name()


@pytest.fixture(scope="module")
def db_url(postgres_container, db_name: str) -> str:
    return create_migrated_test_db(
        postgres_container,
        db_name,
        chains=["core", "finance"],
        schemas={"finance": "finance"},
    )


def test_init_db_replay_twice_preserves_forced_rls(
    postgres_container, db_name: str, db_url: str
) -> None:
    migration_user = urlparse(db_url).username
    assert migration_user is not None
    engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    source = init_db_sql_for_dbapi()
    try:
        for _ in range(2):
            raw = engine.raw_connection()
            try:
                raw.autocommit = True
                with raw.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('butlers.connecting_user', %s, false)",
                        (migration_user,),
                    )
                    cursor.execute(source)
            finally:
                raw.close()
        with engine.connect() as conn:
            forced = conn.exec_driver_sql(
                "SELECT relname, relforcerowsecurity FROM pg_class "
                "WHERE oid IN ('public.cost_claims'::regclass, "
                "'public.cost_claim_resolutions'::regclass, "
                "'public.cost_claim_events'::regclass)"
            ).all()
            delete_policies = conn.exec_driver_sql(
                "SELECT count(*) FROM pg_policy WHERE polrelid IN "
                "('public.cost_claims'::regclass, "
                "'public.cost_claim_resolutions'::regclass, "
                "'public.cost_claim_events'::regclass) AND polcmd = 'd'"
            ).scalar_one()
        assert len(forced) == 3
        assert all(row.relforcerowsecurity for row in forced)
        assert delete_policies == 0

        runtime_engine = create_engine(db_url)
        claim_id = uuid.uuid4()
        try:
            with runtime_engine.begin() as conn:
                conn.exec_driver_sql("SET ROLE butler_relationship_rw")
                conn.exec_driver_sql(
                    """
                    INSERT INTO public.cost_claims
                        (id, claim_key, asserted_by, kind, direction, amount, currency,
                         counterparty_label, description)
                    VALUES (%s, %s, 'relationship', 'receivable', 'inbound', 25, 'SGD',
                            'Alex', 'Lunch')
                    """,
                    (claim_id, f"test:bootstrap-delete:{uuid.uuid4()}"),
                )
                conn.exec_driver_sql("SET ROLE butler_finance_rw")
                conn.exec_driver_sql(
                    "INSERT INTO public.cost_claim_resolutions (claim_id, state) "
                    "VALUES (%s, 'settled')",
                    (claim_id,),
                )

            protected_tables = {
                "cost_claims": "id",
                "cost_claim_resolutions": "claim_id",
                "cost_claim_events": "claim_id",
            }
            for table, claim_column in protected_tables.items():
                with runtime_engine.connect() as conn:
                    conn.exec_driver_sql("SET ROLE butler_finance_rw")
                    assert (
                        conn.exec_driver_sql(
                            "SELECT has_table_privilege(current_user, %s, 'DELETE')",
                            (f"public.{table}",),
                        ).scalar_one()
                        is False
                    )
                    with pytest.raises(ProgrammingError, match="permission denied"):
                        conn.exec_driver_sql(
                            f"DELETE FROM public.{table} WHERE {claim_column} = %s",  # noqa: S608
                            (claim_id,),
                        )

            with runtime_engine.connect() as conn:
                conn.exec_driver_sql("SET ROLE butler_finance_rw")
                assert (
                    conn.exec_driver_sql(
                        "SELECT count(*) FROM public.cost_claim_resolutions WHERE claim_id = %s",
                        (claim_id,),
                    ).scalar_one()
                    == 1
                )
                assert (
                    conn.exec_driver_sql(
                        "SELECT count(*) FROM public.cost_claim_events WHERE claim_id = %s",
                        (claim_id,),
                    ).scalar_one()
                    == 2
                )
        finally:
            runtime_engine.dispose()
    finally:
        engine.dispose()


def test_core_239_replay_reconciles_restore_policies_without_widening_finance_authority(
    postgres_container, db_name: str, db_url: str
) -> None:
    """A schema replay converges on one owner policy per ledger without opening writes."""
    bootstrap_db_url = migration_bootstrap_db_url(postgres_container, db_name)
    migration_owner = urlparse(db_url).username
    bootstrap_owner = urlparse(bootstrap_db_url).username
    assert migration_owner is not None
    assert bootstrap_owner is not None

    _replay_core_239(bootstrap_db_url)
    _replay_core_239(bootstrap_db_url)

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    claim_id = uuid.uuid4()
    try:
        with engine.connect() as conn:
            policy_rows = conn.exec_driver_sql(
                """
                SELECT c.relname,
                       p.polname,
                       p.polcmd,
                       p.polpermissive,
                       p.polroles = ARRAY[0::oid],
                       c.relforcerowsecurity,
                       pg_get_expr(p.polwithcheck, p.polrelid),
                       pg_get_userbyid(c.relowner)
                FROM pg_policy AS p
                JOIN pg_class AS c ON c.oid = p.polrelid
                WHERE p.polname IN (
                    'cost_claims_restore_owner',
                    'cost_claim_resolutions_restore_owner',
                    'cost_claim_events_restore_owner'
                )
                ORDER BY c.relname
                """
            ).all()
            assert [(row[0], row[1], *row[2:6], row[7]) for row in policy_rows] == [
                (
                    "cost_claim_events",
                    "cost_claim_events_restore_owner",
                    "a",
                    True,
                    True,
                    True,
                    migration_owner,
                ),
                (
                    "cost_claim_resolutions",
                    "cost_claim_resolutions_restore_owner",
                    "a",
                    True,
                    True,
                    True,
                    migration_owner,
                ),
                (
                    "cost_claims",
                    "cost_claims_restore_owner",
                    "a",
                    True,
                    True,
                    True,
                    migration_owner,
                ),
            ]
            expected_restore_check = f"(CURRENT_USER = '{migration_owner}'::name)"
            assert all(row[6] == expected_restore_check for row in policy_rows)
            assert all(
                row[6] != f"(CURRENT_USER = '{bootstrap_owner}'::name)" for row in policy_rows
            )

            restore_function = conn.exec_driver_sql(
                """
                SELECT p.prosecdef,
                       pg_get_userbyid(p.proowner),
                       p.proconfig,
                       NOT EXISTS (
                           SELECT 1
                           FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) AS acl
                           WHERE acl.grantee = 0
                             AND acl.privilege_type = 'EXECUTE'
                       )
                FROM pg_proc AS p
                WHERE p.oid = 'public.cost_claim_restore_row(text,jsonb)'::regprocedure
                """
            ).one()
            assert restore_function == (
                True,
                migration_owner,
                ["search_path=pg_catalog, public", "row_security=on"],
                True,
            )

            conn.exec_driver_sql("SET ROLE butler_relationship_rw")
            try:
                conn.exec_driver_sql(
                    """
                    INSERT INTO public.cost_claims
                        (id, claim_key, asserted_by, kind, direction, amount, currency,
                         counterparty_label, description)
                    VALUES (%s, %s, 'relationship', 'receivable', 'inbound', 25, 'SGD',
                            'Replay fixture', 'Verify Finance authority')
                    """,
                    (claim_id, f"test:core-239-replay:{uuid.uuid4()}"),
                )
                with pytest.raises(ProgrammingError, match="row-level security policy"):
                    conn.exec_driver_sql(
                        "INSERT INTO public.cost_claim_resolutions (claim_id, state) "
                        "VALUES (%s, 'settled')",
                        (claim_id,),
                    )
            finally:
                conn.exec_driver_sql("RESET ROLE")

            conn.exec_driver_sql("SET ROLE butler_finance_rw")
            try:
                conn.exec_driver_sql(
                    "INSERT INTO public.cost_claim_resolutions (claim_id, state) "
                    "VALUES (%s, 'settled')",
                    (claim_id,),
                )
            finally:
                conn.exec_driver_sql("RESET ROLE")
    finally:
        engine.dispose()


async def _role_conn(db_url: str, role: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(db_url)
    await conn.execute(f"SET ROLE {role}")
    await conn.execute("SET search_path TO finance, public")
    return conn


async def _insert_relationship_claim(conn: asyncpg.Connection, key: str) -> uuid.UUID:
    row = await conn.fetchrow(
        """
        INSERT INTO public.cost_claims
            (claim_key, asserted_by, kind, direction, amount, currency,
             counterparty_label, description)
        VALUES ($1, 'relationship', 'receivable', 'inbound', 25, 'SGD', 'Alex', 'Lunch')
        RETURNING id
        """,
        key,
    )
    return row["id"]


async def test_real_roles_split_assertion_and_resolution_authority(db_url: str) -> None:
    relationship = await _role_conn(db_url, "butler_relationship_rw")
    finance = await _role_conn(db_url, "butler_finance_rw")
    general = await _role_conn(db_url, "butler_general_rw")
    try:
        claim_id = await _insert_relationship_claim(relationship, f"test:{uuid.uuid4()}")
        assert (
            await finance.fetchval(
                "SELECT has_schema_privilege(current_user, 'relationship', 'USAGE')"
            )
            is False
        )
        assert (
            await finance.fetchval(
                "SELECT count(*) FROM public.cost_claims WHERE id = $1", claim_id
            )
            == 1
        )
        assert (
            await relationship.fetchval(
                "SELECT has_function_privilege(current_user, "
                "'public.cost_claim_restore_row(text,jsonb)', 'EXECUTE')"
            )
            is False
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await relationship.execute(
                "SELECT public.cost_claim_restore_row('cost_claims', '{}'::jsonb)"
            )

        with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError)):
            await general.execute(
                """
                INSERT INTO public.cost_claims
                    (claim_key, asserted_by, kind, direction, amount, currency,
                     counterparty_label, description)
                VALUES ($1, 'relationship', 'receivable', 'inbound', 25, 'SGD', 'Alex', 'Forged')
                """,
                f"test:{uuid.uuid4()}",
            )

        assert (
            await general.execute(
                "UPDATE public.cost_claims SET retracted_at = now(), retraction_reason = 'forged' "
                "WHERE id = $1",
                claim_id,
            )
            == "UPDATE 0"
        )
        assert (
            await finance.execute(
                "UPDATE public.cost_claims SET amount = 30 WHERE id = $1", claim_id
            )
            == "UPDATE 0"
        )
        assert (
            await relationship.execute(
                "UPDATE public.cost_claim_resolutions SET state = 'settled' WHERE claim_id = $1",
                claim_id,
            )
            == "UPDATE 0"
        )

        assert (
            await finance.fetchval(
                "SELECT count(*) FROM public.cost_claim_resolutions WHERE claim_id = $1", claim_id
            )
            == 0
        )

        assert (
            await finance.execute(
                "INSERT INTO public.cost_claim_resolutions (claim_id, state) "
                "VALUES ($1, 'settled')",
                claim_id,
            )
            == "INSERT 0 1"
        )
        assert (
            await finance.fetchval(
                "SELECT count(*) FROM public.cost_claim_events "
                "WHERE claim_id = $1 AND action = 'resolved'",
                claim_id,
            )
            == 1
        )
    finally:
        await relationship.close()
        await finance.close()
        await general.close()


async def test_concurrent_identical_assert_has_one_live_row(db_url: str) -> None:
    key = f"test:race:{uuid.uuid4()}"
    pools = []
    try:
        for _ in range(2):

            async def setup(conn: asyncpg.Connection) -> None:
                await conn.execute("SET ROLE butler_relationship_rw")

            pools.append(await asyncpg.create_pool(db_url, min_size=1, max_size=1, setup=setup))

        values = dict(
            claim_key=key,
            asserted_by="relationship",
            kind="receivable",
            direction="inbound",
            amount=Decimal("25.00"),
            currency="SGD",
            counterparty_entity_id=None,
            counterparty_label="Alex",
            expected_on=None,
            description="Lunch",
            evidence_kind="fact",
            evidence_ref="fact-1",
        )
        first, second = await asyncio.gather(
            assert_claim(pools[0], **values), assert_claim(pools[1], **values)
        )
        assert first["id"] == second["id"]
        assert (
            await pools[0].fetchval(
                "SELECT count(*) FROM public.cost_claims WHERE asserted_by = 'relationship' "
                "AND claim_key = $1 AND superseded_at IS NULL AND retracted_at IS NULL",
                key,
            )
            == 1
        )

        changed = {**values, "amount": Decimal("30.00")}
        amended = await assert_claim(pools[0], **changed)
        assert amended["id"] != first["id"]
        assert (
            await pools[0].fetchval(
                "SELECT superseded_at IS NOT NULL FROM public.cost_claims WHERE id = $1",
                first["id"],
            )
            is True
        )
        assert (
            await pools[0].fetchval(
                "SELECT state FROM public.cost_claim_resolutions WHERE claim_id = $1",
                amended["id"],
            )
            is None
        )
    finally:
        await asyncio.gather(*(pool.close() for pool in pools))


def test_core_239_downgrade_and_upgrade_round_trip(db_url: str) -> None:
    finance_config = _build_alembic_config(db_url, ["finance"], target_schema="finance")
    core_config = _build_alembic_config(db_url, ["core"], target_schema=None)
    command.downgrade(finance_config, "finance@finance_014")
    command.downgrade(core_config, "core@core_238")
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert (
                conn.exec_driver_sql("SELECT to_regclass('public.cost_claims')").scalar_one()
                is None
            )
    finally:
        engine.dispose()
    command.upgrade(core_config, "core@head")
    command.upgrade(finance_config, "finance@head")

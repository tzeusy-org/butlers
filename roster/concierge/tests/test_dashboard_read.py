"""Acceptance-criteria tests for the Concierge staffer + dashboard_read module (bu-0ynlk.3).

Each test class below maps to one of the six acceptance criteria on the bead:

  AC1 TestRosterIntegration        -- tool budget, docstrings, source envelopes
  AC2 TestParity                   -- tool output vs. the production read-model layer
  AC3 TestDbSecurity                -- RFC 0030 view-only cross-schema access
  AC4 TestMigrationChain            -- concierge chain upgrade/downgrade round-trip
  AC5 TestCatalogRouting            -- memory_catalog seed routes to concierge
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migration_db, migration_db_name

# All async tests in this file must share the session event loop so that the
# asyncpg pools created in module/function-scoped fixtures (which themselves
# run on the session loop per asyncio_default_fixture_loop_scope="session")
# are never used from a different loop. Synchronous tests in this file get a
# harmless "marked with asyncio but not async" warning from this mark; that
# is preferable to the InterfaceError a mismatched loop produces (see
# roster/general/tests/test_tools.py for the same tradeoff).
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
]

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Every schema unioned into concierge.v_fleet_sessions / v_fleet_spend (mirrors
# roster/concierge/migrations/001_fleet_views.py's _FLEET_SCHEMAS).
_FLEET_SCHEMAS: tuple[str, ...] = (
    "chronicler",
    "concierge",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)

_PRICED_MODEL = "claude-sonnet-4-5-20250929"
_UNPRICED_MODEL = "totally-unpriced-test-model"


def _conn_kwargs(db_url: str) -> dict[str, object]:
    parsed = urlparse(db_url)
    return {
        "host": parsed.hostname,
        "port": parsed.port,
        "user": parsed.username,
        "password": parsed.password,
        "db_name": parsed.path.lstrip("/"),
    }


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _execute_as_role(db_url: str, role_name: str, sql: str, *, scalar: bool = False):
    """Run *sql* after ``SET ROLE`` *role_name*, mirroring the established
    pattern in ``tests/config/test_schema_acl_isolation.py``."""
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {_quote_ident(role_name)}"))
            try:
                result = conn.execute(text(sql))
                if scalar:
                    return result.scalar()
                return result.fetchall()
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Module-scoped fleet database: core chain in every fleet schema + concierge
# chain (the two RFC 0030 views + the catalog seed).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fleet_db_url(postgres_container) -> str:
    from butlers.migrations import run_migrations

    db_url = create_migration_db(postgres_container, migration_db_name())
    for schema in _FLEET_SCHEMAS:
        asyncio.run(run_migrations(db_url, chain="core", schema=schema))
    asyncio.run(run_migrations(db_url, chain="concierge", schema="concierge"))
    return db_url


@pytest.fixture
async def admin_pool(fleet_db_url):
    pool = await asyncpg.create_pool(fleet_db_url, min_size=1, max_size=8)
    yield pool
    await pool.close()


@pytest.fixture
async def concierge_db(fleet_db_url):
    """A production-faithful ``Database`` that ``SET ROLE``s to butler_concierge_rw."""
    from butlers.db import Database

    db = Database(
        schema="concierge",
        role="butler_concierge_rw",
        **_conn_kwargs(fleet_db_url),
    )
    await db.connect()
    yield db
    await db.close()


async def _insert_session(
    pool: asyncpg.Pool,
    schema: str,
    *,
    session_id: uuid.UUID | None = None,
    started_at: datetime,
    completed_at: datetime | None,
    success: bool | None,
    trigger_source: str = "schedule",
    model: str | None = _PRICED_MODEL,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    error: str | None = None,
) -> uuid.UUID:
    session_id = session_id or uuid.uuid4()
    await pool.execute(
        f"""
        INSERT INTO {schema}.sessions (
            id, prompt, trigger_source, request_id, model, success, error,
            started_at, completed_at, input_tokens, output_tokens
        ) VALUES ($1, 'fixture prompt', $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        session_id,
        trigger_source,
        str(uuid.uuid4()),
        model,
        success,
        error,
        started_at,
        completed_at,
        input_tokens,
        output_tokens,
    )
    return session_id


# ---------------------------------------------------------------------------
# AC1 -- roster integration: tool budget, docstrings, source envelope
# ---------------------------------------------------------------------------

_EXPECTED_DASHBOARD_READ_TOOLS = {
    "dashboard_read_fleet_status",
    "dashboard_read_butler_detail",
    "dashboard_read_sessions_recent",
    "dashboard_read_session_detail",
    "dashboard_read_sessions_aggregate",
    "dashboard_read_sessions_trigger_breakdown",
    "dashboard_read_fleet_errors_recent",
    "dashboard_read_fleet_search",
    "dashboard_read_timeline_recent",
    "dashboard_read_butler_activity",
    "dashboard_read_spend_summary",
    "dashboard_read_spend_daily",
    "dashboard_read_spend_top_sessions",
    "dashboard_read_spend_breakdown_by_butler",
    "dashboard_read_spend_breakdown_by_model",
    "dashboard_read_insight_delivery_state",
}


class TestRosterIntegration:
    async def test_boots_within_tool_budget_with_source_envelopes(
        self, fleet_db_url, admin_pool, concierge_db
    ):
        from fastmcp import FastMCP

        from butlers.config import load_config
        from butlers.daemon import ButlerDaemon
        from butlers.modules.dashboard_read import DashboardReadModule

        now = datetime.now(UTC)
        health_session_id = await _insert_session(
            admin_pool,
            "health",
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=4),
            success=True,
        )

        config = load_config(_REPO_ROOT / "roster" / "concierge")
        module = DashboardReadModule()
        await module.on_startup(None, concierge_db)

        daemon = ButlerDaemon(butler_name="concierge")
        daemon.config = config
        daemon.mcp = FastMCP("test-concierge")
        daemon.db = concierge_db
        daemon.spawner = None
        daemon._modules = [module]

        daemon._register_core_tools()
        await daemon._register_module_tools()

        tools = {t.name: t for t in await daemon.mcp.list_tools()}
        assert 30 <= len(tools) <= 50, f"tool count {len(tools)} outside the 30-50 RFC 0002 budget"

        dashboard_tools = {
            name: t for name, t in tools.items() if name.startswith("dashboard_read_")
        }
        assert dashboard_tools.keys() == _EXPECTED_DASHBOARD_READ_TOOLS

        # Docstring completeness is scoped to this module's own tool surface
        # (module-dashboard-read spec) -- pre-existing core tools are out of
        # scope for this bead.
        for name, tool in dashboard_tools.items():
            assert tool.description and tool.description.strip(), f"{name} has an empty docstring"

        # Exercise every arg-free dashboard_read tool and assert the source envelope.
        arg_free = _EXPECTED_DASHBOARD_READ_TOOLS - {
            "dashboard_read_butler_detail",
            "dashboard_read_session_detail",
            "dashboard_read_butler_activity",
        }
        for name in arg_free:
            result = await tools[name].fn()
            assert "source" in result, f"{name} result missing 'source' envelope"
            source = result["source"]
            assert source.keys() >= {"kind", "ref", "as_of"}
            assert source["kind"] in {"view", "table"}
            datetime.fromisoformat(source["as_of"])

        detail_result = await tools["dashboard_read_session_detail"].fn(
            session_id=str(health_session_id)
        )
        assert detail_result["session"]["butler"] == "health"
        assert detail_result["source"]["ref"] == "concierge.v_fleet_sessions"

        activity_result = await tools["dashboard_read_butler_activity"].fn(butler="health")
        assert activity_result["source"]["ref"] == "concierge.v_fleet_sessions"

        detail_for_butler = await tools["dashboard_read_butler_detail"].fn(butler="health")
        assert detail_for_butler["butler"] == "health"
        assert detail_for_butler["source"]["ref"] == "concierge.v_fleet_sessions"


# ---------------------------------------------------------------------------
# AC2 -- parity against the production read-model / pricing layer
# ---------------------------------------------------------------------------


class TestParity:
    async def test_sessions_recent_matches_sessions_v1_row_to_summary(
        self, fleet_db_url, admin_pool, concierge_db
    ):
        from butlers.api.read_models.sessions_v1 import SUMMARY_COLUMNS, row_to_summary
        from butlers.modules.dashboard_read import queries as q

        now = datetime.now(UTC)
        session_id = await _insert_session(
            admin_pool,
            "health",
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
            success=True,
            trigger_source="schedule",
            model=_PRICED_MODEL,
            input_tokens=1234,
            output_tokens=567,
        )

        raw_row = await admin_pool.fetchrow(
            f"SELECT {SUMMARY_COLUMNS} FROM health.sessions WHERE id = $1", session_id
        )
        expected = row_to_summary(raw_row, butler="health")

        page = await q.query_sessions_recent(concierge_db.pool, limit=50, butler="health")
        actual = next(r for r in page.rows if r.id == session_id)

        assert actual.id == expected.id
        assert actual.butler == expected.butler
        assert actual.started_at == expected.started_at
        assert actual.ended_at == expected.completed_at
        assert actual.trigger_source == expected.trigger_source
        assert actual.model == expected.model
        assert actual.input_tokens == expected.input_tokens
        assert actual.output_tokens == expected.output_tokens
        assert actual.status == "success"
        assert expected.success is True

    async def test_spend_summary_matches_independently_computed_pricing(
        self, fleet_db_url, admin_pool, concierge_db
    ):
        from butlers.core.pricing import estimate_session_cost, load_pricing
        from butlers.modules.dashboard_read import queries as q

        now = datetime.now(UTC)
        priced_id = await _insert_session(
            admin_pool,
            "finance",
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=1),
            success=True,
            model=_PRICED_MODEL,
            input_tokens=10_000,
            output_tokens=2_000,
        )
        unpriced_id = await _insert_session(
            admin_pool,
            "finance",
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(minutes=1),
            success=True,
            model=_UNPRICED_MODEL,
            input_tokens=300,
            output_tokens=100,
        )

        pricing = load_pricing()

        # Independently recompute the expected aggregate straight from the
        # sanctioned view (bypassing queries.py's own _cost_cents helper) so
        # this test verifies query_spend_summary's aggregation, not just
        # pricing.py. Reads the view's *current* state rather than assuming
        # this test's two rows are the only ones present -- fleet_db_url is
        # a module-scoped fixture shared with other tests in this file.
        since = now - timedelta(hours=24)
        raw_rows = await admin_pool.fetch(
            f"SELECT model, input_tokens, output_tokens FROM {q.FLEET_SPEND_VIEW} "
            "WHERE started_at >= $1",
            since,
        )
        expected_cents = 0
        expected_unpriced_in = 0
        expected_unpriced_out = 0
        any_priced = False
        for r in raw_rows:
            dollars = (
                estimate_session_cost(
                    pricing, r["model"], r["input_tokens"] or 0, r["output_tokens"] or 0
                )
                if r["model"]
                else None
            )
            if dollars is None:
                expected_unpriced_in += r["input_tokens"] or 0
                expected_unpriced_out += r["output_tokens"] or 0
                continue
            any_priced = True
            expected_cents += round(dollars * 100)

        summary = await q.query_spend_summary(concierge_db.pool, pricing, period="today")

        assert summary.total_cost_cents == (expected_cents if any_priced or not raw_rows else None)
        assert summary.unpriced_input_tokens == expected_unpriced_in
        assert summary.unpriced_output_tokens == expected_unpriced_out
        assert summary.session_count == len(raw_rows)

        # Sanity: both fixture rows are actually visible through the view.
        page = await q.query_sessions_recent(concierge_db.pool, limit=200, butler="finance")
        seen_ids = {r.id for r in page.rows}
        assert {priced_id, unpriced_id} <= seen_ids

    async def test_fleet_status_matches_butlers_v1_query_sessions_24h(
        self, fleet_db_url, admin_pool, concierge_db
    ):
        from butlers.api.db import DatabaseManager
        from butlers.api.read_models.butlers_v1 import query_sessions_24h
        from butlers.modules.dashboard_read import queries as q

        now = datetime.now(UTC)
        await _insert_session(
            admin_pool,
            "general",
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(minutes=1),
            success=True,
        )
        await _insert_session(
            admin_pool,
            "general",
            started_at=now - timedelta(hours=3),
            completed_at=now - timedelta(hours=3) + timedelta(minutes=1),
            success=False,
            error="ValueError: boom",
        )

        conn_kwargs = _conn_kwargs(fleet_db_url)
        mgr = DatabaseManager(
            host=conn_kwargs["host"],
            port=conn_kwargs["port"],
            user=conn_kwargs["user"],
            password=conn_kwargs["password"],
        )
        try:
            for schema in _FLEET_SCHEMAS:
                await mgr.add_butler(schema, db_name=conn_kwargs["db_name"], db_schema=schema)
            expected_counts = await query_sessions_24h(mgr, butler_names=list(_FLEET_SCHEMAS))
        finally:
            for schema in _FLEET_SCHEMAS:
                pool = mgr.pool(schema)
                if pool is not None:
                    await pool.close()

        rows = await q.query_fleet_status(concierge_db.pool, butler="general")
        assert rows, "expected a fleet_status row for 'general'"
        assert rows[0].sessions_24h == expected_counts.get("general", 0)


# ---------------------------------------------------------------------------
# AC3 -- DB security: view-only cross-schema access, allowlisted columns
# ---------------------------------------------------------------------------

_FORBIDDEN_COLUMNS = {"prompt", "result", "tool_calls", "cost", "error"}
_ALLOWLISTED_SESSIONS_COLUMNS = {
    "id",
    "source_butler",
    "started_at",
    "ended_at",
    "status",
    "trigger_source",
    "model",
    "input_tokens",
    "output_tokens",
    "error_class",
}
_ALLOWLISTED_SPEND_COLUMNS = {
    "id",
    "source_butler",
    "started_at",
    "ended_at",
    "model",
    "input_tokens",
    "output_tokens",
}


class TestDbSecurity:
    def test_direct_cross_schema_select_denied(self, fleet_db_url, admin_pool):
        with pytest.raises(Exception, match="permission denied"):
            _execute_as_role(
                fleet_db_url,
                "butler_concierge_rw",
                "SELECT * FROM health.sessions",
            )

    def test_view_select_succeeds_with_allowlisted_columns_only(self, fleet_db_url, admin_pool):
        sessions_cols = _execute_as_role(
            fleet_db_url,
            "butler_concierge_rw",
            "SELECT * FROM concierge.v_fleet_sessions LIMIT 1",
        )
        assert sessions_cols is not None  # query itself did not raise

        column_rows = _execute_as_role(
            fleet_db_url,
            "butler_concierge_rw",
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'concierge' AND table_name = 'v_fleet_sessions'
            """,
        )
        column_names = {r[0] for r in column_rows}
        assert column_names == _ALLOWLISTED_SESSIONS_COLUMNS
        assert not (column_names & _FORBIDDEN_COLUMNS)

        spend_column_rows = _execute_as_role(
            fleet_db_url,
            "butler_concierge_rw",
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'concierge' AND table_name = 'v_fleet_spend'
            """,
        )
        spend_column_names = {r[0] for r in spend_column_rows}
        assert spend_column_names == _ALLOWLISTED_SPEND_COLUMNS
        assert not (spend_column_names & _FORBIDDEN_COLUMNS)

    def test_concierge_role_has_no_grant_on_other_schema_tables(self, fleet_db_url, admin_pool):
        # butler_concierge_rw holds no USAGE on 'health' at all, so it cannot
        # even resolve 'health.sessions' to check a privilege on it -- an even
        # stronger boundary than "SELECT denied": the object is not visible.
        # Resolve the OID as the (unrestricted) migration user first, then
        # check the privilege against that OID directly under SET ROLE.
        engine = create_engine(fleet_db_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                health_table_oid = conn.execute(
                    text("SELECT 'health.sessions'::regclass::oid")
                ).scalar()
        finally:
            engine.dispose()

        has_priv = _execute_as_role(
            fleet_db_url,
            "butler_concierge_rw",
            f"SELECT has_table_privilege(current_user, {health_table_oid}, 'SELECT')",
            scalar=True,
        )
        assert has_priv is False


# ---------------------------------------------------------------------------
# AC4 -- migration chain: upgrade/downgrade round-trip
# ---------------------------------------------------------------------------


class TestMigrationChain:
    def test_upgrade_downgrade_round_trip(self, postgres_container):
        from butlers.migrations import _build_alembic_config, get_chain_head, run_migrations

        db_url = create_migration_db(postgres_container, migration_db_name())
        # The view union spans every fleet schema, so all of them need the
        # core chain's `sessions` table before the concierge chain can create
        # concierge.v_fleet_sessions / v_fleet_spend.
        for schema in _FLEET_SCHEMAS:
            asyncio.run(run_migrations(db_url, chain="core", schema=schema))
        asyncio.run(run_migrations(db_url, chain="concierge", schema="concierge"))

        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                sessions_view = conn.execute(
                    text("SELECT to_regclass('concierge.v_fleet_sessions')")
                ).scalar()
                spend_view = conn.execute(
                    text("SELECT to_regclass('concierge.v_fleet_spend')")
                ).scalar()
        finally:
            engine.dispose()
        assert sessions_view is not None
        assert spend_view is not None

        from alembic import command

        config = _build_alembic_config(db_url, ["concierge"], target_schema="concierge")
        command.downgrade(config, "concierge@base")

        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                sessions_view = conn.execute(
                    text("SELECT to_regclass('concierge.v_fleet_sessions')")
                ).scalar()
                spend_view = conn.execute(
                    text("SELECT to_regclass('concierge.v_fleet_spend')")
                ).scalar()
                catalog_count = conn.execute(
                    text(
                        "SELECT count(*) FROM public.memory_catalog "
                        "WHERE source_schema = 'concierge' AND source_table = 'catalog_seed'"
                    )
                ).scalar()
        finally:
            engine.dispose()
        assert sessions_view is None
        assert spend_view is None
        assert catalog_count == 0

        # Chain integrity: the concierge branch resolves to exactly one head
        # (get_chain_head raises if the chain is empty or forked).
        assert get_chain_head("concierge")


# ---------------------------------------------------------------------------
# AC5 -- catalog routing: resolve_target_via_catalog surfaces concierge
# ---------------------------------------------------------------------------


class TestCatalogRouting:
    async def test_system_plane_question_routes_to_concierge(self, fleet_db_url, admin_pool):
        from butlers.core import delegation_ledger
        from butlers.core.delegation_ledger import resolve_target_via_catalog

        # A distractor row sharing some vocabulary with the query ("how much",
        # "spend") but belonging to a domain butler, so the assertion actually
        # exercises relevance ranking against a real competitor rather than
        # "the only matching row wins by default".
        distractor_summary = (
            "Finance tracks how much you personally spend on groceries and "
            "subscriptions each month."
        )
        await admin_pool.execute(
            """
            INSERT INTO public.memory_catalog (
                source_schema, source_table, source_id, source_butler, tenant_id,
                summary, search_vector, memory_type, title
            ) VALUES (
                'finance', 'facts', gen_random_uuid(), 'finance', 'owner',
                $1, to_tsvector('english', $1),
                'fact', 'Personal grocery/subscription spend'
            )
            """,
            distractor_summary,
        )

        async def _fts_search(pool, query, *, limit, mode):
            rows = await pool.fetch(
                """
                SELECT
                    id, source_butler, source_schema,
                    ts_rank(search_vector, plainto_tsquery('english', $1)) AS rrf_score
                FROM public.memory_catalog
                WHERE search_vector @@ plainto_tsquery('english', $1)
                ORDER BY rrf_score DESC
                LIMIT $2
                """,
                query,
                limit,
            )
            return [dict(r) for r in rows]

        import unittest.mock

        with unittest.mock.patch.object(delegation_ledger, "search_memory_catalog", _fts_search):
            target, match_id, score = await resolve_target_via_catalog(
                admin_pool, "how much did the fleet spend yesterday"
            )

        # match_id is memory_catalog's own row id (a fresh gen_random_uuid()
        # PK, distinct from the migration's pinned source_id sentinel) --
        # only target routing is a contract here.
        assert target == "concierge"
        assert match_id is not None
        assert score is not None

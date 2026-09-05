"""Tests for butlers.core.sessions — session log CRUD operations — condensed."""

from __future__ import annotations

import ast
import re
import shutil
import uuid

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool with sessions table cleared between tests."""
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE sessions CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# session_create / sessions_get
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_session_create_and_get(pool):
    """session_create returns UUID; fields are persisted; request_id=None raises."""
    from butlers.core.sessions import session_create, sessions_get

    req_id = str(uuid.uuid4())
    session_id = await session_create(
        pool,
        prompt="Run daily report",
        trigger_source="tick",
        trace_id="abc-123",
        request_id=req_id,
    )
    assert isinstance(session_id, uuid.UUID)

    session = await sessions_get(pool, session_id)
    assert session is not None
    assert session["prompt"] == "Run daily report"
    assert session["trigger_source"] == "tick"
    assert session["request_id"] == req_id
    assert session["result"] is None
    assert session["success"] is None
    assert session["completed_at"] is None

    # Missing key returns None
    assert await sessions_get(pool, uuid.uuid4()) is None

    # request_id=None raises
    with pytest.raises((ValueError, Exception)):
        await session_create(pool, prompt="x", trigger_source="tick", request_id=None)


# ---------------------------------------------------------------------------
# session_complete
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_session_complete_success_and_failure(pool):
    """session_complete sets success/error/result/duration; nonexistent raises."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    req_id = str(uuid.uuid4())
    session_id = await session_create(
        pool, prompt="Do work", trigger_source="schedule:x", request_id=req_id
    )

    # Complete successfully
    await session_complete(
        pool,
        session_id,
        output="All done",
        tool_calls=[{"name": "tool1"}],
        duration_ms=1234,
        success=True,
        input_tokens=100,
        output_tokens=50,
    )
    done = await sessions_get(pool, session_id)
    assert done["success"] is True
    assert done["result"] == "All done"
    assert done["duration_ms"] == 1234
    assert done["error"] is None
    assert done["completed_at"] is not None

    # Create and complete with failure
    s2 = await session_create(
        pool, prompt="Will fail", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await session_complete(
        pool,
        s2,
        output=None,
        tool_calls=[],
        duration_ms=0,
        success=False,
        error="something went wrong",
    )
    failed = await sessions_get(pool, s2)
    assert failed["success"] is False
    assert failed["error"] == "something went wrong"
    assert failed["result"] is None

    # Nonexistent raises
    with pytest.raises((ValueError, Exception)):
        await session_complete(
            pool, uuid.uuid4(), output=None, tool_calls=[], duration_ms=0, success=False
        )


@_asyncio_session
async def test_session_fields_sanitize_untranslatable_unicode(pool):
    """Bad Unicode in TEXT/JSONB payloads should be stripped before persistence."""
    from butlers.core.sessions import session_complete, session_create, sessions_get

    session_id = await session_create(
        pool,
        prompt="prompt\x00with\ud83dnoise",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
    )
    await session_complete(
        pool,
        session_id,
        output="done\x00\ud83d",
        tool_calls=[
            {
                "name": "tool\x00\ud83d",
                "arguments": {
                    "value": "bad\x00\ud83dtext",
                    "items": ["ok", "\x00\ud83d"],
                },
            }
        ],
        duration_ms=42,
        success=False,
        error="boom\x00\ud83d",
        cost={"raw": "cost\x00\ud83d"},
    )

    row = await sessions_get(pool, session_id)
    assert row is not None
    assert row["prompt"] == "promptwithnoise"
    assert row["result"] == "done"
    assert row["error"] == "boom"
    assert row["tool_calls"] == [
        {
            "name": "tool",
            "arguments": {
                "value": "badtext",
                "items": ["ok", ""],
            },
        }
    ]
    assert row["cost"] == {"raw": "cost"}


# ---------------------------------------------------------------------------
# sessions_list / sessions_summary
# ---------------------------------------------------------------------------


@pytest.mark.pg_clock
@_asyncio_session
async def test_sessions_list_and_summary(pool):
    """sessions_list returns sessions in order; sessions_summary aggregates correctly."""
    from butlers.core.sessions import (
        session_complete,
        session_create,
        sessions_list,
        sessions_summary,
    )

    for i in range(3):
        sid = await session_create(
            pool,
            prompt=f"task {i}",
            trigger_source="tick",
            request_id=str(uuid.uuid4()),
            model="claude-3",
        )
        await session_complete(
            pool,
            sid,
            output=f"result {i}",
            tool_calls=[],
            duration_ms=100,
            success=True,
            input_tokens=100,
            output_tokens=50,
        )

    listed = await sessions_list(pool)
    assert len(listed) >= 3
    # Pagination works
    page1 = await sessions_list(pool, limit=2, offset=0)
    assert len(page1) == 2

    summary = await sessions_summary(pool, period="7d")
    assert summary["total_sessions"] >= 3
    assert "by_model" in summary
    # A clean run of successful sessions produces zero failures and an empty
    # error-marker breakdown (bu-8cdl1.9 slice 1).
    assert summary["succeeded"] >= 3
    assert summary["failed"] == 0
    assert summary["by_error_marker"] == {}

    # Invalid period raises
    with pytest.raises((ValueError, Exception)):
        await sessions_summary(pool, period="invalid_period")


@pytest.mark.pg_clock
@_asyncio_session
async def test_sessions_summary_error_marker_breakdown(pool):
    """A guardrail-marker failure increments failed + by_error_marker deterministically."""
    from butlers.core.sessions import session_complete, session_create, sessions_summary

    guardrail_sid = await session_create(
        pool,
        prompt="task guardrail",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
        model="claude-3",
    )
    await session_complete(
        pool,
        guardrail_sid,
        output=None,
        tool_calls=[],
        duration_ms=100,
        success=False,
        error="RuntimeError: degenerate_tool_loop: 5 consecutive identical calls to foo",
    )

    other_sid = await session_create(
        pool,
        prompt="task other",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
        model="claude-3",
    )
    await session_complete(
        pool,
        other_sid,
        output=None,
        tool_calls=[],
        duration_ms=100,
        success=False,
        error="ValueError: something unrelated broke",
    )

    # Genuine switchboard classification timeout: "mini" model, <=60s bound.
    classification_sid = await session_create(
        pool,
        prompt="classify inbound message",
        trigger_source="classification",
        request_id=str(uuid.uuid4()),
        model="claude-haiku-4-5-mini",
    )
    await session_complete(
        pool,
        classification_sid,
        output=None,
        tool_calls=[],
        duration_ms=100,
        success=False,
        error="TimeoutError: Session timed out after 45s (model=claude-haiku-4-5-mini, "
        "butler=switchboard)",
    )

    # A non-classification switchboard timeout (e.g. a route-dispatch session)
    # emits the identical message template but with a non-"mini" model. This
    # must NOT be misclassified as classification_timeout (bu-ixqo6 review
    # finding on PR #4004).
    route_sid = await session_create(
        pool,
        prompt="dispatch to butler",
        trigger_source="route",
        request_id=str(uuid.uuid4()),
        model="claude-sonnet-4-6",
    )
    await session_complete(
        pool,
        route_sid,
        output=None,
        tool_calls=[],
        duration_ms=100,
        success=False,
        error="TimeoutError: Session timed out after 30s (model=claude-sonnet-4-6, "
        "butler=switchboard)",
    )

    summary = await sessions_summary(pool, period="7d")
    assert summary["failed"] >= 4
    assert summary["by_error_marker"]["degenerate_tool_loop"] == 1
    assert summary["by_error_marker"]["classification_timeout"] == 1
    # ValueError plus the non-classification switchboard timeout both bucket
    # under "other".
    assert summary["by_error_marker"]["other"] == 2


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------


@pytest.mark.pg_clock
@_asyncio_session
async def test_recover_orphaned_sessions_closes_open_rows(pool):
    """Open sessions are closed and marked failed; completed rows untouched."""
    from datetime import UTC, datetime, timedelta

    from butlers.core.sessions import (
        recover_orphaned_sessions,
        session_complete,
        session_create,
        sessions_get,
    )

    # Two open sessions (one back-dated to verify duration_ms is populated).
    open_recent = await session_create(
        pool, prompt="recent open", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    open_old = await session_create(
        pool, prompt="old open", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    backdated = datetime.now(UTC) - timedelta(days=3)
    await pool.execute("UPDATE sessions SET started_at = $2 WHERE id = $1", open_old, backdated)

    # One already-completed session — must not be touched.
    completed = await session_create(
        pool, prompt="done", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await session_complete(
        pool, completed, output="ok", tool_calls=[], duration_ms=42, success=True
    )

    n = await recover_orphaned_sessions(pool)
    assert n == 2

    recent_row = await sessions_get(pool, open_recent)
    old_row = await sessions_get(pool, open_old)
    completed_row = await sessions_get(pool, completed)

    # Both orphans closed and marked failed.
    for row in (recent_row, old_row):
        assert row["completed_at"] is not None
        assert row["success"] is False
        assert row["error"] == "orphaned: daemon restart"
        assert row["duration_ms"] is not None and row["duration_ms"] >= 0

    # Old orphan got a non-trivial duration backfilled (~3 days).
    assert old_row["duration_ms"] >= 2 * 24 * 3600 * 1000

    # Completed session is untouched.
    assert completed_row["success"] is True
    assert completed_row["duration_ms"] == 42
    assert completed_row["error"] is None


@pytest.mark.pg_clock
@_asyncio_session
async def test_recover_orphaned_sessions_clamps_duration_for_very_old_rows(pool):
    """30-day-old orphans must not overflow the INTEGER duration_ms column."""
    from datetime import UTC, datetime, timedelta

    from butlers.core.sessions import recover_orphaned_sessions, session_create, sessions_get

    sid = await session_create(
        pool, prompt="ancient", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await pool.execute(
        "UPDATE sessions SET started_at = $2 WHERE id = $1",
        sid,
        datetime.now(UTC) - timedelta(days=30),
    )
    n = await recover_orphaned_sessions(pool)
    assert n == 1
    row = await sessions_get(pool, sid)
    assert row["duration_ms"] == 2147483647


@_asyncio_session
async def test_recover_orphaned_sessions_idempotent_and_no_open(pool):
    """Returns 0 when no open rows; second call after recovery also returns 0."""
    from butlers.core.sessions import (
        recover_orphaned_sessions,
        session_complete,
        session_create,
    )

    # Empty table → 0
    assert await recover_orphaned_sessions(pool) == 0

    # All-completed table → 0
    sid = await session_create(
        pool, prompt="x", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await session_complete(pool, sid, output="ok", tool_calls=[], duration_ms=1, success=True)
    assert await recover_orphaned_sessions(pool) == 0

    # One orphan → 1, then 0 on second pass
    await session_create(pool, prompt="orphan", trigger_source="tick", request_id=str(uuid.uuid4()))
    assert await recover_orphaned_sessions(pool) == 1
    assert await recover_orphaned_sessions(pool) == 0


@_asyncio_session
async def test_recover_orphaned_sessions_preserves_existing_error(pool):
    """If error is already set (e.g. budget overrun), do not overwrite it."""
    from butlers.core.sessions import recover_orphaned_sessions, session_create, sessions_get

    sid = await session_create(
        pool, prompt="x", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await pool.execute("UPDATE sessions SET error = 'budget overrun' WHERE id = $1", sid)
    n = await recover_orphaned_sessions(pool)
    assert n == 1
    row = await sessions_get(pool, sid)
    assert row["error"] == "budget overrun"
    assert row["success"] is False
    assert row["completed_at"] is not None


# ---------------------------------------------------------------------------
# top_sessions / schedule_costs — date-range scoping [bu-oaiiw]
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_top_sessions_date_range_filters_by_started_at(pool):
    """from_date/to_date scope results to sessions started within the inclusive range."""
    from datetime import UTC, datetime

    from butlers.core.sessions import session_complete, session_create, top_sessions

    in_range = await session_create(
        pool, prompt="in-range", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await pool.execute(
        "UPDATE sessions SET started_at = $2 WHERE id = $1",
        in_range,
        datetime(2026, 5, 3, tzinfo=UTC),
    )
    await session_complete(
        pool,
        in_range,
        output="ok",
        tool_calls=[],
        duration_ms=10,
        success=True,
        input_tokens=9000,
        output_tokens=9000,
    )

    out_of_range = await session_create(
        pool, prompt="out-of-range", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    await pool.execute(
        "UPDATE sessions SET started_at = $2 WHERE id = $1",
        out_of_range,
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    await session_complete(
        pool,
        out_of_range,
        output="ok",
        tool_calls=[],
        duration_ms=10,
        success=True,
        input_tokens=99999,
        output_tokens=99999,
    )

    scoped = await top_sessions(pool, limit=10, from_date="2026-05-01", to_date="2026-05-07")
    scoped_ids = {s["session_id"] for s in scoped["sessions"]}
    assert str(in_range) in scoped_ids
    assert str(out_of_range) not in scoped_ids

    # Omitting both from_date/to_date preserves all-time behavior (back-compat).
    all_time = await top_sessions(pool, limit=10)
    all_time_ids = {s["session_id"] for s in all_time["sessions"]}
    assert str(in_range) in all_time_ids
    assert str(out_of_range) in all_time_ids

    # Only one of from_date/to_date raises.
    with pytest.raises(ValueError):
        await top_sessions(pool, from_date="2026-05-01")


@_asyncio_session
async def test_schedule_costs_date_range_filters_runs(pool):
    """from_date/to_date scope run aggregates; schedules with no runs in-window still appear."""
    from datetime import UTC, datetime

    from butlers.core.scheduler import schedule_create
    from butlers.core.sessions import schedule_costs, session_complete, session_create

    await schedule_create(pool, name="daily-report", cron="0 8 * * *", prompt="run report")

    recent = await session_create(
        pool,
        prompt="scoped run",
        trigger_source="schedule:daily-report",
        request_id=str(uuid.uuid4()),
        model="claude-3",
    )
    await pool.execute(
        "UPDATE sessions SET started_at = $2 WHERE id = $1",
        recent,
        datetime(2026, 5, 3, tzinfo=UTC),
    )
    await session_complete(
        pool,
        recent,
        output="ok",
        tool_calls=[],
        duration_ms=10,
        success=True,
        input_tokens=1000,
        output_tokens=500,
    )

    old = await session_create(
        pool,
        prompt="old run",
        trigger_source="schedule:daily-report",
        request_id=str(uuid.uuid4()),
        model="claude-3",
    )
    await pool.execute(
        "UPDATE sessions SET started_at = $2 WHERE id = $1",
        old,
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    await session_complete(
        pool,
        old,
        output="ok",
        tool_calls=[],
        duration_ms=10,
        success=True,
        input_tokens=50000,
        output_tokens=50000,
    )

    scoped = await schedule_costs(pool, from_date="2026-05-01", to_date="2026-05-07")
    entries = [e for e in scoped["schedules"] if e["name"] == "daily-report"]
    assert entries, "schedule must still appear even if scoped totals are zero"
    scoped_tokens = sum(e["total_input_tokens"] for e in entries)
    assert scoped_tokens == 1000

    all_time = await schedule_costs(pool)
    all_time_entries = [e for e in all_time["schedules"] if e["name"] == "daily-report"]
    all_time_tokens = sum(e["total_input_tokens"] for e in all_time_entries)
    assert all_time_tokens == 51000

    with pytest.raises(ValueError):
        await schedule_costs(pool, from_date="2026-05-01")


# ---------------------------------------------------------------------------
# Immutability contract
# ---------------------------------------------------------------------------


# Destructive SQL *statements*, matched with word boundaries so that English
# prose ("a truncated one") and identifiers ("truncated") cannot trip them.
# The optional TABLE keyword plus the trailing character class keeps both
# ``TRUNCATE sessions`` and ``TRUNCATE TABLE sessions`` in scope, including the
# f-string case where the table name is an interpolation rather than a literal.
_DESTRUCTIVE_SQL = (
    re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\s+(?:TABLE\b\s*)?[\w\"]", re.IGNORECASE),
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
)
_DESTRUCTIVE_NAME_WORDS = ("delete", "drop", "truncate")
# Stand-in for an f-string interpolation, so a table name supplied at runtime
# still looks like a table name to the patterns above.
_INTERPOLATION = "_expr_"


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """ids of the Constant nodes that sit in a docstring position."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _string_literals(source: str) -> list[str]:
    """Every non-docstring string literal in ``source``, f-strings reassembled."""
    tree = ast.parse(source)
    skip = _docstring_node_ids(tree)
    literals: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                skip.add(id(value))
                parts.append(value.value)
            else:
                parts.append(_INTERPOLATION)
        literals.append("".join(parts))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            literals.append(node.value)

    return literals


def _exposed_definition_names(source: str) -> list[str]:
    """Public functions, classes and methods that ``source`` itself defines.

    Module-level and class-body definitions only: those are the surface the
    module exposes. Definitions nested inside a function body are locals, not
    surface, and names starting with ``_`` are private helpers whose safety is
    established by the SQL scan rather than by their spelling.
    """
    names: list[str] = []

    def visit(body: list[ast.stmt]) -> None:
        for node in body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if not node.name.startswith("_"):
                names.append(node.name)
            if isinstance(node, ast.ClassDef):
                visit(node.body)

    visit(ast.parse(source).body)
    return names


def test_no_delete_or_truncate_in_sessions_module():
    """sessions module must not execute destructive SQL or define destructive callables.

    The session log is append-only, so this guard asks one question: does this
    module issue DELETE/TRUNCATE/DROP against the sessions tables?

    It answers it over the AST rather than over raw source text, because a bare
    substring scan cannot distinguish SQL from prose. The previous version
    asserted ``"TRUNCATE" not in source.upper()`` across the whole file, which
    fired on the English word "truncated" in a docstring and on any identifier
    containing it — a standing tax on a module whose subject is counting — while
    never checking ``DELETE FROM`` at all (bu-rqbac).

    Docstrings are deliberately excluded from the literal scan. A docstring is
    prose by construction and is never handed to a database driver: reaching one
    would take an explicit ``__doc__`` dereference, which is not a way anybody
    smuggles a DELETE past review. Including them buys no safety and reinstates
    exactly the false positive this guard exists to stop having. Every other
    string literal — including f-string fragments, which are where this
    codebase's SQL actually lives — is scanned.

    The member half walks the public definitions the module itself makes,
    replacing a ``dir(mod)`` scan that counted imported modules (``asyncpg``,
    ``json``, ``uuid``) as members while never seeing a method on a class
    defined here. Private helpers are exempt from the name check: a
    ``_truncate_cadence_window`` describes enumeration, not SQL, and rejecting
    it was the same tax in a different spelling. What a private helper actually
    does to the database is caught by the literal scan above.
    """
    import inspect

    import butlers.core.sessions as mod

    source = inspect.getsource(mod)

    offenders = [
        literal
        for literal in _string_literals(source)
        if any(pattern.search(literal) for pattern in _DESTRUCTIVE_SQL)
    ]
    assert not offenders, f"destructive SQL in sessions module: {offenders}"

    destructive_defs = [
        name
        for name in _exposed_definition_names(source)
        if any(word in name.lower() for word in _DESTRUCTIVE_NAME_WORDS)
    ]
    assert not destructive_defs, (
        f"destructive public definitions in sessions module: {destructive_defs}"
    )


# ---------------------------------------------------------------------------
# Spend DB-first evidence path — real-Postgres integration (bu-h1i8k)
#
# The unit tests in tests/api/test_spend.py patch spend.top_sessions /
# schedule_costs; these run the REAL core-helper SQL against a migrated DB and
# feed it through the spend DB evidence helpers (SQL + build + pricing + merge),
# guarding against the mocked-pool-vs-integration skew.
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_spend_top_sessions_from_db_ranks_and_prices(pool):
    """_get_butler_top_sessions_from_db ranks by token volume and prices per model."""
    from unittest.mock import MagicMock

    from butlers.api.deps import ButlerConnectionInfo
    from butlers.api.routers.spend import _get_butler_top_sessions_from_db
    from butlers.core.pricing import ModelPricing, PricingConfig
    from butlers.core.sessions import session_complete, session_create

    pricing = PricingConfig(
        models={
            "m-costly": ModelPricing(0.00001, 0.00002),
            "m-cheap": ModelPricing(0.0000001, 0.0000002),
        }
    )

    costly = await session_create(
        pool, prompt="big", trigger_source="tick", request_id=str(uuid.uuid4()), model="m-costly"
    )
    await session_complete(
        pool,
        costly,
        output="ok",
        tool_calls=[],
        duration_ms=1,
        success=True,
        input_tokens=10000,
        output_tokens=5000,
    )
    cheap = await session_create(
        pool, prompt="small", trigger_source="tick", request_id=str(uuid.uuid4()), model="m-cheap"
    )
    await session_complete(
        pool,
        cheap,
        output="ok",
        tool_calls=[],
        duration_ms=1,
        success=True,
        input_tokens=100,
        output_tokens=50,
    )

    db = MagicMock()
    db.pool = MagicMock(return_value=pool)
    result = await _get_butler_top_sessions_from_db(
        db, ButlerConnectionInfo(name="finance", port=1), pricing, limit=10
    )
    assert result is not None
    # top_sessions orders by (input+output) desc: the costly (15k-token) session ranks first.
    assert result[0].session_id == str(costly)
    assert result[0].butler == "finance"
    # priced per model — the costly model's session costs strictly more.
    assert result[0].cost_usd > result[1].cost_usd


@_asyncio_session
async def test_spend_schedule_costs_from_db_merges_multi_model(pool):
    """_get_butler_schedule_costs_from_db merges a multi-model schedule into one
    priced (butler, schedule) entry (the core merge that fixes duplicate keys)."""
    from unittest.mock import MagicMock

    from butlers.api.deps import ButlerConnectionInfo
    from butlers.api.routers.spend import _get_butler_schedule_costs_from_db
    from butlers.core.pricing import ModelPricing, PricingConfig
    from butlers.core.scheduler import schedule_create
    from butlers.core.sessions import session_complete, session_create

    pricing = PricingConfig(
        models={
            "m-a": ModelPricing(0.00001, 0.00002),
            "m-b": ModelPricing(0.00001, 0.00002),
        }
    )
    await schedule_create(pool, name="brief", cron="0 8 * * *", prompt="brief")
    for model in ("m-a", "m-b"):
        sid = await session_create(
            pool,
            prompt="run",
            trigger_source="schedule:brief",
            request_id=str(uuid.uuid4()),
            model=model,
        )
        await session_complete(
            pool,
            sid,
            output="ok",
            tool_calls=[],
            duration_ms=1,
            success=True,
            input_tokens=1000,
            output_tokens=500,
        )

    db = MagicMock()
    db.pool = MagicMock(return_value=pool)
    result = await _get_butler_schedule_costs_from_db(
        db, ButlerConnectionInfo(name="finance", port=1), pricing, None, None
    )
    assert result is not None
    brief = [c for c in result if c.schedule_name == "brief"]
    assert len(brief) == 1  # merged across m-a + m-b, not split into two fragments
    assert brief[0].total_runs == 2
    assert brief[0].butler == "finance"
    assert brief[0].total_cost_usd > 0

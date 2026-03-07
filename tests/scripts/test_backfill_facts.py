"""Unit tests for src/butlers/scripts/backfill_facts.py.

Tests are isolated from the real DB and EmbeddingEngine; both are mocked.
Coverage targets:
  - backfill_key / fact_exists / idempotency guard
  - insert_fact (dry-run and live path)
  - owner_entity_id resolution fallback
  - each phase runner (happy path + table-not-found graceful skip)
  - CLI argument parsing and _main exit codes
"""

from __future__ import annotations

import importlib
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Make the script importable without loading sentence-transformers.
# We stub the embedding module before importing backfill_facts.
# ---------------------------------------------------------------------------

_SCRIPTS_PATH = Path(__file__).resolve().parents[2] / "src" / "butlers" / "scripts"
_BACKFILL_MODULE_NAME = "butlers.scripts.backfill_facts"


def _make_fake_embedding_engine():
    """Return a fake EmbeddingEngine that produces deterministic vectors."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


@pytest.fixture(autouse=True)
def stub_embedding(monkeypatch):
    """Replace _load_embedding_engine so tests never load sentence-transformers."""
    fake_engine = _make_fake_embedding_engine()
    # If already imported, patch in-place; otherwise patch during import.
    if _BACKFILL_MODULE_NAME in sys.modules:
        mod = sys.modules[_BACKFILL_MODULE_NAME]
        monkeypatch.setattr(mod, "_embedding_engine", fake_engine)
        monkeypatch.setattr(mod, "_load_embedding_engine", lambda: fake_engine)
    else:
        # Ensure butlers.scripts package is on the path.
        scripts_src = str(_SCRIPTS_PATH.parent.parent)  # src/
        if scripts_src not in sys.path:
            sys.path.insert(0, scripts_src)
        with patch(
            "butlers.scripts.backfill_facts._load_embedding_engine",
            return_value=fake_engine,
        ):
            pass  # import happens lazily; patch object at module level after import

    yield fake_engine


def _import_module():
    """Return the backfill_facts module, importing it if needed."""
    if _BACKFILL_MODULE_NAME not in sys.modules:
        scripts_src = str(_SCRIPTS_PATH.parent.parent)
        if scripts_src not in sys.path:
            sys.path.insert(0, scripts_src)
        mod = importlib.import_module(_BACKFILL_MODULE_NAME)
    else:
        mod = sys.modules[_BACKFILL_MODULE_NAME]
    return mod


# Eagerly import so the autouse fixture can patch it.
bf = _import_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pool_with_rows(rows, *, fetchval_return=None, fetchrow_return=None):
    """Build a minimal mock asyncpg pool."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool


def _row(**kwargs):
    """Build a dict-like asyncpg record substitute."""
    return dict(**kwargs)


# ---------------------------------------------------------------------------
# _backfill_key
# ---------------------------------------------------------------------------


def test_backfill_key_format():
    key = bf._backfill_key("measurements", 42)
    assert key == "measurements:42"


def test_backfill_key_uuid():
    uid = uuid.uuid4()
    key = bf._backfill_key("symptoms", uid)
    assert key == f"symptoms:{uid}"


# ---------------------------------------------------------------------------
# _fact_exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fact_exists_true(monkeypatch):
    pool = _pool_with_rows([], fetchrow_return={"1": 1})
    assert await bf._fact_exists(pool, "measurements:1") is True


@pytest.mark.asyncio
async def test_fact_exists_false(monkeypatch):
    pool = _pool_with_rows([], fetchrow_return=None)
    assert await bf._fact_exists(pool, "measurements:99") is False


# ---------------------------------------------------------------------------
# _owner_entity_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_entity_id_from_contacts():
    eid = uuid.uuid4()
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"id": eid})
    result = await bf._owner_entity_id(pool)
    assert result == eid


@pytest.mark.asyncio
async def test_owner_entity_id_fallback():
    eid = uuid.uuid4()
    pool = AsyncMock()
    # First call (contacts join) returns None; second call (entities direct) returns row.
    pool.fetchrow = AsyncMock(side_effect=[None, {"id": eid}])
    result = await bf._owner_entity_id(pool)
    assert result == eid


@pytest.mark.asyncio
async def test_owner_entity_id_none_when_absent():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    result = await bf._owner_entity_id(pool)
    assert result is None


# ---------------------------------------------------------------------------
# _insert_fact — dry run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_fact_dry_run_does_not_call_execute(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())
    pool = AsyncMock()
    await bf._insert_fact(
        pool,
        subject="user",
        predicate="medication",
        content="Aspirin, 100mg, daily",
        entity_id=None,
        valid_at=None,
        permanence="stable",
        source_butler="health",
        backfill_key="medications:1",
        dry_run=True,
    )
    pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# _insert_fact — live path (mock pool.execute)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_fact_live_calls_execute(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    eid = uuid.uuid4()
    await bf._insert_fact(
        pool,
        subject="user",
        predicate="condition_status",
        content="Hypertension, active",
        entity_id=eid,
        valid_at=datetime(2024, 1, 1, tzinfo=UTC),
        permanence="stable",
        source_butler="health",
        backfill_key="conditions:abc",
    )
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args[0]
    # The SQL INSERT should reference $1 .. $16
    assert "INSERT INTO facts" in call_args[0]


# ---------------------------------------------------------------------------
# Health phase — measurements backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_health_measurements_happy(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())
    owner_id = uuid.uuid4()
    rows = [
        _row(
            id=1,
            type="weight",
            value='{"kg": 75}',
            notes=None,
            measured_at=datetime(2024, 3, 1, tzinfo=UTC),
        ),
    ]
    pool = _pool_with_rows(rows)
    # _fact_exists → no existing fact
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = bf.Stats()
    await bf._backfill_health_measurements(pool, owner_id, stats, dry_run=False)

    assert stats.processed == 1
    assert stats.inserted == 1
    assert stats.skipped == 0
    assert stats.errors == 0


@pytest.mark.asyncio
async def test_backfill_health_measurements_skips_existing(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())
    owner_id = uuid.uuid4()
    rows = [
        _row(
            id=1,
            type="weight",
            value='{"kg": 75}',
            notes=None,
            measured_at=datetime(2024, 3, 1, tzinfo=UTC),
        ),
    ]
    pool = _pool_with_rows(rows)
    # _fact_exists → fact already present
    pool.fetchrow = AsyncMock(return_value={"1": 1})
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = bf.Stats()
    await bf._backfill_health_measurements(pool, owner_id, stats, dry_run=False)

    assert stats.skipped == 1
    assert stats.inserted == 0
    pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Health phase — medications (property fact, no valid_at)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_health_medications_property_fact(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())
    owner_id = uuid.uuid4()
    rows = [
        _row(
            id=uuid.uuid4(),
            name="Metformin",
            dosage="500mg",
            frequency="daily",
            notes=None,
            active=True,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    ]
    pool = _pool_with_rows(rows)
    pool.fetchrow = AsyncMock(return_value=None)  # no existing fact
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = bf.Stats()
    await bf._backfill_health_medications(pool, owner_id, stats, dry_run=False)

    assert stats.inserted == 1
    # Confirm valid_at=None was passed (property fact)
    call_args = pool.execute.call_args[0]
    # The 16th positional arg ($16) maps to valid_at — should be None
    assert call_args[16] is None


# ---------------------------------------------------------------------------
# Relationship phase — quick_facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_rel_quick_facts_happy(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())
    eid = uuid.uuid4()
    rows = [
        _row(
            id=1,
            contact_id=uuid.uuid4(),
            key="favorite_food",
            value="Pizza",
            contact_name="Alice Smith",
            entity_id=str(eid),
            updated_at=datetime(2024, 2, 1, tzinfo=UTC),
        )
    ]
    pool = _pool_with_rows(rows)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = bf.Stats()
    await bf._backfill_rel_quick_facts(pool, stats, dry_run=False)

    assert stats.inserted == 1


# ---------------------------------------------------------------------------
# Finance phase — transactions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_fin_transactions_happy(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())
    owner_id = uuid.uuid4()
    rows = [
        _row(
            id=uuid.uuid4(),
            merchant="Trader Joe's",
            amount=Decimal("45.00"),
            currency="USD",
            direction="debit",
            category="groceries",
            description=None,
            posted_at=datetime(2024, 4, 1, tzinfo=UTC),
        )
    ]
    pool = _pool_with_rows(rows)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = bf.Stats()
    await bf._backfill_fin_transactions(pool, owner_id, stats, dry_run=False)

    assert stats.inserted == 1
    assert stats.errors == 0


# ---------------------------------------------------------------------------
# Home phase — ha_entity_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_home_happy(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())
    owner_id = uuid.uuid4()
    rows = [
        _row(
            entity_id="sensor.living_room_temperature",
            state="22.5",
            attributes={"friendly_name": "Living Room Temperature", "unit_of_measurement": "°C"},
            last_updated=datetime(2024, 5, 1, tzinfo=UTC),
            captured_at=datetime(2024, 5, 1, 12, 0, tzinfo=UTC),
        )
    ]

    pool = AsyncMock()
    # _owner_entity_id call uses fetchrow; backfill uses fetch + fetchrow (exists check)
    call_count = [0]

    async def _fetchrow_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # owner entity lookup
            return {"id": owner_id}
        return None  # _fact_exists → no existing fact

    async def _fetch_side_effect(*args, **kwargs):
        return rows

    pool.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)
    pool.fetch = AsyncMock(side_effect=_fetch_side_effect)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = await bf.backfill_home(pool, dry_run=False)

    assert stats.inserted == 1
    assert stats.errors == 0


@pytest.mark.asyncio
async def test_backfill_home_table_missing(monkeypatch):
    """Should skip gracefully when ha_entity_snapshot does not exist."""
    import asyncpg

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(side_effect=asyncpg.UndefinedTableError("ha_entity_snapshot"))

    stats = await bf.backfill_home(pool, dry_run=False)

    assert stats.processed == 0
    assert stats.errors == 0


# ---------------------------------------------------------------------------
# Stats.report smoke test
# ---------------------------------------------------------------------------


def test_stats_report(caplog):
    stats = bf.Stats()
    stats.processed = 10
    stats.inserted = 8
    stats.skipped = 2
    stats.errors = 0
    with caplog.at_level("INFO", logger="backfill_facts"):
        stats.report("health")
    assert "processed=10" in caplog.text
    assert "inserted=8" in caplog.text


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def test_parse_args_phase():
    args = bf._parse_args(["--phase", "health"])
    assert args.phase == "health"
    assert args.dry_run is False


def test_parse_args_dry_run():
    args = bf._parse_args(["--phase", "finance", "--dry-run"])
    assert args.dry_run is True


def test_parse_args_invalid_phase():
    with pytest.raises(SystemExit):
        bf._parse_args(["--phase", "invalid"])


# ---------------------------------------------------------------------------
# _main exit code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_returns_0_on_success(monkeypatch):
    monkeypatch.setattr(bf, "_run_phase", AsyncMock(return_value=bf.Stats()))
    code = await bf._main(["--phase", "home"])
    assert code == 0


@pytest.mark.asyncio
async def test_main_returns_1_on_error(monkeypatch):
    errored = bf.Stats()
    errored.errors = 1
    monkeypatch.setattr(bf, "_run_phase", AsyncMock(return_value=errored))
    code = await bf._main(["--phase", "home"])
    assert code == 1


@pytest.mark.asyncio
async def test_main_returns_1_on_exception(monkeypatch):
    monkeypatch.setattr(bf, "_run_phase", AsyncMock(side_effect=RuntimeError("boom")))
    code = await bf._main(["--phase", "home"])
    assert code == 1

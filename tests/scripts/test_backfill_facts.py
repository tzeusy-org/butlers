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
# _backfill_key and _fact_exists
# ---------------------------------------------------------------------------


def test_backfill_key():
    assert bf._backfill_key("measurements", 42) == "measurements:42"
    uid = uuid.uuid4()
    assert bf._backfill_key("symptoms", uid) == f"symptoms:{uid}"


@pytest.mark.asyncio
async def test_fact_exists(monkeypatch):
    pool_true = _pool_with_rows([], fetchrow_return={"1": 1})
    assert await bf._fact_exists(pool_true, "measurements:1") is True

    pool_false = _pool_with_rows([], fetchrow_return=None)
    assert await bf._fact_exists(pool_false, "measurements:99") is False


# ---------------------------------------------------------------------------
# _owner_entity_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_entity_id():
    eid = uuid.uuid4()

    # Resolved directly from public.entities (bu-jnaa3: single query, no
    # contacts join / fallback).
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"id": eid})
    assert await bf._owner_entity_id(pool) == eid
    assert pool.fetchrow.await_count == 1

    # Not found
    pool3 = AsyncMock()
    pool3.fetchrow = AsyncMock(return_value=None)
    assert await bf._owner_entity_id(pool3) is None


# ---------------------------------------------------------------------------
# _insert_fact — dry-run and live path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_fact_dry_run_and_live(monkeypatch):
    monkeypatch.setattr(bf, "_load_embedding_engine", lambda: _make_fake_embedding_engine())

    # Dry run: execute must not be called
    pool_dry = AsyncMock()
    await bf._insert_fact(
        pool_dry,
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
    pool_dry.execute.assert_not_called()

    # Live path: execute must be called with INSERT INTO facts
    pool_live = AsyncMock()
    pool_live.execute = AsyncMock(return_value="INSERT 0 1")
    eid = uuid.uuid4()
    await bf._insert_fact(
        pool_live,
        subject="user",
        predicate="condition_status",
        content="Hypertension, active",
        entity_id=eid,
        valid_at=datetime(2024, 1, 1, tzinfo=UTC),
        permanence="stable",
        source_butler="health",
        backfill_key="conditions:abc",
    )
    pool_live.execute.assert_called_once()
    assert "INSERT INTO facts" in pool_live.execute.call_args[0][0]


# ---------------------------------------------------------------------------
# Health phase — measurements backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_health_measurements(monkeypatch):
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

    # Happy path: inserts
    pool = _pool_with_rows(rows)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    stats = bf.Stats()
    await bf._backfill_health_measurements(pool, owner_id, stats, dry_run=False)
    assert stats.processed == 1 and stats.inserted == 1 and stats.skipped == 0

    # Skip existing fact
    pool2 = _pool_with_rows(rows)
    pool2.fetchrow = AsyncMock(return_value={"1": 1})
    pool2.execute = AsyncMock(return_value="INSERT 0 1")
    stats2 = bf.Stats()
    await bf._backfill_health_measurements(pool2, owner_id, stats2, dry_run=False)
    assert stats2.skipped == 1 and stats2.inserted == 0
    pool2.execute.assert_not_called()


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
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = bf.Stats()
    await bf._backfill_health_medications(pool, owner_id, stats, dry_run=False)

    assert stats.inserted == 1
    # valid_at should be None (property fact — 16th positional arg)
    assert pool.execute.call_args[0][16] is None


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
async def test_backfill_home(monkeypatch):
    """Happy path inserts; missing table is skipped gracefully."""
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
    call_count = [0]

    async def _fetchrow_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"id": owner_id}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow_side_effect)
    pool.fetch = AsyncMock(return_value=rows)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = await bf.backfill_home(pool, dry_run=False)
    assert stats.inserted == 1 and stats.errors == 0

    # Missing table: skip gracefully
    import asyncpg

    pool2 = AsyncMock()
    pool2.fetchrow = AsyncMock(return_value=None)
    pool2.fetch = AsyncMock(side_effect=asyncpg.UndefinedTableError("ha_entity_snapshot"))
    stats2 = await bf.backfill_home(pool2, dry_run=False)
    assert stats2.processed == 0 and stats2.errors == 0


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
# CLI argument parsing and _main exit codes
# ---------------------------------------------------------------------------


def test_parse_args():
    args_phase = bf._parse_args(["--phase", "health"])
    assert args_phase.phase == "health" and args_phase.dry_run is False

    args_dry = bf._parse_args(["--phase", "finance", "--dry-run"])
    assert args_dry.dry_run is True

    with pytest.raises(SystemExit):
        bf._parse_args(["--phase", "invalid"])


@pytest.mark.asyncio
async def test_main_exit_codes(monkeypatch):
    # Success
    monkeypatch.setattr(bf, "_run_phase", AsyncMock(return_value=bf.Stats()))
    assert await bf._main(["--phase", "home"]) == 0

    # Errors in stats
    errored = bf.Stats()
    errored.errors = 1
    monkeypatch.setattr(bf, "_run_phase", AsyncMock(return_value=errored))
    assert await bf._main(["--phase", "home"]) == 1

    # Exception
    monkeypatch.setattr(bf, "_run_phase", AsyncMock(side_effect=RuntimeError("boom")))
    assert await bf._main(["--phase", "home"]) == 1

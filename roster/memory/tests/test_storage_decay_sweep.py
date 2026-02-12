"""Tests for run_decay_sweep() in Memory Butler storage."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# Mock sentence_transformers before loading to avoid heavy dependency.
# ---------------------------------------------------------------------------

_STORAGE_PATH = __import__("pathlib").Path(__file__).resolve().parent.parent / "storage.py"


def _load_storage_module():
    st_mock = MagicMock()
    # sys.modules.setdefault("sentence_transformers", st_mock)
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
run_decay_sweep = _mod.run_decay_sweep

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Simple async context manager returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


def _make_pool(conn: AsyncMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


def _fact_row(
    *,
    confidence: float = 1.0,
    decay_rate: float = 0.008,
    days_ago: float = 0.0,
    metadata: dict | None = None,
) -> dict:
    """Build a dict mimicking an asyncpg Record for a fact row."""
    anchor = datetime.now(UTC) - timedelta(days=days_ago)
    return {
        "id": uuid.uuid4(),
        "confidence": confidence,
        "decay_rate": decay_rate,
        "last_confirmed_at": anchor,
        "created_at": anchor,
        "metadata": json.dumps(metadata or {}),
    }


def _rule_row(
    *,
    confidence: float = 0.5,
    decay_rate: float = 0.01,
    days_ago: float = 0.0,
    metadata: dict | None = None,
    last_confirmed_at: datetime | None = None,
) -> dict:
    """Build a dict mimicking an asyncpg Record for a rule row."""
    anchor = datetime.now(UTC) - timedelta(days=days_ago)
    return {
        "id": uuid.uuid4(),
        "confidence": confidence,
        "decay_rate": decay_rate,
        "last_confirmed_at": last_confirmed_at or anchor,
        "created_at": anchor,
        "metadata": json.dumps(metadata or {}),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDecaySweepEmpty:
    """Empty database returns zero stats."""

    async def test_returns_zero_stats_on_empty_db(self) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result == {
            "facts_checked": 0,
            "rules_checked": 0,
            "facts_fading": 0,
            "rules_fading": 0,
            "facts_expired": 0,
            "rules_expired": 0,
        }


class TestDecaySweepFacts:
    """Fact-specific decay sweep logic."""

    async def test_permanent_facts_skipped_by_query(self) -> None:
        """Permanent facts (decay_rate=0.0) are excluded by the SQL WHERE clause."""
        conn = AsyncMock()
        # Both calls return empty — permanent facts never reach the function
        conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        # Verify the facts query filters out decay_rate = 0
        facts_sql = conn.fetch.call_args_list[0][0][0]
        assert "decay_rate > 0.0" in facts_sql

    async def test_healthy_fact_not_affected(self) -> None:
        """A fact with recent confirmation (high eff confidence) is not modified."""
        healthy = _fact_row(confidence=1.0, decay_rate=0.008, days_ago=1.0)
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[healthy], []])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_checked"] == 1
        assert result["facts_fading"] == 0
        assert result["facts_expired"] == 0
        conn.execute.assert_not_called()

    async def test_fading_fact_gets_status(self) -> None:
        """A fact with eff confidence between 0.05 and 0.2 gets metadata.status='fading'."""
        # confidence=1.0, decay_rate=0.008 => need days where 0.05 <= eff < 0.2
        # eff = exp(-0.008 * d) => 0.2 = exp(-0.008*d) => d = -ln(0.2)/0.008 = ~201
        # eff = exp(-0.008 * d) => 0.05 = exp(-0.008*d) => d = -ln(0.05)/0.008 = ~374
        days = -math.log(0.15) / 0.008  # eff ~= 0.15
        fading = _fact_row(confidence=1.0, decay_rate=0.008, days_ago=days)
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[fading], []])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_fading"] == 1
        assert result["facts_expired"] == 0
        # Verify the metadata update
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "UPDATE facts SET metadata" in sql
        updated_metadata = json.loads(call_args[0][1])
        assert updated_metadata["status"] == "fading"

    async def test_expired_fact_gets_validity_expired(self) -> None:
        """A fact with eff confidence < 0.05 gets validity='expired'."""
        # eff = exp(-0.008 * d) < 0.05 => d > ~374 days
        days = -math.log(0.01) / 0.008  # eff ~= 0.01
        expired = _fact_row(confidence=1.0, decay_rate=0.008, days_ago=days)
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[expired], []])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_expired"] == 1
        assert result["facts_fading"] == 0
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "UPDATE facts SET validity = 'expired'" in sql

    async def test_clears_fading_when_confidence_restored(self) -> None:
        """If a fact previously had status='fading' but eff >= 0.2, clear it."""
        healthy = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=1.0, metadata={"status": "fading"}
        )
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[healthy], []])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_checked"] == 1
        assert result["facts_fading"] == 0
        # Should update metadata to remove the fading status
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "UPDATE facts SET metadata" in sql
        updated_metadata = json.loads(call_args[0][1])
        assert "status" not in updated_metadata


class TestDecaySweepRules:
    """Rule-specific decay sweep logic."""

    async def test_anti_pattern_rules_skipped_by_query(self) -> None:
        """Anti-pattern rules are excluded by the SQL WHERE clause."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        rules_sql = conn.fetch.call_args_list[1][0][0]
        assert "maturity != 'anti_pattern'" in rules_sql

    async def test_already_forgotten_rules_skipped_by_query(self) -> None:
        """Rules with metadata.forgotten=true are excluded by the SQL WHERE clause."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        rules_sql = conn.fetch.call_args_list[1][0][0]
        assert "forgotten" in rules_sql

    async def test_fading_rule_gets_status(self) -> None:
        """A rule with eff confidence between 0.05 and 0.2 gets metadata.status='fading'."""
        # confidence=0.5, decay_rate=0.01 => eff = 0.5 * exp(-0.01 * d)
        # 0.15 = 0.5 * exp(-0.01 * d) => d = -ln(0.3)/0.01 = ~120 days
        days = -math.log(0.3) / 0.01
        fading = _rule_row(confidence=0.5, decay_rate=0.01, days_ago=days)
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[], [fading]])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["rules_fading"] == 1
        assert result["rules_expired"] == 0
        call_args = conn.execute.call_args_list[0]
        updated_metadata = json.loads(call_args[0][1])
        assert updated_metadata["status"] == "fading"

    async def test_expired_rule_gets_forgotten(self) -> None:
        """A rule with eff confidence < 0.05 gets metadata.forgotten=true."""
        # confidence=0.5, decay_rate=0.01 => eff = 0.5 * exp(-0.01 * d)
        # 0.04 = 0.5 * exp(-0.01 * d) => d = -ln(0.08)/0.01 = ~252 days
        days = -math.log(0.08) / 0.01
        expired = _rule_row(confidence=0.5, decay_rate=0.01, days_ago=days)
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[], [expired]])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["rules_expired"] == 1
        assert result["rules_fading"] == 0
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "UPDATE rules SET metadata" in sql
        updated_metadata = json.loads(call_args[0][1])
        assert updated_metadata["forgotten"] is True

    async def test_healthy_rule_not_affected(self) -> None:
        """A recently confirmed rule (high eff confidence) is not modified."""
        healthy = _rule_row(confidence=0.8, decay_rate=0.01, days_ago=1.0)
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[], [healthy]])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["rules_checked"] == 1
        assert result["rules_fading"] == 0
        assert result["rules_expired"] == 0
        conn.execute.assert_not_called()

    async def test_clears_fading_when_confidence_restored_rule(self) -> None:
        """If a rule previously had status='fading' but eff >= 0.2, clear it."""
        healthy = _rule_row(
            confidence=0.8, decay_rate=0.01, days_ago=1.0, metadata={"status": "fading"}
        )
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[], [healthy]])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["rules_checked"] == 1
        assert result["rules_fading"] == 0
        call_args = conn.execute.call_args_list[0]
        updated_metadata = json.loads(call_args[0][1])
        assert "status" not in updated_metadata


class TestDecaySweepStats:
    """Stats dict is returned correctly with mixed facts and rules."""

    async def test_mixed_facts_and_rules_stats(self) -> None:
        """Run with a mix of healthy, fading, and expired facts and rules."""
        healthy_fact = _fact_row(confidence=1.0, decay_rate=0.008, days_ago=1.0)
        fading_fact = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=-math.log(0.15) / 0.008
        )
        expired_fact = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=-math.log(0.01) / 0.008
        )

        healthy_rule = _rule_row(confidence=0.8, decay_rate=0.01, days_ago=1.0)
        expired_rule = _rule_row(
            confidence=0.5, decay_rate=0.01, days_ago=-math.log(0.08) / 0.01
        )

        conn = AsyncMock()
        conn.fetch = AsyncMock(
            side_effect=[
                [healthy_fact, fading_fact, expired_fact],
                [healthy_rule, expired_rule],
            ]
        )
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_checked"] == 3
        assert result["facts_fading"] == 1
        assert result["facts_expired"] == 1
        assert result["rules_checked"] == 2
        assert result["rules_expired"] == 1
        assert result["rules_fading"] == 0

    async def test_uses_created_at_when_last_confirmed_is_none(self) -> None:
        """Falls back to created_at when last_confirmed_at is None."""
        days = -math.log(0.15) / 0.008
        anchor = datetime.now(UTC) - timedelta(days=days)
        fact = {
            "id": uuid.uuid4(),
            "confidence": 1.0,
            "decay_rate": 0.008,
            "last_confirmed_at": None,
            "created_at": anchor,
            "metadata": json.dumps({}),
        }
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[[fact], []])
        conn.execute = AsyncMock()
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_checked"] == 1
        assert result["facts_fading"] == 1

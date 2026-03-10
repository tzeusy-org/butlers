"""Tests for run_decay_sweep() in Memory Butler storage."""

from __future__ import annotations

import importlib.util
import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# Mock sentence_transformers before loading to avoid heavy dependency.
# ---------------------------------------------------------------------------

_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    # (sentence_transformers mock removed — no longer needed after refactor)
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

# Default spec-driven retention classes used in tests
_POLICY_ROWS = [
    {
        "retention_class": "transient",
        "min_retrieval_confidence": 0.1,
        "archive_before_delete": False,
    },
    {
        "retention_class": "episodic",
        "min_retrieval_confidence": 0.15,
        "archive_before_delete": False,
    },
    {
        "retention_class": "operational",
        "min_retrieval_confidence": 0.2,
        "archive_before_delete": False,
    },
    {
        "retention_class": "health_log",
        "min_retrieval_confidence": 0.1,
        "archive_before_delete": True,
    },
    {
        "retention_class": "financial_log",
        "min_retrieval_confidence": 0.1,
        "archive_before_delete": True,
    },
    {
        "retention_class": "personal_profile",
        "min_retrieval_confidence": 0.0,
        "archive_before_delete": True,
    },
    {
        "retention_class": "rule",
        "min_retrieval_confidence": 0.2,
        "archive_before_delete": False,
    },
    {
        "retention_class": "anti_pattern",
        "min_retrieval_confidence": 0.0,
        "archive_before_delete": False,
    },
]


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
    retention_class: str = "operational",
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
        "retention_class": retention_class,
    }


def _rule_row(
    *,
    confidence: float = 0.5,
    decay_rate: float = 0.01,
    days_ago: float = 0.0,
    metadata: dict | None = None,
    last_confirmed_at: datetime | None = None,
    retention_class: str = "rule",
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
        "retention_class": retention_class,
    }


def _make_conn_with_policies(
    facts: list | None = None,
    rules: list | None = None,
    policy_rows: list | None = None,
) -> AsyncMock:
    """Create a mock connection that returns policy rows first, then facts, then rules."""
    conn = AsyncMock()
    _policy_rows = policy_rows if policy_rows is not None else _POLICY_ROWS
    _facts = facts if facts is not None else []
    _rules = rules if rules is not None else []
    # fetch() is called three times: policies, facts, rules
    conn.fetch = AsyncMock(side_effect=[_policy_rows, _facts, _rules])
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDecaySweepEmpty:
    """Empty database returns zero stats."""

    async def test_returns_zero_stats_on_empty_db(self) -> None:
        conn = _make_conn_with_policies()
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


class TestDecaySweepPolicyLoading:
    """Verify policy rows are loaded from memory_policies before processing."""

    async def test_policy_sql_queries_correct_columns(self) -> None:
        conn = _make_conn_with_policies()
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        policy_sql = conn.fetch.call_args_list[0][0][0]
        assert "min_retrieval_confidence" in policy_sql
        assert "archive_before_delete" in policy_sql
        assert "memory_policies" in policy_sql

    async def test_facts_query_includes_retention_class(self) -> None:
        conn = _make_conn_with_policies()
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        facts_sql = conn.fetch.call_args_list[1][0][0]
        assert "retention_class" in facts_sql
        assert "decay_rate > 0.0" in facts_sql

    async def test_rules_query_includes_retention_class(self) -> None:
        conn = _make_conn_with_policies()
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        rules_sql = conn.fetch.call_args_list[2][0][0]
        assert "retention_class" in rules_sql
        assert "maturity != 'anti_pattern'" in rules_sql


class TestDecaySweepFacts:
    """Fact-specific decay sweep logic with policy-driven thresholds."""

    async def test_permanent_facts_skipped_by_query(self) -> None:
        """Permanent facts (decay_rate=0.0) are excluded by the SQL WHERE clause."""
        conn = _make_conn_with_policies()
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        # Verify the facts query filters out decay_rate = 0
        facts_sql = conn.fetch.call_args_list[1][0][0]
        assert "decay_rate > 0.0" in facts_sql

    async def test_healthy_fact_not_affected(self) -> None:
        """A fact with recent confirmation (high eff confidence) is not modified."""
        # operational policy: min_retrieval_confidence=0.2, so fading threshold is 0.2
        healthy = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=1.0, retention_class="operational"
        )
        conn = _make_conn_with_policies(facts=[healthy])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_checked"] == 1
        assert result["facts_fading"] == 0
        assert result["facts_expired"] == 0
        conn.execute.assert_not_called()

    async def test_fading_fact_gets_status_operational(self) -> None:
        """A fact with eff confidence between expiry and fading thresholds gets status='fading'.

        operational policy: fading=0.2, expiry=0.2*0.25=0.05
        eff = exp(-0.008 * d) => need 0.05 <= eff < 0.2
        """
        # eff = 1.0 * exp(-0.008 * d) = 0.15 => d = -ln(0.15)/0.008 ~= ~231 days
        days = -math.log(0.15) / 0.008
        fading = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=days, retention_class="operational"
        )
        conn = _make_conn_with_policies(facts=[fading])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_fading"] == 1
        assert result["facts_expired"] == 0
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "UPDATE facts SET metadata" in sql
        updated_metadata = json.loads(call_args[0][1])
        assert updated_metadata["status"] == "fading"

    async def test_expired_fact_gets_validity_expired(self) -> None:
        """A fact with eff confidence < expiry threshold gets validity='expired'.

        operational policy: expiry=0.2*0.25=0.05
        """
        # eff = 1.0 * exp(-0.008 * d) < 0.05 => d > ~374 days
        days = -math.log(0.01) / 0.008  # eff ~= 0.01
        expired = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=days, retention_class="operational"
        )
        conn = _make_conn_with_policies(facts=[expired])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_expired"] == 1
        assert result["facts_fading"] == 0
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "UPDATE facts SET validity = 'expired'" in sql

    async def test_clears_fading_when_confidence_restored(self) -> None:
        """If a fact previously had status='fading' but eff >= fading threshold, clear it."""
        healthy = _fact_row(
            confidence=1.0,
            decay_rate=0.008,
            days_ago=1.0,
            metadata={"status": "fading"},
            retention_class="operational",
        )
        conn = _make_conn_with_policies(facts=[healthy])
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


class TestDecaySweepPolicyDrivenThresholds:
    """Policy thresholds are applied per-class, not with global hardcoded values."""

    async def test_transient_class_uses_its_own_thresholds(self) -> None:
        """transient policy: min_retrieval_confidence=0.1, fading=0.1, expiry=0.025."""
        # eff = 0.08: should be fading (0.025 <= 0.08 < 0.1)
        days = -math.log(0.08) / 0.1
        fading = _fact_row(
            confidence=1.0, decay_rate=0.1, days_ago=days, retention_class="transient"
        )
        conn = _make_conn_with_policies(facts=[fading])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_fading"] == 1
        assert result["facts_expired"] == 0

    async def test_episodic_class_uses_its_own_thresholds(self) -> None:
        """episodic policy: min_retrieval_confidence=0.15, fading=0.15, expiry=0.0375."""
        # eff = 0.1: should be fading (0.0375 <= 0.1 < 0.15)
        days = -math.log(0.1 / 1.0) / 0.03
        fading = _fact_row(
            confidence=1.0, decay_rate=0.03, days_ago=days, retention_class="episodic"
        )
        conn = _make_conn_with_policies(facts=[fading])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_fading"] == 1
        assert result["facts_expired"] == 0

    async def test_unknown_class_falls_back_to_hardcoded_defaults(self) -> None:
        """When retention_class is not in memory_policies, use hardcoded 0.2/0.05."""
        # eff = 0.15: fading under hardcoded defaults (0.05 <= 0.15 < 0.2)
        days = -math.log(0.15) / 0.008
        fading = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=days, retention_class="unknown_class"
        )
        conn = _make_conn_with_policies(facts=[fading])
        pool = _make_pool(conn)

        with patch.object(_mod.logger, "warning") as mock_warn:
            result = await run_decay_sweep(pool)

        assert result["facts_fading"] == 1
        mock_warn.assert_called_once()
        warn_msg = mock_warn.call_args[0][0]
        assert "not found in memory_policies" in warn_msg

    async def test_unknown_class_below_hardcoded_expiry_expires(self) -> None:
        """eff < 0.05 for unknown class triggers expiry with hardcoded fallback."""
        days = -math.log(0.01) / 0.008
        expired = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=days, retention_class="unknown_class"
        )
        conn = _make_conn_with_policies(facts=[expired])
        pool = _make_pool(conn)

        with patch.object(_mod.logger, "warning"):
            result = await run_decay_sweep(pool)

        assert result["facts_expired"] == 1


class TestDecaySweepArchiveBeforeDelete:
    """archive_before_delete=true causes archival metadata to be set before expiry."""

    async def test_health_log_fact_archived_before_expiry(self) -> None:
        """health_log has archive_before_delete=True; metadata gets archived_at before expiry."""
        # health_log: min_conf=0.1, expiry=0.1*0.25=0.025
        # eff < 0.025 triggers expiry path
        days = -math.log(0.01) / 0.002  # eff ~= 0.01
        expired = _fact_row(
            confidence=1.0, decay_rate=0.002, days_ago=days, retention_class="health_log"
        )
        conn = _make_conn_with_policies(facts=[expired])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_expired"] == 1
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        # For archive_before_delete, the UPDATE includes both validity=expired AND metadata
        assert "UPDATE facts SET validity = 'expired'" in sql
        assert "metadata" in sql
        updated_metadata = json.loads(call_args[0][1])
        assert "archived_at" in updated_metadata
        assert updated_metadata["archived_content"] is True

    async def test_non_archive_class_skips_archival(self) -> None:
        """operational has archive_before_delete=False; direct expiry without archival metadata."""
        days = -math.log(0.01) / 0.008
        expired = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=days, retention_class="operational"
        )
        conn = _make_conn_with_policies(facts=[expired])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_expired"] == 1
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "UPDATE facts SET validity = 'expired' WHERE id = $1" == sql.strip()

    async def test_archival_failure_skips_expiry(self) -> None:
        """If archival fails for archive_before_delete class, fact is NOT expired (fail-closed)."""
        # health_log: archive_before_delete=True
        days = -math.log(0.01) / 0.002
        expired = _fact_row(
            confidence=1.0, decay_rate=0.002, days_ago=days, retention_class="health_log"
        )
        conn = _make_conn_with_policies(facts=[expired])
        conn.execute = AsyncMock(side_effect=Exception("DB write failed"))
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        # Fail-closed: fact is NOT counted as expired
        assert result["facts_expired"] == 0
        assert result["facts_fading"] == 0


class TestDecaySweepRules:
    """Rule-specific decay sweep logic."""

    async def test_anti_pattern_rules_skipped_by_query(self) -> None:
        """Anti-pattern rules are excluded by the SQL WHERE clause."""
        conn = _make_conn_with_policies()
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        rules_sql = conn.fetch.call_args_list[2][0][0]
        assert "maturity != 'anti_pattern'" in rules_sql

    async def test_already_forgotten_rules_skipped_by_query(self) -> None:
        """Rules with metadata.forgotten=true are excluded by the SQL WHERE clause."""
        conn = _make_conn_with_policies()
        pool = _make_pool(conn)

        await run_decay_sweep(pool)

        rules_sql = conn.fetch.call_args_list[2][0][0]
        assert "forgotten" in rules_sql

    async def test_fading_rule_gets_status(self) -> None:
        """A rule with eff confidence between expiry and fading thresholds gets status='fading'.

        rule policy: min_retrieval_confidence=0.2, fading=0.2, expiry=0.05
        """
        # confidence=0.5, decay_rate=0.01 => eff = 0.5 * exp(-0.01 * d)
        # 0.15 = 0.5 * exp(-0.01 * d) => d = -ln(0.3)/0.01 = ~120 days
        days = -math.log(0.3) / 0.01
        fading = _rule_row(confidence=0.5, decay_rate=0.01, days_ago=days, retention_class="rule")
        conn = _make_conn_with_policies(rules=[fading])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["rules_fading"] == 1
        assert result["rules_expired"] == 0
        call_args = conn.execute.call_args_list[0]
        updated_metadata = json.loads(call_args[0][1])
        assert updated_metadata["status"] == "fading"

    async def test_expired_rule_gets_forgotten(self) -> None:
        """A rule with eff confidence < expiry threshold gets metadata.forgotten=true.

        rule policy: expiry threshold = 0.2 * 0.25 = 0.05
        """
        # confidence=0.5, decay_rate=0.01 => eff = 0.5 * exp(-0.01 * d)
        # 0.04 = 0.5 * exp(-0.01 * d) => d = -ln(0.08)/0.01 = ~252 days
        days = -math.log(0.08) / 0.01
        expired = _rule_row(confidence=0.5, decay_rate=0.01, days_ago=days, retention_class="rule")
        conn = _make_conn_with_policies(rules=[expired])
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
        healthy = _rule_row(confidence=0.8, decay_rate=0.01, days_ago=1.0, retention_class="rule")
        conn = _make_conn_with_policies(rules=[healthy])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["rules_checked"] == 1
        assert result["rules_fading"] == 0
        assert result["rules_expired"] == 0
        conn.execute.assert_not_called()

    async def test_clears_fading_when_confidence_restored_rule(self) -> None:
        """If a rule previously had status='fading' but eff >= fading threshold, clear it."""
        healthy = _rule_row(
            confidence=0.8,
            decay_rate=0.01,
            days_ago=1.0,
            metadata={"status": "fading"},
            retention_class="rule",
        )
        conn = _make_conn_with_policies(rules=[healthy])
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
        # operational: fading=0.2, expiry=0.05
        healthy_fact = _fact_row(
            confidence=1.0, decay_rate=0.008, days_ago=1.0, retention_class="operational"
        )
        fading_fact = _fact_row(
            confidence=1.0,
            decay_rate=0.008,
            days_ago=-math.log(0.15) / 0.008,
            retention_class="operational",
        )
        expired_fact = _fact_row(
            confidence=1.0,
            decay_rate=0.008,
            days_ago=-math.log(0.01) / 0.008,
            retention_class="operational",
        )
        # rule: fading=0.2, expiry=0.05
        healthy_rule = _rule_row(
            confidence=0.8, decay_rate=0.01, days_ago=1.0, retention_class="rule"
        )
        expired_rule = _rule_row(
            confidence=0.5, decay_rate=0.01, days_ago=-math.log(0.08) / 0.01, retention_class="rule"
        )

        conn = _make_conn_with_policies(
            facts=[healthy_fact, fading_fact, expired_fact],
            rules=[healthy_rule, expired_rule],
        )
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
        # operational: fading threshold=0.2, so eff=0.15 => fading
        days = -math.log(0.15) / 0.008
        anchor = datetime.now(UTC) - timedelta(days=days)
        fact = {
            "id": uuid.uuid4(),
            "confidence": 1.0,
            "decay_rate": 0.008,
            "last_confirmed_at": None,
            "created_at": anchor,
            "metadata": json.dumps({}),
            "retention_class": "operational",
        }
        conn = _make_conn_with_policies(facts=[fact])
        pool = _make_pool(conn)

        result = await run_decay_sweep(pool)

        assert result["facts_checked"] == 1
        assert result["facts_fading"] == 1

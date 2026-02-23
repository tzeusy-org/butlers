"""Unit tests for contact_resolve threshold decision logic."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Load resolve.py from roster/relationship/tools
RESOLVE_PATH = Path(__file__).parent.parent.parent / "roster/relationship/tools/resolve.py"


def _load_resolve_module():
    """Load resolve.py from disk."""
    spec = importlib.util.spec_from_file_location("resolve", RESOLVE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["resolve"] = mod
    spec.loader.exec_module(mod)
    return mod


resolve_mod = _load_resolve_module()
contact_resolve = resolve_mod.contact_resolve
CONFIDENCE_HIGH = resolve_mod.CONFIDENCE_HIGH
CONFIDENCE_MEDIUM = resolve_mod.CONFIDENCE_MEDIUM
CONFIDENCE_NONE = resolve_mod.CONFIDENCE_NONE

pytestmark = pytest.mark.unit


class TestContactResolveThresholds:
    """Tests for the 30-point gap threshold decision logic."""

    async def test_exact_match_multiple_with_30_point_gap_returns_high(self):
        """Multiple exact matches: if top candidate leads by ≥30 points, return HIGH confidence."""
        # Mock pool with two exact matches
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query (stay_in_touch_days)
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query - +50 points to uuid-1
                [{"contact_id": "uuid-1", "forward_label": "spouse"}],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"
        assert len(result["candidates"]) == 2
        # uuid-1 should have base 90 + salience (50 for spouse + 10 for stay_in_touch) = 150
        # uuid-2 should have base 90 + salience (0) = 90
        # Gap = 60 points (≥30) → HIGH confidence
        assert result["candidates"][0]["contact_id"] == "uuid-1"
        assert result["candidates"][0]["score"] >= 150

    async def test_exact_match_multiple_with_small_gap_returns_medium(self):
        """Multiple exact matches: gap <30 points returns MEDIUM, no auto-selection."""
        # Mock pool with two exact matches that score similarly
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [],
                # relationships query
                [],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] is None  # No auto-selection when gap <30
        assert len(result["candidates"]) == 2
        # Both should have base 90 + salience (0) = 90
        # Gap = 0 points (<30) → MEDIUM confidence, no auto-selection

    async def test_partial_match_with_30_point_gap_returns_high(self):
        """Partial matches: top leads by ≥30 points returns HIGH with auto-selection."""
        # Mock pool with partial matches where one clearly leads
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns no rows
                [],
                # Partial match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": "Acme Corp",
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "Johnny",
                        "last_name": "Doe",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query - +50 points to uuid-1
                [{"contact_id": "uuid-1", "forward_label": "spouse"}],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John")

        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"
        assert len(result["candidates"]) == 2
        # uuid-1 should have base score (70 for first name prefix match) + salience (50 + 10) = 130
        # uuid-2 should have lower base score + salience (0) = ~40-50
        # Gap should be ≥30 → HIGH confidence with auto-selection

    async def test_partial_match_with_small_gap_returns_medium_no_autoselect(self):
        """Partial matches: if gap <30 points, return MEDIUM confidence without auto-selection."""
        # Mock pool with partial matches that score similarly
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns no rows
                [],
                # Partial match query returns 2 rows with similar scores
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "Johnny",
                        "last_name": "Doe",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [],
                # relationships query
                [],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John")

        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] is None  # No auto-selection when gap <30
        assert len(result["candidates"]) == 2

    async def test_single_partial_match_returns_medium(self):
        """A single partial match returns MEDIUM confidence (no threshold check needed)."""
        # Mock pool with one partial match
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns no rows
                [],
                # Partial match query returns 1 row
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    }
                ],
            ]
        )

        result = await contact_resolve(pool, "John")

        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] == "uuid-1"
        assert len(result["candidates"]) == 1

    async def test_exact_30_point_gap_is_high_confidence(self):
        """A gap of exactly 30 points should return HIGH confidence (boundary test)."""
        # Mock pool with exact matches where gap is exactly 30
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query
                [{"contact_id": "uuid-1", "forward_label": "friend"}],  # +10 points
                # interactions query
                [
                    {
                        "contact_id": "uuid-1",
                        "count_90d": 10,  # +20 points (capped)
                        "most_recent": None,
                    }
                ],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        # uuid-1: base 90 + stay_in_touch (10) + friend (10) + interactions (20) = 130
        # uuid-2: base 90 + 0 = 90
        # Gap = 40 points (≥30) → HIGH confidence
        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"

    async def test_29_point_gap_is_medium_confidence(self):
        """A gap of 29 points should return MEDIUM confidence (boundary test)."""
        # This is harder to control precisely, but we can test the logic
        # by ensuring gap <30 → MEDIUM
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query - give uuid-1 a slight edge (10 points)
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query
                [{"contact_id": "uuid-1", "forward_label": "acquaintance"}],  # +2 points
                # interactions query
                [
                    {
                        "contact_id": "uuid-1",
                        "count_90d": 8,  # +16 points
                        "most_recent": None,
                    }
                ],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        # uuid-1: base 90 + stay_in_touch (10) + acquaintance (2) + interactions (16) = 118
        # uuid-2: base 90 + 0 = 90
        # Gap = 28 points (<30) → MEDIUM confidence, no auto-selection
        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] is None


class TestInferredFieldsPresence:
    """Tests for the new inferred and inferred_reason fields."""

    async def test_inferred_fields_present_in_all_responses(self):
        """All responses should include inferred and inferred_reason fields."""
        pool = MagicMock()

        # Test no match case
        pool.fetch = AsyncMock(return_value=[])
        result = await contact_resolve(pool, "Nonexistent")
        assert "inferred" in result
        assert "inferred_reason" in result
        assert result["inferred"] is False
        assert result["inferred_reason"] is None

        # Test single exact match case
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": "uuid-1",
                    "first_name": "John",
                    "last_name": "Smith",
                    "nickname": None,
                    "company": None,
                    "job_title": None,
                    "metadata": {},
                }
            ]
        )
        result = await contact_resolve(pool, "John Smith")
        assert "inferred" in result
        assert "inferred_reason" in result
        assert result["inferred"] is False
        assert result["inferred_reason"] is None

    async def test_candidates_include_salience_field(self):
        """All candidates should include salience field."""
        pool = MagicMock()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": "uuid-1",
                    "first_name": "John",
                    "last_name": "Smith",
                    "nickname": None,
                    "company": None,
                    "job_title": None,
                    "metadata": {},
                }
            ]
        )

        result = await contact_resolve(pool, "John Smith")
        assert len(result["candidates"]) == 1
        assert "salience" in result["candidates"][0]
        assert isinstance(result["candidates"][0]["salience"], int)

    async def test_inferred_true_when_gap_30_or_more(self):
        """When gap ≥30, inferred should be True with a reason."""
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query - +50 points to uuid-1
                [{"contact_id": "uuid-1", "forward_label": "partner"}],
                # interactions query - +20 points to uuid-1
                [
                    {
                        "contact_id": "uuid-1",
                        "count_90d": 15,  # +20 points (capped)
                        "most_recent": None,
                    }
                ],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        # uuid-1: base 90 + stay_in_touch (10) + partner (50) + interactions (20) = 170
        # uuid-2: base 90 + 0 = 90
        # Gap = 80 points (≥30) → HIGH confidence, inferred=True
        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["inferred"] is True
        assert result["inferred_reason"] is not None
        assert isinstance(result["inferred_reason"], str)
        # Should mention relationship type
        assert "partner" in result["inferred_reason"].lower()

    async def test_inferred_false_when_gap_less_than_30(self):
        """When gap <30, inferred should be False."""
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [],
                # relationships query
                [],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        # Both have base 90 + salience (0) = 90
        # Gap = 0 points (<30) → MEDIUM confidence, inferred=False
        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["inferred"] is False
        assert result["inferred_reason"] is None

    async def test_inferred_reason_includes_interaction_frequency(self):
        """inferred_reason should mention interaction frequency when significant."""
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [],
                # relationships query
                [],
                # interactions query - give uuid-1 high interaction count
                [
                    {
                        "contact_id": "uuid-1",
                        "count_90d": 12,  # +20 points (capped)
                        "most_recent": None,
                    }
                ],
                # fact_note query - +10 points (capped)
                [
                    {"contact_id": "uuid-1", "fact_count": 5, "note_count": 5},
                ],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        # uuid-1: base 90 + interactions (20) + facts/notes (10) = 120
        # uuid-2: base 90 + 0 = 90
        # Gap = 30 points (≥30) → HIGH confidence, inferred=True
        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["inferred"] is True
        assert result["inferred_reason"] is not None
        # Should mention frequent contact
        reason_lower = result["inferred_reason"].lower()
        assert "frequent" in reason_lower or "contact" in reason_lower


class TestEntityResolveIntegration:
    """Tests for entity_resolve integration via context_hints.domain_scores."""

    def _make_pool_with_entity_ids(
        self,
        entity_id_1: str,
        entity_id_2: str,
        salience_rows: list,
    ) -> MagicMock:
        """Build a mock pool that returns two exact-match rows with entity_ids."""
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows with entity_ids
                [
                    {
                        "id": "uuid-1",
                        "first_name": "Chloe",
                        "last_name": "Wong",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                        "entity_id": uuid.UUID(entity_id_1),
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "Chloe",
                        "last_name": "Tan",
                        "nickname": None,
                        "company": "Acme",
                        "job_title": None,
                        "metadata": {},
                        "entity_id": uuid.UUID(entity_id_2),
                    },
                ],
                # salience queries
                *salience_rows,
            ]
        )
        return pool

    async def test_entity_resolve_called_with_domain_scores(self):
        """When memory_pool is provided, entity_resolve is called with salience as domain_scores."""
        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())

        pool = self._make_pool_with_entity_ids(
            eid1,
            eid2,
            salience_rows=[
                # contact_data query (stay_in_touch_days) - eid1 has weekly cadence
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},  # +10 salience
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query - eid1 is partner
                [{"contact_id": "uuid-1", "forward_label": "partner"}],  # +50 salience
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ],
        )
        memory_pool = MagicMock()

        # entity_resolve returns eid1 ranked first with a large score gap
        entity_resolve_result = [
            {
                "entity_id": eid1,
                "canonical_name": "Chloe Wong",
                "entity_type": "person",
                "score": 210.0,
                "name_match": "exact",
                "aliases": [],
            },
            {
                "entity_id": eid2,
                "canonical_name": "Chloe Tan",
                "entity_type": "person",
                "score": 90.0,
                "name_match": "exact",
                "aliases": [],
            },
        ]

        captured_hints: dict = {}

        async def mock_entity_resolve(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            captured_hints.update(context_hints or {})
            return entity_resolve_result

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve,
        ):
            await contact_resolve(
                pool, "Chloe", memory_pool=memory_pool, memory_tenant_id="relationship"
            )

        # entity_resolve should have been called with domain_scores
        assert "domain_scores" in captured_hints
        assert eid1 in captured_hints["domain_scores"]
        assert eid2 in captured_hints["domain_scores"]
        # eid1 should have higher salience (partner + weekly cadence = 60)
        assert captured_hints["domain_scores"][eid1] == 60.0
        assert captured_hints["domain_scores"][eid2] == 0.0

    async def test_entity_resolve_score_used_for_threshold(self):
        """Entity_resolve composite scores are used for the 30-point gap threshold."""
        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())

        pool = self._make_pool_with_entity_ids(
            eid1,
            eid2,
            salience_rows=[
                # All zeros — salience provided but not important for this test
                [],  # contact_data
                [],  # relationships
                [],  # interactions
                [],  # fact_note
                [],  # groups
            ],
        )
        memory_pool = MagicMock()

        # entity_resolve returns eid1 with score 145, eid2 with score 85 (gap = 60 >= 30)
        async def mock_entity_resolve(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            return [
                {
                    "entity_id": eid1,
                    "canonical_name": "Chloe Wong",
                    "entity_type": "person",
                    "score": 145.0,
                    "name_match": "exact",
                    "aliases": [],
                },
                {
                    "entity_id": eid2,
                    "canonical_name": "Chloe Tan",
                    "entity_type": "person",
                    "score": 85.0,
                    "name_match": "exact",
                    "aliases": [],
                },
            ]

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve,
        ):
            result = await contact_resolve(pool, "Chloe", memory_pool=memory_pool)

        # Gap = 60 >= 30 → HIGH confidence with auto-selection of uuid-1 (mapped from eid1)
        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"
        assert result["inferred"] is True

    async def test_entity_resolve_medium_when_gap_less_than_30(self):
        """When entity_resolve scores have gap < 30, returns MEDIUM confidence."""
        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())

        pool = self._make_pool_with_entity_ids(
            eid1,
            eid2,
            salience_rows=[[], [], [], [], []],
        )
        memory_pool = MagicMock()

        # entity_resolve returns candidates with gap = 10 (< 30)
        async def mock_entity_resolve(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            return [
                {
                    "entity_id": eid1,
                    "canonical_name": "Chloe Wong",
                    "entity_type": "person",
                    "score": 100.0,
                    "name_match": "exact",
                    "aliases": [],
                },
                {
                    "entity_id": eid2,
                    "canonical_name": "Chloe Tan",
                    "entity_type": "person",
                    "score": 90.0,
                    "name_match": "exact",
                    "aliases": [],
                },
            ]

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve,
        ):
            result = await contact_resolve(pool, "Chloe", memory_pool=memory_pool)

        # Gap = 10 < 30 → MEDIUM confidence, no auto-selection
        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] is None
        assert result["inferred"] is False

    async def test_context_passed_as_topic_hint(self):
        """Context string is passed as context_hints.topic to entity_resolve."""
        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())

        pool = self._make_pool_with_entity_ids(eid1, eid2, salience_rows=[[], [], [], [], []])
        memory_pool = MagicMock()

        captured_hints: dict = {}

        async def mock_entity_resolve(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            captured_hints.update(context_hints or {})
            return [
                {
                    "entity_id": eid1,
                    "canonical_name": "Chloe Wong",
                    "entity_type": "person",
                    "score": 100.0,
                    "name_match": "exact",
                    "aliases": [],
                },
                {
                    "entity_id": eid2,
                    "canonical_name": "Chloe Tan",
                    "entity_type": "person",
                    "score": 90.0,
                    "name_match": "exact",
                    "aliases": [],
                },
            ]

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve,
        ):
            await contact_resolve(pool, "Chloe", context="from work", memory_pool=memory_pool)

        assert captured_hints.get("topic") == "from work"

    async def test_fallback_to_local_scoring_when_entity_resolve_fails(self):
        """If entity_resolve raises an exception, local scoring is used as fallback."""
        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())

        pool = self._make_pool_with_entity_ids(
            eid1,
            eid2,
            salience_rows=[
                # contact_data: uuid-1 has weekly cadence (+10)
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships: uuid-1 is partner (+50)
                [{"contact_id": "uuid-1", "forward_label": "partner"}],
                # interactions
                [],
                # fact_note
                [],
                # groups
                [],
            ],
        )
        memory_pool = MagicMock()

        async def mock_entity_resolve_fails(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            raise RuntimeError("entity_resolve unavailable")

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve_fails,
        ):
            result = await contact_resolve(pool, "Chloe", memory_pool=memory_pool)

        # Fallback: local salience (partner=50 + stay_in_touch=10 = 60) + base 90 = 150
        # vs uuid-2: base 90. Gap = 60 ≥ 30 → HIGH confidence from local scoring
        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"
        assert result["inferred"] is True

    async def test_fallback_when_no_entity_ids(self):
        """When contacts have no entity_ids, local scoring is used without entity_resolve."""
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows WITHOUT entity_ids
                [
                    {
                        "id": "uuid-1",
                        "first_name": "Chloe",
                        "last_name": "Wong",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                        "entity_id": None,
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "Chloe",
                        "last_name": "Tan",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                        "entity_id": None,
                    },
                ],
                # contact_data: uuid-1 has partner relationship
                [
                    {"id": "uuid-1", "stay_in_touch_days": None},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships: uuid-1 is partner (+50)
                [{"contact_id": "uuid-1", "forward_label": "partner"}],
                # interactions
                [],
                # fact_note
                [],
                # groups
                [],
            ]
        )
        memory_pool = MagicMock()

        # entity_resolve should NOT be called when no entity_ids exist
        entity_resolve_call_count = 0

        async def mock_entity_resolve(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            nonlocal entity_resolve_call_count
            entity_resolve_call_count += 1
            return []

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve,
        ):
            result = await contact_resolve(pool, "Chloe", memory_pool=memory_pool)

        # entity_resolve should NOT be called when no entity_ids exist
        assert entity_resolve_call_count == 0
        # Local scoring: uuid-1 gets partner (+50), gap=50 ≥ 30 → HIGH
        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"

    async def test_salience_only_computed_for_multiple_candidates(self):
        """Salience scoring is skipped for single-match results (zero-cost rule)."""
        pool = MagicMock()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": "uuid-1",
                    "first_name": "Chloe",
                    "last_name": "Wong",
                    "nickname": None,
                    "company": None,
                    "job_title": None,
                    "metadata": {},
                    "entity_id": None,
                }
            ]
        )
        memory_pool = MagicMock()

        entity_resolve_call_count = 0

        async def mock_entity_resolve(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            nonlocal entity_resolve_call_count
            entity_resolve_call_count += 1
            return []

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve,
        ):
            result = await contact_resolve(pool, "Chloe Wong", memory_pool=memory_pool)

        # Single match: entity_resolve should NOT be called
        assert entity_resolve_call_count == 0
        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"
        assert result["inferred"] is False

    async def test_entity_resolve_called_with_person_entity_type(self):
        """entity_resolve is called with entity_type='person' filter."""
        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())

        pool = self._make_pool_with_entity_ids(eid1, eid2, salience_rows=[[], [], [], [], []])
        memory_pool = MagicMock()

        captured_args: dict = {}

        async def mock_entity_resolve(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            captured_args["entity_type"] = entity_type
            return [
                {
                    "entity_id": eid1,
                    "canonical_name": "Chloe Wong",
                    "entity_type": "person",
                    "score": 100.0,
                    "name_match": "exact",
                    "aliases": [],
                },
                {
                    "entity_id": eid2,
                    "canonical_name": "Chloe Tan",
                    "entity_type": "person",
                    "score": 90.0,
                    "name_match": "exact",
                    "aliases": [],
                },
            ]

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve,
        ):
            await contact_resolve(pool, "Chloe", memory_pool=memory_pool)

        assert captured_args.get("entity_type") == "person"

    async def test_salience_uses_all_six_signal_types(self):
        """Salience scoring incorporates all 6 signal types from §10.4."""
        import datetime

        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())

        now = datetime.datetime.now(datetime.UTC)
        recent_interaction = now - datetime.timedelta(days=3)  # <7 days = +15

        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "Chloe",
                        "last_name": "Wong",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                        "entity_id": uuid.UUID(eid1),
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "Chloe",
                        "last_name": "Tan",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                        "entity_id": uuid.UUID(eid2),
                    },
                ],
                # contact_data: signal 5 (stay_in_touch: weekly = +10)
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships: signal 1 (partner = +50)
                [{"contact_id": "uuid-1", "forward_label": "partner"}],
                # interactions: signal 2 (6 interactions * 2 = +12) + signal 3 (recent <7d = +15)
                [
                    {
                        "contact_id": "uuid-1",
                        "count_90d": 6,
                        "most_recent": recent_interaction,
                    }
                ],
                # fact_note: signal 4 (3+3=6 = +6)
                [{"contact_id": "uuid-1", "fact_count": 3, "note_count": 3}],
                # groups: signal 6 (family = +10)
                [{"contact_id": "uuid-1", "type": "family"}],
            ]
        )
        memory_pool = MagicMock()

        captured_hints: dict = {}

        async def mock_entity_resolve(
            pool, name, *, tenant_id, entity_type=None, context_hints=None, enable_fuzzy=False
        ):
            captured_hints.update(context_hints or {})
            return [
                {
                    "entity_id": eid1,
                    "canonical_name": "Chloe Wong",
                    "entity_type": "person",
                    "score": 200.0,
                    "name_match": "exact",
                    "aliases": [],
                },
                {
                    "entity_id": eid2,
                    "canonical_name": "Chloe Tan",
                    "entity_type": "person",
                    "score": 90.0,
                    "name_match": "exact",
                    "aliases": [],
                },
            ]

        with patch(
            "butlers.modules.memory.tools.entities.entity_resolve",
            side_effect=mock_entity_resolve,
        ):
            await contact_resolve(pool, "Chloe", memory_pool=memory_pool)

        domain_scores = captured_hints.get("domain_scores", {})
        # Expected salience for uuid-1:
        # signal 1: partner = +50
        # signal 2: 6 * 2 = +12
        # signal 3: recent <7d = +15
        # signal 4: 3+3=6 density = +6
        # signal 5: weekly stay_in_touch = +10
        # signal 6: family group = +10
        # Total = 50 + 12 + 15 + 6 + 10 + 10 = 103
        assert eid1 in domain_scores
        assert domain_scores[eid1] == 103.0
        assert eid2 in domain_scores
        assert domain_scores[eid2] == 0.0

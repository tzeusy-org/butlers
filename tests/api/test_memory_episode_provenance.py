"""API projection regressions for expired source-episode provenance."""

from __future__ import annotations

from butlers.api.routers.memory import _row_to_fact, _row_to_rule


def test_fact_and_rule_projection_expose_expired_source_state() -> None:
    """Durable rows keep a deleted source visible to API consumers."""
    fact = _row_to_fact(
        {
            "id": "fact-1",
            "subject": "owner",
            "predicate": "preference",
            "content": "durable fact",
            "importance": 1.0,
            "confidence": 1.0,
            "decay_rate": 0.0,
            "permanence": "standard",
            "source_butler": "general",
            "source_episode_id": "episode-1",
            "source_episode_status": "expired",
            "supersedes_id": None,
            "entity_id": None,
            "object_entity_id": None,
            "validity": "active",
            "scope": "global",
            "reference_count": 0,
            "created_at": "2026-07-29T00:00:00Z",
            "last_referenced_at": None,
            "last_confirmed_at": None,
            "tags": [],
            "metadata": {},
        }
    )
    rule = _row_to_rule(
        {
            "id": "rule-1",
            "content": "durable rule",
            "scope": "global",
            "maturity": "candidate",
            "confidence": 0.5,
            "decay_rate": 0.0,
            "permanence": "standard",
            "effectiveness_score": 0.0,
            "applied_count": 0,
            "success_count": 0,
            "harmful_count": 0,
            "source_episode_id": "episode-1",
            "source_episode_status": "expired",
            "source_butler": "general",
            "created_at": "2026-07-29T00:00:00Z",
            "last_applied_at": None,
            "last_evaluated_at": None,
            "tags": [],
            "metadata": {},
        }
    )

    assert fact.source_episode_id == "episode-1"
    assert fact.source_episode_status == "expired"
    assert rule.source_episode_id == "episode-1"
    assert rule.source_episode_status == "expired"

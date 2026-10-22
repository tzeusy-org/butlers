"""Dark source value controls, not proof of a durable/verified producer adapter.

REQ-capability-authorization-013, REQ-goal-orchestration-007.
"""

from dataclasses import replace
from uuid import UUID

import pytest

from butlers.core.goal_admission import Grant
from butlers.core.obligation_sources import ObligationSource, RuntimeCreationSnapshot
from butlers.core.obligations import DeclarationError, parse_declaration

pytestmark = pytest.mark.unit


def test_runtime_ceiling_refuses_capability_resource_and_park_only_widening():
    creator = RuntimeCreationSnapshot(
        UUID(int=1), (Grant("health__read", (("entity_id", ("a", "b")),), True),), True
    )
    declaration = parse_declaration(
        {
            "version": 1,
            "capabilities": [
                {"capability": "health__read", "resources": {"entity_id": ["a"]}, "park_only": True}
            ],
        }
    )
    source = ObligationSource("health", "schedule", UUID(int=2), "a" * 64, declaration, creator)
    assert source.creator.exposed is True
    for slot in ("nominal-due-1", "nominal-due-2"):
        # Exposure belongs to the immutable creation snapshot, not an occurrence
        # request that could clear it. This is not a broker authentication test.
        assert source.occurrence(slot).kind == "occurrence"
        assert source.creator.exposed
    for grant in (
        {"capability": "health__write"},
        {"capability": "health__read", "park_only": True},
        {"capability": "health__read", "resources": {"entity_id": ["c"]}, "park_only": True},
        {"capability": "health__read", "resources": {"entity_id": ["a"]}},
    ):
        wider = parse_declaration({"version": 1, "capabilities": [grant]})
        with pytest.raises(DeclarationError, match="creator_ceiling_exceeded"):
            replace(source, declaration=wider)


def test_source_revision_occurrence_and_domain_are_independent_replay_keys():
    declaration = parse_declaration({"version": 1, "capabilities": []})
    source = ObligationSource("education", "schedule", UUID(int=3), "b" * 64, declaration)
    first = source.occurrence("stored-nominal-due")
    assert first == source.occurrence("stored-nominal-due")
    variants = (
        replace(source, revision="c" * 64),
        replace(source, owner_domain="health"),
        replace(source, source_id=UUID(int=4)),
        replace(source, family="deadline"),
    )
    assert (
        len(
            {
                first.key,
                *(s.occurrence("stored-nominal-due").key for s in variants),
                source.occurrence("next-stored-due").key,
            }
        )
        == 6
    )
    with pytest.raises(DeclarationError):
        source.occurrence("")
    with pytest.raises(DeclarationError):
        replace(source, revision="caller-says-current")

    subscription = replace(source, family="subscription")
    with pytest.raises(DeclarationError):
        subscription.occurrence("event-body-cannot-be-a-slot")
    assert subscription.event(UUID(int=5), feed=True).kind == "feed"
    assert subscription.event(UUID(int=5), feed=False).kind == "domain"
    assert (
        subscription.event(UUID(int=5), feed=True).key
        != subscription.event(UUID(int=5), feed=False).key
    )
    assert (
        subscription.event(UUID(int=5), feed=True).key
        != subscription.event(UUID(int=6), feed=True).key
    )

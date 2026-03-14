"""Memory system Pydantic models.

Provides models for the three-tier memory subsystem:
- Episodes (Eden tier — raw session memories)
- Facts (Mid-term tier — consolidated knowledge)
- Rules (Long-term tier — behavioral patterns)
"""

from __future__ import annotations

from pydantic import BaseModel


class Episode(BaseModel):
    """An episode from the Eden memory tier."""

    id: str
    butler: str
    session_id: str | None = None
    content: str
    importance: float = 5.0
    reference_count: int = 0
    consolidated: bool = False
    consolidation_status: str = "pending"
    tenant_id: str = "owner"
    request_id: str | None = None
    retention_class: str = "transient"
    sensitivity: str = "normal"
    created_at: str
    last_referenced_at: str | None = None
    expires_at: str | None = None
    metadata: dict = {}


class Fact(BaseModel):
    """A consolidated fact from the mid-term memory tier."""

    id: str
    subject: str
    predicate: str
    content: str
    importance: float = 5.0
    confidence: float = 1.0
    decay_rate: float = 0.008
    permanence: str = "standard"
    source_butler: str | None = None
    source_episode_id: str | None = None
    supersedes_id: str | None = None
    entity_id: str | None = None
    entity_name: str | None = None
    object_entity_id: str | None = None
    validity: str = "active"
    scope: str = "global"
    tenant_id: str = "owner"
    request_id: str | None = None
    retention_class: str = "operational"
    sensitivity: str = "normal"
    valid_at: str | None = None
    invalid_at: str | None = None
    idempotency_key: str | None = None
    reference_count: int = 0
    created_at: str
    last_referenced_at: str | None = None
    last_confirmed_at: str | None = None
    tags: list[str] = []
    metadata: dict = {}


class Rule(BaseModel):
    """A behavioral rule from the long-term memory tier."""

    id: str
    content: str
    scope: str = "global"
    maturity: str = "candidate"
    confidence: float = 0.5
    decay_rate: float = 0.01
    permanence: str = "standard"
    effectiveness_score: float = 0.0
    applied_count: int = 0
    success_count: int = 0
    harmful_count: int = 0
    source_episode_id: str | None = None
    source_butler: str | None = None
    tenant_id: str = "owner"
    request_id: str | None = None
    retention_class: str = "rule"
    sensitivity: str = "normal"
    created_at: str
    last_applied_at: str | None = None
    last_evaluated_at: str | None = None
    last_confirmed_at: str | None = None
    tags: list[str] = []
    metadata: dict = {}


class MemoryStats(BaseModel):
    """Aggregated statistics across all memory tiers."""

    total_episodes: int = 0
    unconsolidated_episodes: int = 0
    total_facts: int = 0
    active_facts: int = 0
    fading_facts: int = 0
    total_rules: int = 0
    candidate_rules: int = 0
    established_rules: int = 0
    proven_rules: int = 0
    anti_pattern_rules: int = 0


class EntitySummary(BaseModel):
    """Lightweight entity representation for list views."""

    id: str
    canonical_name: str
    entity_type: str
    aliases: list[str] = []
    roles: list[str] = []
    fact_count: int = 0
    linked_contact_id: str | None = None
    unidentified: bool = False
    source_butler: str | None = None
    source_scope: str | None = None
    created_at: str
    updated_at: str


class EntityInfoEntry(BaseModel):
    """A single entity_info row (credential, identifier, etc.)."""

    id: str
    type: str
    value: str | None = None  # None when secured=True (masked)
    label: str | None = None
    is_primary: bool = False
    secured: bool = False


class EntityDetail(EntitySummary):
    """Full entity detail including recent facts and linked contact info."""

    metadata: dict = {}
    recent_facts: list[Fact] = []
    linked_contact_name: str | None = None
    entity_info: list[EntityInfoEntry] = []


class UpdateEntityRequest(BaseModel):
    """Patch request for updating entity core fields."""

    canonical_name: str | None = None
    aliases: list[str] | None = None
    metadata: dict | None = None


class MemoryActivity(BaseModel):
    """A recent memory activity event (new fact, rule, or episode)."""

    id: str
    type: str  # "episode", "fact", "rule"
    summary: str
    butler: str | None = None
    created_at: str

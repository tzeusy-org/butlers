"""Memory system Pydantic models.

Provides models for the three-tier memory subsystem:
- Episodes (Eden tier — raw session memories)
- Facts (Mid-term tier — consolidated knowledge)
- Rules (Long-term tier — behavioral patterns)
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    session_id: str | None = None
    supersedes_id: str | None = None
    entity_id: str | None = None
    entity_name: str | None = None
    object_entity_id: str | None = None
    object_entity_name: str | None = None
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
    archived: bool = False
    created_at: str
    updated_at: str
    dunbar_tier: int | None = None
    dunbar_score: float | None = None


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
    recent_facts_total: int = 0
    recent_facts_offset: int = 0
    recent_facts_limit: int = 20
    recent_facts_has_more: bool = False
    linked_contact_name: str | None = None
    entity_info: list[EntityInfoEntry] = []


class UpdateEntityRequest(BaseModel):
    """Patch request for updating entity core fields."""

    canonical_name: str | None = None
    entity_type: str | None = None
    aliases: list[str] | None = None
    metadata: dict | None = None
    roles: list[str] | None = None


class MemoryActivity(BaseModel):
    """A recent memory activity event (new fact, rule, or episode)."""

    id: str
    type: str  # "episode", "fact", "rule"
    summary: str
    butler: str | None = None
    created_at: str


class ButlerMemoryStats(BaseModel):
    """Per-butler memory subsystem counts with 24-hour deltas."""

    total_episodes: int = 0
    episodes_24h: int = 0
    total_facts: int = 0
    facts_24h: int = 0
    total_entities: int = 0
    entities_24h: int = 0
    total_rules: int = 0
    rules_24h: int = 0


class MemoryRetentionPolicy(BaseModel):
    """A single row from public.memory_retention_policies."""

    kind: str
    ttl_days: int | None = None
    max_rows: int | None = None
    updated_at: str
    updated_by: str | None = None


class UpdateRetentionPolicyEntry(BaseModel):
    """One entry in a bulk PUT request for retention policies."""

    kind: str
    ttl_days: int | None = None
    max_rows: int | None = None


class UpdateRetentionPoliciesRequest(BaseModel):
    """Bulk update request for retention policies."""

    policies: list[UpdateRetentionPolicyEntry]


class CompactionLogEntry(BaseModel):
    """A single row from public.memory_compaction_log."""

    id: int
    ts: str
    kind: str
    rows_removed: int
    bytes_freed: int | None = None


class MemoryInspectResult(BaseModel):
    """A single result from the memory inspect search."""

    id: str
    kind: str  # "episode", "fact", "rule"
    content: str
    butler: str | None = None
    created_at: str
    metadata: dict = {}


# ---------------------------------------------------------------------------
# Re-embedding models
# ---------------------------------------------------------------------------

_DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class ReembedPendingCounts(BaseModel):
    """Per-tier counts of rows whose stored embedding is stale."""

    counts: dict[str, int]
    """Stale row count per tier: episodes, facts, rules."""
    total: int
    """Sum of all tier counts."""
    current_model: str
    """The model name used as the reference point for staleness."""


class ReembedRunRequest(BaseModel):
    """Request body for POST /api/memory/reembed."""

    butler: str
    """Butler schema to operate on."""
    dry_run: bool = True
    """When True (default), count and log only — no DB writes are performed."""
    tiers: list[str] | None = None
    """Subset of tiers to process (episodes, facts, rules).  None → all tiers."""
    batch_size: int = Field(default=50, ge=1, le=500)
    """Rows per DB round-trip (1–500, default 50)."""
    current_model: str = _DEFAULT_EMBEDDING_MODEL
    """Embedding model currently configured.  Defaults to all-MiniLM-L6-v2."""


class ReembedRunResult(BaseModel):
    """Response from POST /api/memory/reembed.

    Note: this is a synchronous (blocking) endpoint. Re-embedding thousands of
    rows can take minutes.  Callers should use a long-poll timeout or run
    dry_run=True first to estimate the scope before committing.
    """

    dry_run: bool
    current_model: str
    tiers_processed: list[str]
    counts: dict[str, int]
    """Rows re-embedded (or found stale in dry_run) per tier."""
    total: int
    """Sum across all tiers."""
    errors: list[str]
    """Non-fatal per-batch errors encountered during the run."""

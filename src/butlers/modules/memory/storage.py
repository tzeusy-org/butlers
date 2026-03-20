"""Core memory storage operations for the Memory Butler.

Provides async functions for storing episodes, facts, and rules in the
memory database.  All functions accept an asyncpg connection pool and
use the EmbeddingEngine for semantic vector generation.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import math
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load sibling modules from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent


def _load_module(name: str):
    path = _MODULE_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_embedding_mod = _load_module("embedding")
_search_mod = _load_module("search_vector")

EmbeddingEngine = _embedding_mod.EmbeddingEngine
preprocess_text = _search_mod.preprocess_text
tsvector_sql = _search_mod.tsvector_sql

# Default episode time-to-live.
_DEFAULT_EPISODE_TTL_DAYS = 7

# ---------------------------------------------------------------------------
# Permanence -> decay-rate mapping (from butler.toml)
# ---------------------------------------------------------------------------
_PERMANENCE_DECAY: dict[str, float] = {
    "permanent": 0.0,
    "stable": 0.002,
    "standard": 0.008,
    "volatile": 0.03,
    "ephemeral": 0.1,
}


def validate_permanence(permanence: str) -> float:
    """Validate a permanence level and return its decay rate.

    Args:
        permanence: One of 'permanent', 'stable', 'standard', 'volatile', 'ephemeral'.

    Returns:
        The corresponding decay rate float.

    Raises:
        ValueError: If *permanence* is not a recognised level.
    """
    try:
        return _PERMANENCE_DECAY[permanence]
    except KeyError:
        valid = sorted(_PERMANENCE_DECAY)
        raise ValueError(f"Invalid permanence: {permanence!r}. Must be one of {valid}") from None


# ---------------------------------------------------------------------------
# Constants for memory types and link relations
# ---------------------------------------------------------------------------
_VALID_RELATIONS = frozenset(
    {
        "derived_from",
        "supports",
        "contradicts",
        "supersedes",
        "related_to",
    }
)
_VALID_MEMORY_TYPES = frozenset({"episode", "fact", "rule"})

# Map memory types to their table names
_TYPE_TABLE: dict[str, str] = {
    "episode": "episodes",
    "fact": "facts",
    "rule": "rules",
}

# ---------------------------------------------------------------------------
# Fuzzy predicate matching helpers
# ---------------------------------------------------------------------------

_FUZZY_EDIT_DISTANCE_THRESHOLD = 2
_FUZZY_PREFIX_LENGTH_THRESHOLD = 5


def _levenshtein_distance(a: str, b: str) -> int:
    """Compute the Levenshtein edit distance between two strings.

    Uses a space-efficient two-row DP approach.  Suitable for short predicate
    names (typically ≤ 40 chars).

    Args:
        a: First string.
        b: Second string.

    Returns:
        Minimum number of single-character edits (insertions, deletions,
        substitutions) required to transform *a* into *b*.

    Examples:
        >>> _levenshtein_distance("parent_of", "parnet_of")
        2
        >>> _levenshtein_distance("parent_of", "parent_of")
        0
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # Optimise: if lengths differ by more than threshold, early exit.
    # The edit distance can't be smaller than the absolute length difference.
    if abs(len(a) - len(b)) > _FUZZY_EDIT_DISTANCE_THRESHOLD:
        return _FUZZY_EDIT_DISTANCE_THRESHOLD + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,  # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + (0 if ca == cb else 1),  # substitution
            )
        prev = curr
    return prev[len(b)]


async def _fuzzy_match_predicates(
    conn,
    predicate: str,
) -> list[dict]:
    """Find registered predicates similar to *predicate* via Levenshtein and prefix overlap.

    Fetches all predicate names (and optional descriptions) from
    ``predicate_registry``, then filters to those that satisfy at least one of:

    - Edit distance ≤ ``_FUZZY_EDIT_DISTANCE_THRESHOLD`` (2)
    - Shared prefix of ≥ ``_FUZZY_PREFIX_LENGTH_THRESHOLD`` (5) characters

    The input predicate itself is excluded from results (exact match means it
    IS registered — the caller should not call this in that case).

    Args:
        conn: asyncpg connection (inside an open transaction).
        predicate: Normalised predicate string that was NOT found in the registry.

    Returns:
        List of ``{"predicate": str, "description": str | None}`` dicts,
        ordered by edit distance ascending (closest matches first).
        Empty list when no close matches exist.
    """
    rows = await conn.fetch("SELECT name, description FROM predicate_registry ORDER BY name")
    if not rows:
        return []

    results: list[tuple[int, str, str | None]] = []
    for row in rows:
        name: str = row["name"]
        description: str | None = row["description"]
        dist = _levenshtein_distance(predicate, name)
        if dist <= _FUZZY_EDIT_DISTANCE_THRESHOLD:
            results.append((dist, name, description))
            continue
        # Prefix overlap check — only if both strings are long enough.
        min_len = min(len(predicate), len(name))
        if min_len >= _FUZZY_PREFIX_LENGTH_THRESHOLD:
            for prefix_len in range(_FUZZY_PREFIX_LENGTH_THRESHOLD, min_len + 1):
                if predicate[:prefix_len] == name[:prefix_len]:
                    # Rank prefix matches slightly lower than near-exact edits.
                    results.append((_FUZZY_EDIT_DISTANCE_THRESHOLD + 1, name, description))
                    break

    # Deduplicate (a predicate can't match twice, but guard just in case).
    seen: set[str] = set()
    deduped: list[tuple[int, str, str | None]] = []
    for item in results:
        if item[1] not in seen:
            seen.add(item[1])
            deduped.append(item)

    deduped.sort(key=lambda t: t[0])
    return [{"predicate": name, "description": desc} for _, name, desc in deduped]


# ---------------------------------------------------------------------------
# Temporal fact idempotency
# ---------------------------------------------------------------------------


def _generate_temporal_idempotency_key(
    entity_id: uuid.UUID | None,
    object_entity_id: uuid.UUID | None,
    scope: str,
    predicate: str,
    valid_at: datetime,
    source_episode_id: uuid.UUID | None,
) -> str:
    """Generate a deterministic idempotency key for a temporal fact.

    Computes a SHA-256 hash (truncated to 32 hex chars) of the canonical
    tuple ``(entity_id, object_entity_id, scope, predicate, valid_at,
    source_episode_id)``.  This prevents duplicate temporal fact writes
    even when the same observation is submitted multiple times.

    Args:
        entity_id: Subject entity UUID or None.
        object_entity_id: Object entity UUID or None (for edge-facts).
        scope: Fact scope string.
        predicate: Fact predicate string.
        valid_at: Temporal timestamp (must not be None for temporal facts).
        source_episode_id: Source episode UUID or None.

    Returns:
        A 32-character lowercase hex string.
    """
    parts = "|".join(
        [
            str(entity_id) if entity_id is not None else "",
            str(object_entity_id) if object_entity_id is not None else "",
            scope,
            predicate,
            valid_at.isoformat(),
            str(source_episode_id) if source_episode_id is not None else "",
        ]
    )
    return hashlib.sha256(parts.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Shared discovery catalog helpers
# ---------------------------------------------------------------------------


async def _upsert_catalog(
    pool: Pool,
    *,
    source_schema: str,
    source_table: str,
    source_id: uuid.UUID,
    source_butler: str | None,
    tenant_id: str,
    entity_id: uuid.UUID | None,
    summary: str,
    embedding: list[float],
    search_text: str,
    memory_type: str,
    # Spec-required enrichment columns (all nullable / optional).
    title: str | None = None,
    predicate: str | None = None,
    scope: str | None = None,
    valid_at: datetime | None = None,
    confidence: float | None = None,
    importance: float | None = None,
    retention_class: str | None = None,
    sensitivity: str | None = None,
    object_entity_id: uuid.UUID | None = None,
) -> None:
    """Upsert a row into shared.memory_catalog (best-effort, fire-and-forget).

    On any error the exception is caught and logged as a warning so that
    catalog write failure NEVER blocks the canonical memory write.

    The enrichment columns (title, predicate, scope, valid_at, confidence,
    importance, retention_class, sensitivity, object_entity_id) map directly
    to the spec-required columns added in core_024.  They are all nullable so
    the call remains backward-compatible before that migration is applied.
    """
    sql = f"""
        INSERT INTO shared.memory_catalog (
            source_schema, source_table, source_id,
            source_butler, tenant_id, entity_id,
            summary, embedding, search_vector, memory_type,
            title, predicate, scope, valid_at,
            confidence, importance, retention_class, sensitivity,
            object_entity_id,
            updated_at
        )
        VALUES (
            $1, $2, $3,
            $4, $5, $6,
            $7, $8, {tsvector_sql("$9")}, $10,
            $11, $12, $13, $14,
            $15, $16, $17, $18,
            $19,
            now()
        )
        ON CONFLICT (source_schema, source_table, source_id)
        DO UPDATE SET
            summary          = EXCLUDED.summary,
            embedding        = EXCLUDED.embedding,
            search_vector    = EXCLUDED.search_vector,
            entity_id        = EXCLUDED.entity_id,
            tenant_id        = EXCLUDED.tenant_id,
            title            = EXCLUDED.title,
            predicate        = EXCLUDED.predicate,
            scope            = EXCLUDED.scope,
            valid_at         = EXCLUDED.valid_at,
            confidence       = EXCLUDED.confidence,
            importance       = EXCLUDED.importance,
            retention_class  = EXCLUDED.retention_class,
            sensitivity      = EXCLUDED.sensitivity,
            object_entity_id = EXCLUDED.object_entity_id,
            updated_at       = now()
    """
    await pool.execute(
        sql,
        source_schema,
        source_table,
        source_id,
        source_butler,
        tenant_id,
        entity_id,
        summary,
        str(embedding),
        search_text,
        memory_type,
        title,
        predicate,
        scope,
        valid_at,
        confidence,
        importance,
        retention_class,
        sensitivity,
        object_entity_id,
    )


# ---------------------------------------------------------------------------
# Public API — Storage
# ---------------------------------------------------------------------------


async def _lookup_episode_ttl_days(pool: Pool, retention_class: str) -> int:
    """Look up the TTL (in days) for an episode's retention_class from memory_policies.

    Falls back to ``_DEFAULT_EPISODE_TTL_DAYS`` if the table does not yet exist
    (e.g. before migration mem_019 is applied) or the class is not found.

    Args:
        pool: asyncpg connection pool.
        retention_class: The retention class to look up (e.g. 'transient').

    Returns:
        Number of days until the episode expires.
    """
    try:
        ttl = await pool.fetchval(
            "SELECT ttl_days FROM memory_policies WHERE retention_class = $1",
            retention_class,
        )
        if ttl is not None and isinstance(ttl, int) and ttl > 0:
            return ttl
    except Exception:
        pass  # table may not exist yet; fall through to default
    return _DEFAULT_EPISODE_TTL_DAYS


async def store_episode(
    pool: Pool,
    content: str,
    butler: str,
    embedding_engine: EmbeddingEngine,
    *,
    session_id: uuid.UUID | None = None,
    importance: float = 5.0,
    metadata: dict | None = None,
    tenant_id: str = "owner",
    request_id: str | None = None,
    retention_class: str = "transient",
    sensitivity: str = "normal",
) -> uuid.UUID:
    """Store a raw episode from a butler runtime session.

    Generates both a semantic embedding and a full-text search vector for the
    content, then inserts a row into the ``episodes`` table.  The episode TTL
    is derived from the ``memory_policies`` table via the ``retention_class``
    (falls back to ``_DEFAULT_EPISODE_TTL_DAYS`` when the policy row is absent).

    Args:
        pool: asyncpg connection pool for the memory database.
        content: Raw episode text content.
        butler: Name of the source butler.
        embedding_engine: EmbeddingEngine instance for generating vectors.
        session_id: Optional UUID of the source runtime session.
        importance: Importance rating (default 5.0).
        metadata: Optional JSONB metadata dict.
        tenant_id: Tenant scope for multi-tenant isolation (default 'owner').
        request_id: Optional request trace ID for correlation.
        retention_class: Retention policy class (default 'transient').
        sensitivity: Data sensitivity classification (default 'normal').

    Returns:
        The UUID of the newly created episode row.
    """
    episode_id = uuid.uuid4()
    embedding = embedding_engine.embed(content)
    search_text = preprocess_text(content)
    ttl_days = await _lookup_episode_ttl_days(pool, retention_class)
    expires_at = datetime.now(UTC) + timedelta(days=ttl_days)

    sql = f"""
        INSERT INTO episodes (id, butler, session_id, content, embedding, search_vector,
                              importance, expires_at, metadata, tenant_id, request_id,
                              retention_class, sensitivity)
        VALUES ($1, $2, $3, $4, $5, {tsvector_sql("$6")}, $7, $8, $9, $10, $11, $12, $13)
    """

    meta_json = json.dumps(metadata or {})

    await pool.execute(
        sql,
        episode_id,
        butler,
        session_id,
        content,
        str(embedding),  # pgvector accepts string format '[1.0, 2.0, ...]'
        search_text,
        importance,
        expires_at,
        meta_json,
        tenant_id,
        request_id,
        retention_class,
        sensitivity,
    )

    return episode_id


async def _insert_fact_record(
    conn,
    *,
    fact_id: uuid.UUID,
    subject: str,
    predicate: str,
    content: str,
    embedding: list[float],
    search_text: str,
    importance: float,
    decay_rate: float,
    permanence: str,
    source_butler: str | None,
    source_episode_id: uuid.UUID | None,
    supersedes_id: uuid.UUID | None,
    scope: str,
    now: datetime,
    tags_json: str,
    meta_json: str,
    entity_id: uuid.UUID | None,
    object_entity_id: uuid.UUID | None,
    fact_valid_at: datetime | None,
    tenant_id: str,
    request_id: str | None,
    idempotency_key: str | None,
    retention_class: str,
    sensitivity: str,
) -> None:
    """Insert a single fact row into the ``facts`` table.

    Extracted from :func:`store_fact` to eliminate the near-duplicate INSERT
    block required for inverse / symmetric edge facts.  All parameters map
    directly to ``facts`` columns; the caller is responsible for computing the
    correct values (including any entity swaps for inverse facts).

    Args:
        conn: asyncpg connection inside an open transaction.
        fact_id: UUID for the new fact row.
        subject: Human-readable subject label.
        predicate: Predicate string.
        content: Human-readable content / object label.
        embedding: Pre-computed semantic embedding vector.
        search_text: Pre-processed full-text search string.
        importance: Importance score.
        decay_rate: Memory decay rate derived from *permanence*.
        permanence: Permanence level string (e.g. ``'standard'``).
        source_butler: Name of the originating butler, or ``None``.
        source_episode_id: Source episode UUID, or ``None``.
        supersedes_id: UUID of the fact being superseded, or ``None``.
        scope: Fact scope string (e.g. ``'global'``).
        now: Timestamp used for ``created_at``, ``last_confirmed_at``, and
            ``observed_at``.
        tags_json: JSON-serialised tags list.
        meta_json: JSON-serialised metadata dict.
        entity_id: Subject entity UUID, or ``None``.
        object_entity_id: Object entity UUID (edge-facts only), or ``None``.
        fact_valid_at: Temporal validity timestamp, or ``None`` for property facts.
        tenant_id: Tenant scope string.
        request_id: Optional request trace ID.
        idempotency_key: Deduplication key for temporal facts, or ``None``.
        retention_class: Retention policy class string.
        sensitivity: Data sensitivity classification string.
    """
    sql = f"""
        INSERT INTO facts (
            id, subject, predicate, content, embedding, search_vector,
            importance, confidence, decay_rate, permanence, source_butler,
            source_episode_id, supersedes_id, validity, scope,
            created_at, last_confirmed_at, tags, metadata, entity_id,
            object_entity_id, valid_at, tenant_id, request_id,
            idempotency_key, observed_at, retention_class, sensitivity
        )
        VALUES (
            $1, $2, $3, $4, $5, {tsvector_sql("$6")},
            $7, $8, $9, $10, $11,
            $12, $13, 'active', $14,
            $15, $15, $16, $17, $18,
            $19, $20, $21, $22,
            $23, $24, $25, $26
        )
    """
    await conn.execute(
        sql,
        fact_id,
        subject,
        predicate,
        content,
        str(embedding),
        search_text,
        importance,
        1.0,  # confidence
        decay_rate,
        permanence,
        source_butler,
        source_episode_id,
        supersedes_id,
        scope,
        now,
        tags_json,
        meta_json,
        entity_id,
        object_entity_id,
        fact_valid_at,
        tenant_id,
        request_id,
        idempotency_key,
        now,  # observed_at = insertion time
        retention_class,
        sensitivity,
    )


async def store_fact(
    pool: Pool,
    subject: str,
    predicate: str,
    content: str,
    embedding_engine: EmbeddingEngine,
    *,
    importance: float = 5.0,
    permanence: str = "standard",
    scope: str = "global",
    tags: list[str] | None = None,
    source_butler: str | None = None,
    source_episode_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    entity_id: uuid.UUID | None = None,
    object_entity_id: uuid.UUID | None = None,
    valid_at: datetime | None = None,
    tenant_id: str = "owner",
    request_id: str | None = None,
    idempotency_key: str | None = None,
    retention_class: str = "operational",
    sensitivity: str = "normal",
    enable_shared_catalog: bool = False,
    source_schema: str | None = None,
) -> uuid.UUID:
    """Store a distilled fact with optional supersession.

    If ``entity_id`` is provided the fact is anchored to a resolved entity.
    Uniqueness and supersession use ``(entity_id, scope, predicate)``; the
    ``subject`` field is still stored as a human-readable label.

    If ``object_entity_id`` is also provided, the fact represents a directed
    edge from ``entity_id`` (subject) to ``object_entity_id`` (object).
    Uniqueness and supersession use
    ``(entity_id, object_entity_id, scope, predicate)``.

    If ``entity_id`` is omitted (backward compatible), uniqueness and
    supersession use ``(subject, predicate)`` as before.

    If an active fact matching the key already exists **and both the new and
    old facts are property facts** (``valid_at IS NULL``), the old fact is
    superseded:

    1. Set the old fact's ``validity`` to ``'superseded'``.
    2. Link the new fact to the old one via ``supersedes_id``.
    3. Create a ``memory_links`` row with ``relation='supersedes'``.

    Temporal facts (``valid_at IS NOT NULL``) never supersede each other or
    property facts.  A new temporal fact always coexists as an additional
    active row.  Similarly, a new property fact (``valid_at IS NULL``) will
    supersede only another property fact with the same key — it leaves any
    existing temporal facts untouched.

    Args:
        entity_id: Optional UUID of an existing entity row.  When provided
            the entity must exist; a ``ValueError`` is raised otherwise.
        object_entity_id: Optional UUID of a target entity for edge-facts.
            When provided the entity must exist; a ``ValueError`` is raised
            otherwise.  ``entity_id`` must also be set.
        valid_at: Optional wall-clock time the fact was true.  Defaults to
            ``None`` (property fact).  Pass an explicit datetime to create a
            temporal fact; multiple temporal facts with different ``valid_at``
            values coexist as active facts without superseding each other.
        tenant_id: Tenant scope for multi-tenant isolation (default 'owner').
            Supersession checks are scoped to the same tenant_id.
        request_id: Optional request trace ID for correlation.
        idempotency_key: Optional dedup key for temporal facts.  When omitted
            and ``valid_at`` is set, a key is auto-generated as a SHA-256 hash
            (32 hex chars) of ``(entity_id, object_entity_id, scope, predicate,
            valid_at, source_episode_id)``.  A write with the same
            ``(tenant_id, idempotency_key)`` is a no-op; the existing fact's ID
            is returned instead.  Property facts (``valid_at IS NULL``) always
            have ``idempotency_key = NULL`` and use supersession instead.
        retention_class: Retention policy class for the fact (default
            'operational').  Controls lifecycle management behaviour.
        sensitivity: Data sensitivity classification (default 'normal').
            Use 'pii' for personally-identifiable information, etc.
        enable_shared_catalog: When True, write a summary row to
            ``shared.memory_catalog`` after the canonical fact is stored.
            Catalog write failure is logged as a warning and does NOT block
            the canonical write.  Defaults to False.
        source_schema: The butler schema name used as ``source_schema`` in the
            catalog row (e.g. ``'health'``).  Required when
            ``enable_shared_catalog=True``; ignored otherwise.

    Returns:
        A dict with:
        - ``"id"``: UUID of the newly created (or pre-existing, idempotent) fact.
        - ``"supersedes_id"``: UUID of the superseded fact, or ``None``.
        - ``"suggestions"``: list of ``{"predicate": str, "description": str | None}``
          dicts of registered predicates similar to the novel predicate, or absent
          when the predicate was found in the registry or no close matches exist.
    """
    fact_id = uuid.uuid4()
    searchable = f"{subject} {predicate} {content}"
    embedding = embedding_engine.embed(searchable)
    search_text = preprocess_text(searchable)
    decay_rate = validate_permanence(permanence)
    now = datetime.now(UTC)
    # valid_at IS NULL means property fact; valid_at IS NOT NULL means temporal fact.
    # Preserve NULL when omitted — do NOT default to now().
    fact_valid_at = valid_at  # None → property fact, datetime → temporal fact
    tags_json = json.dumps(tags or [])
    meta_json = json.dumps(metadata or {})

    # Determine idempotency_key for temporal facts.
    # Property facts (valid_at IS NULL) must NOT get an idempotency key —
    # they use the existing supersession mechanism instead.
    effective_idempotency_key: str | None = None
    if fact_valid_at is not None:
        if idempotency_key is not None:
            # Caller provided an explicit key — use it as-is.
            effective_idempotency_key = idempotency_key
        else:
            # Auto-generate a deterministic key from the canonical fact tuple.
            effective_idempotency_key = _generate_temporal_idempotency_key(
                entity_id,
                object_entity_id,
                scope,
                predicate,
                fact_valid_at,
                source_episode_id,
            )

    # Lifecycle warning populated inside the transaction block when the predicate
    # is deprecated; included in the return dict after the block exits.
    _deprecation_warning: str | None = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Validate entity_id when provided.
            # D7: fetch entity_type in the same query as the existence check — no extra round-trip.
            _subject_entity_type: str | None = None
            if entity_id is not None:
                _entity_row = await conn.fetchrow(
                    "SELECT id, entity_type FROM shared.entities WHERE id = $1",
                    entity_id,
                )
                if _entity_row is None:
                    raise ValueError(f"entity_id {entity_id!r} does not exist in entities table")
                _subject_entity_type = _entity_row["entity_type"]

            # Validate object_entity_id when provided.
            # D7: fetch entity_type in the same query as the existence check.
            _object_entity_type: str | None = None
            if object_entity_id is not None:
                if entity_id is None:
                    raise ValueError(
                        "object_entity_id requires entity_id to be set (edge-facts "
                        "need both subject and object entities)"
                    )
                if entity_id == object_entity_id:
                    raise ValueError(
                        "Self-referencing edges are not allowed: "
                        "entity_id and object_entity_id must differ"
                    )
                _obj_entity_row = await conn.fetchrow(
                    "SELECT id, entity_type FROM shared.entities WHERE id = $1",
                    object_entity_id,
                )
                if _obj_entity_row is None:
                    raise ValueError(
                        f"object_entity_id {object_entity_id!r} does not exist in entities table"
                    )
                _object_entity_type = _obj_entity_row["entity_type"]

            # Alias resolution: before registry enforcement, check whether the
            # predicate name matches an alias for a canonical predicate.  If it
            # does, silently rewrite predicate to the canonical name so that all
            # subsequent logic (enforcement, supersession, auto-registration,
            # usage tracking) operates on the canonical predicate.
            # Best-effort: if the aliases column does not exist yet (pre-mem_025
            # environment) the query will raise; we catch and skip resolution.
            _resolved_from: str | None = None
            try:
                _alias_row = await conn.fetchrow(
                    "SELECT name FROM predicate_registry WHERE $1 = ANY(aliases)",
                    predicate,
                )
                if _alias_row is not None:
                    _resolved_from = predicate
                    predicate = _alias_row["name"]
            except Exception:
                # Pre-migration environment — aliases column absent. Skip.
                logger.debug(
                    "Alias resolution skipped (pre-mem_025 environment or unexpected error).",
                    exc_info=True,
                )

            # Registry enforcement: look up predicate flags and enforce constraints.
            # D1: single cached query inside the existing transaction, PK lookup on
            # a small table (~100 rows), overhead negligible vs. embedding computation.
            # Also fetch lifecycle columns (status, superseded_by) added in mem_023.
            # COALESCE(status, 'active') guards against NULL in case a row predates
            # the migration (status column exists but row was inserted before DEFAULT
            # took effect, which is not possible given NOT NULL DEFAULT, but kept as
            # a defensive measure). Test mocks without status use .get() below.
            # D7: also fetch expected_subject_type and expected_object_type for soft
            # type validation (warnings, not errors).
            _registry_row = await conn.fetchrow(
                "SELECT is_edge, is_temporal,"
                " COALESCE(status, 'active') AS status,"
                " superseded_by,"
                " expected_subject_type, expected_object_type,"
                " inverse_of, is_symmetric"
                " FROM predicate_registry WHERE name = $1",
                predicate,
            )
            _predicate_is_novel = _registry_row is None
            _type_warnings: list[str] = []
            if _registry_row is not None:
                if _registry_row["is_edge"] and object_entity_id is None:
                    raise ValueError(
                        f"Predicate {predicate!r} is registered as an edge predicate "
                        f"(is_edge=true) and requires object_entity_id to be set. "
                        f"Call memory_entity_resolve(identifier=<target_name>) to resolve "
                        f"the target entity, then retry with object_entity_id."
                    )
                if _registry_row["is_temporal"] and valid_at is None:
                    raise ValueError(
                        f"Predicate {predicate!r} is registered as a temporal predicate "
                        f"(is_temporal=true) and requires valid_at to be set. "
                        f"Omitting valid_at would cause supersession to destroy previous "
                        f"records for this predicate. Provide an ISO-8601 valid_at "
                        f"timestamp for when this fact was true."
                    )
                # Lifecycle: build deprecation warning for deprecated predicates.
                # The write still succeeds — warning is attached to the response.
                # Use .get() for backward-compatibility with mocked test fixtures that
                # only include is_edge/is_temporal; the COALESCE in SQL handles real DBs.
                _predicate_status = _registry_row.get("status", "active") or "active"
                if _predicate_status == "deprecated":
                    _superseded_by = _registry_row.get("superseded_by")
                    if _superseded_by:
                        _deprecation_warning = (
                            f"Predicate {predicate!r} is deprecated. "
                            f"Use {_superseded_by!r} instead."
                        )
                    else:
                        _deprecation_warning = (
                            f"Predicate {predicate!r} is deprecated and has no "
                            f"direct replacement. Consider using a domain-specific "
                            f"predicate from the registry."
                        )

                # D7: Soft domain/range type validation.
                # NULL expected types skip validation; mismatches produce warnings, not errors.
                _exp_subject_type = _registry_row["expected_subject_type"]
                _exp_object_type = _registry_row["expected_object_type"]

                if (
                    _exp_subject_type is not None
                    and _subject_entity_type is not None
                    and _subject_entity_type != _exp_subject_type
                ):
                    _type_warnings.append(
                        f"Subject type mismatch for predicate {predicate!r}: "
                        f"expected '{_exp_subject_type}' but subject entity has "
                        f"entity_type='{_subject_entity_type}'. "
                        f"The fact has been stored; this is a soft warning."
                    )

                if (
                    _exp_object_type is not None
                    and _object_entity_type is not None
                    and _object_entity_type != _exp_object_type
                ):
                    _type_warnings.append(
                        f"Object type mismatch for predicate {predicate!r}: "
                        f"expected '{_exp_object_type}' but object entity has "
                        f"entity_type='{_object_entity_type}'. "
                        f"The fact has been stored; this is a soft warning."
                    )

            # D3: Fuzzy matching — for novel predicates (not found in registry),
            # compute suggestions inside the same transaction to reuse the connection.
            _fuzzy_suggestions: list[dict] = []
            if _predicate_is_novel:
                _fuzzy_suggestions = await _fuzzy_match_predicates(conn, predicate)

            # Guard: reject facts that embed entity UUIDs in content without
            # using object_entity_id.  This catches the common mistake of
            # encoding a relationship target as freeform text instead of a
            # proper edge-fact link.
            if object_entity_id is None and re.search(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                content,
                re.IGNORECASE,
            ):
                embedded_uuid = re.search(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    content,
                    re.IGNORECASE,
                ).group()  # type: ignore[union-attr]
                raise ValueError(
                    f"content contains an embedded UUID ({embedded_uuid}) but "
                    f"object_entity_id is not set. If this fact describes a "
                    f"relationship between entities, pass the target entity's UUID "
                    f"as object_entity_id instead of embedding it in content. "
                    f"Call memory_entity_resolve(identifier=<name>) to resolve "
                    f"the target entity first."
                )

            # Idempotency check for temporal facts.
            # If a fact with the same (tenant_id, idempotency_key) already exists,
            # return its ID as a no-op.  Property facts skip this path entirely.
            if effective_idempotency_key is not None:
                existing_idem_id = await conn.fetchval(
                    "SELECT id FROM facts WHERE tenant_id = $1 AND idempotency_key = $2",
                    tenant_id,
                    effective_idempotency_key,
                )
                if existing_idem_id is not None:
                    # Return early with a dict — same shape as the non-idempotent path.
                    # Novel-predicate suggestions are omitted for idempotent hits (the
                    # fact already exists; no new guidance is needed).
                    return {"id": existing_idem_id, "supersedes_id": None}

            # Supersession applies only to property facts (valid_at IS NULL).
            # Temporal facts (valid_at IS NOT NULL) always coexist as independent
            # active rows regardless of predicate_registry.is_temporal.
            skip_supersession = fact_valid_at is not None

            supersedes_id = None
            if not skip_supersession:
                # Check for an existing *property* active fact using the appropriate key.
                # Only consider rows with valid_at IS NULL (property facts).
                # Supersession is always scoped to the same tenant_id.
                if object_entity_id is not None:
                    # Edge-fact: keyed on (tenant_id, entity_id, object_entity_id, scope, predicate)
                    existing = await conn.fetchrow(
                        "SELECT id FROM facts "
                        "WHERE tenant_id = $1 AND entity_id = $2 AND object_entity_id = $3 "
                        "AND scope = $4 AND predicate = $5 "
                        "AND validity = 'active' AND valid_at IS NULL",
                        tenant_id,
                        entity_id,
                        object_entity_id,
                        scope,
                        predicate,
                    )
                elif entity_id is not None:
                    # Property-fact: keyed on (tenant_id, entity_id, scope, predicate)
                    existing = await conn.fetchrow(
                        "SELECT id FROM facts "
                        "WHERE tenant_id = $1 AND entity_id = $2 AND object_entity_id IS NULL "
                        "AND scope = $3 AND predicate = $4 "
                        "AND validity = 'active' AND valid_at IS NULL",
                        tenant_id,
                        entity_id,
                        scope,
                        predicate,
                    )
                else:
                    existing = await conn.fetchrow(
                        "SELECT id FROM facts "
                        "WHERE tenant_id = $1 AND entity_id IS NULL "
                        "AND subject = $2 AND predicate = $3 "
                        "AND validity = 'active' AND valid_at IS NULL",
                        tenant_id,
                        subject,
                        predicate,
                    )

                if existing:
                    old_id = existing["id"]
                    supersedes_id = old_id
                    # Mark old fact as superseded and set invalid_at to record when
                    # it was known to be no longer true (i.e. when the new fact arrives).
                    await conn.execute(
                        "UPDATE facts SET validity = 'superseded', invalid_at = $2 WHERE id = $1",
                        old_id,
                        now,
                    )

            # Insert new fact — include idempotency_key and observed_at columns
            # added by migration mem_016.  Falls back gracefully on older schemas
            # because the columns have safe defaults (NULL and now()).
            await _insert_fact_record(
                conn,
                fact_id=fact_id,
                subject=subject,
                predicate=predicate,
                content=content,
                embedding=embedding,
                search_text=search_text,
                importance=importance,
                decay_rate=decay_rate,
                permanence=permanence,
                source_butler=source_butler,
                source_episode_id=source_episode_id,
                supersedes_id=supersedes_id,
                scope=scope,
                now=now,
                tags_json=tags_json,
                meta_json=meta_json,
                entity_id=entity_id,
                object_entity_id=object_entity_id,
                fact_valid_at=fact_valid_at,
                tenant_id=tenant_id,
                request_id=request_id,
                idempotency_key=effective_idempotency_key,
                retention_class=retention_class,
                sensitivity=sensitivity,
            )

            # Create supersedes link if applicable
            if supersedes_id:
                await conn.execute(
                    "INSERT INTO memory_links "
                    "(source_type, source_id, target_type, target_id, relation) "
                    "VALUES ('fact', $1, 'fact', $2, 'supersedes')",
                    fact_id,
                    supersedes_id,
                )

            # Auto-create inverse / mirrored facts for edge predicates.
            # Applies when:
            #   (a) The predicate is an edge fact (entity_id + object_entity_id both set).
            #   (b) The registry row signals inverse_of or is_symmetric.
            # The mirrored fact swaps (entity_id ↔ object_entity_id) and swaps
            # subject/content labels.  It is stored inside the same transaction.
            # Requires mem_025 migration to be applied (inverse_of and is_symmetric
            # columns must exist on predicate_registry).
            if _registry_row is not None and entity_id is not None and object_entity_id is not None:
                _inverse_predicate: str | None = None
                _is_symmetric = _registry_row.get("is_symmetric") or False
                _inverse_of = _registry_row.get("inverse_of")
                if _is_symmetric:
                    _inverse_predicate = predicate
                elif _inverse_of:
                    _inverse_predicate = _inverse_of

                if _inverse_predicate is not None:
                    # Compute the inverse fact's idempotency key when applicable.
                    _inv_idem_key: str | None = None
                    if fact_valid_at is not None:
                        _inv_idem_key = _generate_temporal_idempotency_key(
                            object_entity_id,
                            entity_id,
                            scope,
                            _inverse_predicate,
                            fact_valid_at,
                            source_episode_id,
                        )
                        # Skip if inverse already exists (idempotent temporal).
                        _inv_exists = await conn.fetchval(
                            "SELECT id FROM facts WHERE tenant_id = $1 AND idempotency_key = $2",
                            tenant_id,
                            _inv_idem_key,
                        )
                        if _inv_exists is not None:
                            _inverse_predicate = None  # suppress insert below

                    if _inverse_predicate is not None:
                        # For property inverse facts check supersession too.
                        _inv_supersedes_id: uuid.UUID | None = None
                        if fact_valid_at is None:
                            _inv_existing = await conn.fetchrow(
                                "SELECT id FROM facts"
                                " WHERE tenant_id = $1"
                                " AND entity_id = $2 AND object_entity_id = $3"
                                " AND scope = $4 AND predicate = $5"
                                " AND validity = 'active' AND valid_at IS NULL",
                                tenant_id,
                                object_entity_id,
                                entity_id,
                                scope,
                                _inverse_predicate,
                            )
                            if _inv_existing:
                                _inv_old_id = _inv_existing["id"]
                                _inv_supersedes_id = _inv_old_id
                                await conn.execute(
                                    "UPDATE facts"
                                    " SET validity = 'superseded', invalid_at = $2"
                                    " WHERE id = $1",
                                    _inv_old_id,
                                    now,
                                )

                        _inv_fact_id = uuid.uuid4()
                        # Inverse subject/content: swap labels.
                        _inv_subject = content  # object entity label used as subject
                        _inv_content = subject  # forward subject becomes content
                        _inv_searchable = f"{_inv_subject} {_inverse_predicate} {_inv_content}"
                        _inv_embedding = embedding_engine.embed(_inv_searchable)
                        _inv_search_text = preprocess_text(_inv_searchable)
                        await _insert_fact_record(
                            conn,
                            fact_id=_inv_fact_id,
                            subject=_inv_subject,
                            predicate=_inverse_predicate,
                            content=_inv_content,
                            embedding=_inv_embedding,
                            search_text=_inv_search_text,
                            importance=importance,
                            decay_rate=decay_rate,
                            permanence=permanence,
                            source_butler=source_butler,
                            source_episode_id=source_episode_id,
                            supersedes_id=_inv_supersedes_id,
                            scope=scope,
                            now=now,
                            tags_json=tags_json,
                            meta_json=meta_json,
                            entity_id=object_entity_id,  # swapped
                            object_entity_id=entity_id,  # swapped
                            fact_valid_at=fact_valid_at,
                            tenant_id=tenant_id,
                            request_id=request_id,
                            idempotency_key=_inv_idem_key,
                            retention_class=retention_class,
                            sensitivity=sensitivity,
                        )

                        if _inv_supersedes_id:
                            await conn.execute(
                                "INSERT INTO memory_links"
                                " (source_type, source_id, target_type, target_id, relation)"
                                " VALUES ('fact', $1, 'fact', $2, 'supersedes')",
                                _inv_fact_id,
                                _inv_supersedes_id,
                            )

            # D4: Auto-registration of novel predicates.
            # When a predicate is NOT in the registry, insert it with flags
            # inferred from the call parameters.  ON CONFLICT DO NOTHING makes
            # this concurrent-safe: the first writer wins; subsequent writers
            # for the same predicate silently succeed.
            # Novel predicates start with status='proposed' — not yet curated.
            if _registry_row is None:
                _inferred_is_edge = object_entity_id is not None
                _inferred_is_temporal = valid_at is not None

                # Reuse _subject_entity_type already fetched in the entity-validation
                # step above — no additional round-trip needed.
                _inferred_subject_type: str | None = _subject_entity_type

                await conn.execute(
                    """
                    INSERT INTO predicate_registry
                        (name, is_edge, is_temporal, expected_subject_type,
                         description, status)
                    VALUES ($1, $2, $3, $4, NULL, 'proposed')
                    ON CONFLICT (name) DO NOTHING
                    """,
                    predicate,
                    _inferred_is_edge,
                    _inferred_is_temporal,
                    _inferred_subject_type,
                )

            # Usage tracking: increment usage_count and update last_used_at.
            # Best-effort — if the column doesn't exist yet (pre-migration
            # environment) the error is silently swallowed.
            try:
                await conn.execute(
                    """
                    UPDATE predicate_registry
                    SET usage_count = usage_count + 1, last_used_at = now()
                    WHERE name = $1
                    """,
                    predicate,
                )
            except Exception:
                # usage_count column may not exist yet (pre-mem_023 env).
                # Log at debug so unexpected failures (e.g. SQL bugs) are discoverable.
                logger.debug(
                    "Failed to update predicate usage tracking;"
                    " expected in pre-migration environments.",
                    exc_info=True,
                )

    # -------------------------------------------------------------------------
    # Write-behind to shared.memory_catalog (best-effort, non-blocking).
    # The canonical fact is already committed above.  Any failure here is
    # logged as a warning and does NOT raise — catalog is eventually consistent.
    # -------------------------------------------------------------------------
    if enable_shared_catalog and source_schema:
        try:
            await _upsert_catalog(
                pool,
                source_schema=source_schema,
                source_table="facts",
                source_id=fact_id,
                source_butler=source_butler,
                tenant_id=tenant_id,
                entity_id=entity_id,
                summary=f"{subject} {predicate}: {content}",
                embedding=embedding,
                search_text=search_text,
                memory_type="fact",
                # Spec-required enrichment fields from the source fact row.
                title=f"{subject} {predicate}",
                predicate=predicate,
                scope=scope,
                valid_at=valid_at,
                confidence=1.0,
                importance=importance,
                object_entity_id=object_entity_id,
            )
        except Exception:
            logger.warning(
                "memory_catalog: failed to upsert catalog entry for fact %s (schema=%r)",
                fact_id,
                source_schema,
                exc_info=True,
            )

    # Build the return dict.  Include "suggestions" only when there are close
    # matches — omit the key entirely when there are none (per spec: "the
    # response MUST NOT include a 'suggestions' key" for no-match cases).
    # Include "warning" when the predicate is deprecated.
    # D7: Include "warnings" when type mismatches were detected; omit otherwise.
    # Include "resolved_from" when the input predicate was an alias that was
    # transparently rewritten to the canonical predicate name.
    result: dict = {"id": fact_id, "supersedes_id": supersedes_id}
    if _fuzzy_suggestions:
        result["suggestions"] = _fuzzy_suggestions
    if _deprecation_warning:
        result["warning"] = _deprecation_warning
    if _type_warnings:
        result["warnings"] = _type_warnings
    if _resolved_from:
        result["resolved_from"] = _resolved_from
    return result


async def store_rule(
    pool: Pool,
    content: str,
    embedding_engine: EmbeddingEngine,
    *,
    scope: str = "global",
    tags: list[str] | None = None,
    source_butler: str | None = None,
    source_episode_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    tenant_id: str = "owner",
    request_id: str | None = None,
    retention_class: str = "rule",
    sensitivity: str = "normal",
    enable_shared_catalog: bool = False,
    source_schema: str | None = None,
) -> uuid.UUID:
    """Store a new behavioral rule as a candidate.

    Rules start as candidates with confidence=0.5 and effectiveness_score=0.0.
    They progress through maturity levels (candidate -> established -> proven)
    as they accumulate successful applications.

    Args:
        pool: asyncpg connection pool for the memory database.
        content: The rule description text.
        embedding_engine: EmbeddingEngine for generating semantic vectors.
        scope: Visibility scope ('global' or butler-specific).
        tags: Optional list of string tags.
        source_butler: Name of the butler that proposed this rule.
        source_episode_id: Optional source episode UUID.
        metadata: Optional JSONB metadata dict.
        tenant_id: Tenant scope for multi-tenant isolation (default 'owner').
        request_id: Optional request trace ID for correlation.
        retention_class: Retention policy class for the rule (default 'rule').
            Controls lifecycle management behaviour.
        sensitivity: Data sensitivity classification (default 'normal').
            Use 'pii' for personally-identifiable information, etc.
        enable_shared_catalog: When True, write a summary row to
            ``shared.memory_catalog`` after the canonical rule is stored.
            Catalog write failure is logged as a warning and does NOT block
            the canonical write.  Defaults to False.
        source_schema: The butler schema name used as ``source_schema`` in the
            catalog row (e.g. ``'health'``).  Required when
            ``enable_shared_catalog=True``; ignored otherwise.

    Returns:
        The UUID of the newly created rule.
    """
    rule_id = uuid.uuid4()
    embedding = embedding_engine.embed(content)
    search_text = preprocess_text(content)
    now = datetime.now(UTC)
    tags_json = json.dumps(tags or [])
    meta_json = json.dumps(metadata or {})

    sql = f"""
        INSERT INTO rules (id, content, embedding, search_vector, scope, maturity,
                           confidence, decay_rate, effectiveness_score,
                           applied_count, success_count, harmful_count,
                           source_episode_id, source_butler, created_at, tags, metadata,
                           tenant_id, request_id, retention_class, sensitivity)
        VALUES ($1, $2, $3, {tsvector_sql("$4")}, $5, 'candidate',
                0.5, 0.01, 0.0,
                0, 0, 0,
                $6, $7, $8, $9, $10, $11, $12, $13, $14)
    """

    await pool.execute(
        sql,
        rule_id,
        content,
        str(embedding),
        search_text,
        scope,
        source_episode_id,
        source_butler,
        now,
        tags_json,
        meta_json,
        tenant_id,
        request_id,
        retention_class,
        sensitivity,
    )

    # -------------------------------------------------------------------------
    # Write-behind to shared.memory_catalog (best-effort, non-blocking).
    # -------------------------------------------------------------------------
    if enable_shared_catalog and source_schema:
        try:
            await _upsert_catalog(
                pool,
                source_schema=source_schema,
                source_table="rules",
                source_id=rule_id,
                source_butler=source_butler,
                tenant_id=tenant_id,
                entity_id=None,
                summary=content,
                embedding=embedding,
                search_text=search_text,
                memory_type="rule",
                # Spec-required enrichment fields from the source rule row.
                title=content[:100],
                scope=scope,
            )
        except Exception:
            logger.warning(
                "memory_catalog: failed to upsert catalog entry for rule %s (schema=%r)",
                rule_id,
                source_schema,
                exc_info=True,
            )

    return rule_id


# Memory links CRUD
# ---------------------------------------------------------------------------


async def create_link(
    pool: Pool,
    source_type: str,
    source_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    relation: str,
) -> None:
    """Create a link between two memory items.

    Args:
        pool: asyncpg connection pool.
        source_type: Type of the source memory ('episode', 'fact', 'rule').
        source_id: UUID of the source memory.
        target_type: Type of the target memory.
        target_id: UUID of the target memory.
        relation: Relationship type (derived_from, supports, contradicts, supersedes, related_to).

    Raises:
        ValueError: If relation or memory types are invalid.
    """
    if relation not in _VALID_RELATIONS:
        raise ValueError(
            f"Invalid relation: {relation!r}. Must be one of {sorted(_VALID_RELATIONS)}"
        )
    if source_type not in _VALID_MEMORY_TYPES:
        raise ValueError(
            f"Invalid source_type: {source_type!r}. Must be one of {sorted(_VALID_MEMORY_TYPES)}"
        )
    if target_type not in _VALID_MEMORY_TYPES:
        raise ValueError(
            f"Invalid target_type: {target_type!r}. Must be one of {sorted(_VALID_MEMORY_TYPES)}"
        )

    await pool.execute(
        "INSERT INTO memory_links (source_type, source_id, target_type, target_id, relation) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (source_type, source_id, target_type, target_id) DO NOTHING",
        source_type,
        source_id,
        target_type,
        target_id,
        relation,
    )


async def get_links(
    pool: Pool,
    memory_type: str,
    memory_id: uuid.UUID,
    *,
    direction: str = "both",
) -> list[dict]:
    """Get all links for a memory item.

    Args:
        pool: asyncpg connection pool.
        memory_type: Type of the memory ('episode', 'fact', 'rule').
        memory_id: UUID of the memory item.
        direction: 'outgoing' (source), 'incoming' (target), or 'both'.

    Returns:
        List of dicts with keys: source_type, source_id, target_type, target_id,
        relation, created_at.

    Raises:
        ValueError: If memory_type is invalid.
    """
    if memory_type not in _VALID_MEMORY_TYPES:
        raise ValueError(f"Invalid memory_type: {memory_type!r}")

    results: list[dict] = []

    if direction in ("outgoing", "both"):
        rows = await pool.fetch(
            "SELECT source_type, source_id, target_type, target_id, relation, created_at "
            "FROM memory_links WHERE source_type = $1 AND source_id = $2",
            memory_type,
            memory_id,
        )
        results.extend(dict(r) for r in rows)

    if direction in ("incoming", "both"):
        rows = await pool.fetch(
            "SELECT source_type, source_id, target_type, target_id, relation, created_at "
            "FROM memory_links WHERE target_type = $1 AND target_id = $2",
            memory_type,
            memory_id,
        )
        results.extend(dict(r) for r in rows)

    return results


# ---------------------------------------------------------------------------
# Memory retrieval with reference bumping
# ---------------------------------------------------------------------------


async def get_memory(
    pool: Pool,
    memory_type: str,
    memory_id: uuid.UUID,
) -> dict | None:
    """Retrieve a single memory by type and UUID, bumping its reference count.

    Atomically increments ``reference_count`` by 1 and sets
    ``last_referenced_at`` to now. Returns the full record as a dict,
    or ``None`` if not found.

    Args:
        pool: asyncpg connection pool.
        memory_type: One of 'episode', 'fact', 'rule'.
        memory_id: The UUID of the memory item.

    Returns:
        A dict of the full record, or None if not found.

    Raises:
        ValueError: If memory_type is invalid.
    """
    if memory_type not in _VALID_MEMORY_TYPES:
        raise ValueError(
            f"Invalid memory_type: {memory_type!r}. Must be one of {sorted(_VALID_MEMORY_TYPES)}"
        )

    table = _TYPE_TABLE[memory_type]

    # Bump reference_count and last_referenced_at, returning the updated row
    row = await pool.fetchrow(
        f"UPDATE {table} "
        f"SET reference_count = reference_count + 1, last_referenced_at = now() "
        f"WHERE id = $1 "
        f"RETURNING *",
        memory_id,
    )

    if row is None:
        return None

    return dict(row)


# ---------------------------------------------------------------------------
# Soft-delete (forget)
# ---------------------------------------------------------------------------


async def forget_memory(
    pool: Pool,
    memory_type: str,
    memory_id: uuid.UUID,
) -> bool:
    """Soft-delete a memory by marking it as forgotten.

    The approach varies by memory type:

    - **facts**: sets ``validity`` to ``'retracted'``.
    - **episodes**: sets ``expires_at`` to ``now()`` (immediate expiry).
    - **rules**: merges ``{"forgotten": true}`` into the ``metadata`` JSONB column.

    The memory remains in the database but is excluded from retrieval.

    Args:
        pool: asyncpg connection pool for the memory database.
        memory_type: One of ``'episode'``, ``'fact'``, or ``'rule'``.
        memory_id: UUID of the memory row to forget.

    Returns:
        ``True`` if the memory was found and updated, ``False`` if not found.

    Raises:
        ValueError: If *memory_type* is not one of the valid types.
    """
    if memory_type not in _VALID_MEMORY_TYPES:
        raise ValueError(
            f"Invalid memory_type {memory_type!r}; expected one of {sorted(_VALID_MEMORY_TYPES)}"
        )

    if memory_type == "fact":
        result = await pool.execute(
            "UPDATE facts SET validity = 'retracted' WHERE id = $1",
            memory_id,
        )
    elif memory_type == "episode":
        result = await pool.execute(
            "UPDATE episodes SET expires_at = now() WHERE id = $1",
            memory_id,
        )
    else:  # rule
        result = await pool.execute(
            "UPDATE rules SET metadata = metadata || '{\"forgotten\": true}'::jsonb WHERE id = $1",
            memory_id,
        )

    # asyncpg execute returns a status string like "UPDATE 1" or "UPDATE 0"
    return result.endswith("1")


# ---------------------------------------------------------------------------
# Confirm (reset confidence decay timer)
# ---------------------------------------------------------------------------


async def confirm_memory(
    pool: Pool,
    memory_type: str,
    memory_id: uuid.UUID,
) -> bool:
    """Confirm a fact or rule is still accurate, resetting confidence decay.

    Updates ``last_confirmed_at`` to now. This effectively resets the
    confidence decay timer, restoring effective confidence to its base level.

    Episodes cannot be confirmed (they don't have confidence decay) and
    attempting to do so raises a ValueError.

    Args:
        pool: asyncpg connection pool.
        memory_type: One of 'fact' or 'rule'.
        memory_id: UUID of the memory to confirm.

    Returns:
        True if the memory was found and updated, False if not found.

    Raises:
        ValueError: If memory_type is 'episode' or invalid.
    """
    if memory_type not in _VALID_MEMORY_TYPES:
        raise ValueError(
            f"Invalid memory_type: {memory_type!r}. Must be one of {sorted(_VALID_MEMORY_TYPES)}"
        )
    if memory_type == "episode":
        raise ValueError("Episodes cannot be confirmed — they don't have confidence decay")

    table = _TYPE_TABLE[memory_type]
    result = await pool.execute(
        f"UPDATE {table} SET last_confirmed_at = now() WHERE id = $1",
        memory_id,
    )
    return result.endswith("1")


# ---------------------------------------------------------------------------
# Rule feedback — mark_helpful
# ---------------------------------------------------------------------------


async def mark_helpful(
    pool: Pool,
    rule_id: uuid.UUID,
    *,
    session_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> dict | None:
    """Mark a rule as having been applied successfully.

    Atomically increments ``applied_count`` and ``success_count``,
    recalculates ``effectiveness_score``, updates ``last_applied_at``,
    evaluates whether the rule qualifies for maturity promotion, and
    inserts a ``rule_applications`` audit row with ``outcome='helpful'``.

    Effectiveness formula::

        effectiveness = success_count / applied_count

    Promotion thresholds:

    - candidate -> established: success_count >= 5 AND effectiveness >= 0.6
    - established -> proven: success_count >= 15 AND effectiveness >= 0.8
      AND age >= 30 days

    Args:
        pool: asyncpg connection pool.
        rule_id: UUID of the rule.
        session_id: Optional session UUID for audit correlation.
        request_id: Optional request trace ID for audit correlation.

    Returns:
        Updated rule as dict, or None if rule not found.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Increment counts and update timestamp in one atomic UPDATE
            row = await conn.fetchrow(
                "UPDATE rules "
                "SET applied_count = applied_count + 1, "
                "    success_count = success_count + 1, "
                "    last_applied_at = now() "
                "WHERE id = $1 "
                "RETURNING *",
                rule_id,
            )
            if row is None:
                return None

            row = dict(row)

            # Recalculate effectiveness
            applied = row["applied_count"]
            success = row["success_count"]
            effectiveness = success / applied if applied > 0 else 0.0

            # Evaluate maturity promotion
            current_maturity = row["maturity"]
            new_maturity = current_maturity

            if current_maturity == "candidate":
                if success >= 5 and effectiveness >= 0.6:
                    new_maturity = "established"
            elif current_maturity == "established":
                age_days = (datetime.now(UTC) - row["created_at"]).days
                if success >= 15 and effectiveness >= 0.8 and age_days >= 30:
                    new_maturity = "proven"

            # Persist effectiveness score and (possibly promoted) maturity
            await conn.execute(
                "UPDATE rules SET effectiveness_score = $1, maturity = $2 WHERE id = $3",
                effectiveness,
                new_maturity,
                rule_id,
            )

            # Write rule_applications audit row (additive; does not replace counters)
            tenant_id = row.get("tenant_id", "owner")
            await conn.execute(
                "INSERT INTO rule_applications "
                "    (tenant_id, rule_id, session_id, request_id, outcome) "
                "VALUES ($1, $2, $3, $4, 'helpful')",
                tenant_id,
                rule_id,
                session_id,
                request_id,
            )

            row["effectiveness_score"] = effectiveness
            row["maturity"] = new_maturity

            return row


# Rule feedback — mark_harmful
# ---------------------------------------------------------------------------


async def mark_harmful(
    pool: Pool,
    rule_id: uuid.UUID,
    reason: str | None = None,
    *,
    session_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> dict | None:
    """Mark a rule as having caused problems.

    Increments ``harmful_count`` and ``applied_count``, recalculates
    ``effectiveness_score`` using a 4x penalty for harmful marks::

        effectiveness = success / (success + 4 * harmful + 0.01)

    The +0.01 prevents division by zero.

    Evaluates demotion:
    - established -> candidate if effectiveness < 0.6
    - proven -> established if effectiveness < 0.8

    If harmful_count >= 3 and effectiveness < 0.3, sets a flag in metadata
    indicating anti-pattern inversion is needed (will be handled by the
    anti-pattern inversion function).

    Stores the reason (if provided) in metadata.harmful_reasons list.

    Also inserts a ``rule_applications`` audit row with ``outcome='harmful'``.
    The audit row's ``notes`` field includes the reason when provided.

    Args:
        pool: asyncpg connection pool.
        rule_id: UUID of the rule.
        reason: Optional reason why the rule was harmful.
        session_id: Optional session UUID for audit correlation.
        request_id: Optional request trace ID for audit correlation.

    Returns:
        Updated rule as dict, or None if rule not found.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Increment counts
            row = await conn.fetchrow(
                "UPDATE rules "
                "SET applied_count = applied_count + 1, "
                "    harmful_count = harmful_count + 1, "
                "    last_applied_at = now() "
                "WHERE id = $1 "
                "RETURNING *",
                rule_id,
            )
            if row is None:
                return None

            row = dict(row)

            # Recalculate effectiveness with 4x harmful penalty
            success = row["success_count"]
            harmful = row["harmful_count"]
            effectiveness = success / (success + 4 * harmful + 0.01)

            # Evaluate demotion
            current_maturity = row["maturity"]
            new_maturity = current_maturity

            if current_maturity == "established" and effectiveness < 0.6:
                new_maturity = "candidate"
            elif current_maturity == "proven" and effectiveness < 0.8:
                new_maturity = "established"

            # Update metadata with reason if provided
            metadata = row.get("metadata", {})
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            if reason:
                reasons = metadata.get("harmful_reasons", [])
                reasons.append(reason)
                metadata["harmful_reasons"] = reasons

            # Check for anti-pattern inversion trigger
            if harmful >= 3 and effectiveness < 0.3:
                metadata["needs_inversion"] = True

            metadata_json = json.dumps(metadata)

            # Persist changes
            await conn.execute(
                "UPDATE rules "
                "SET effectiveness_score = $1, maturity = $2, metadata = $3 "
                "WHERE id = $4",
                effectiveness,
                new_maturity,
                metadata_json,
                rule_id,
            )

            # Write rule_applications audit row (additive; does not replace counters)
            tenant_id = row.get("tenant_id", "owner")
            audit_notes: dict = {}
            if reason:
                audit_notes["reason"] = reason
            audit_notes_json = json.dumps(audit_notes)
            await conn.execute(
                "INSERT INTO rule_applications "
                "    (tenant_id, rule_id, session_id, request_id, outcome, notes) "
                "VALUES ($1, $2, $3, $4, 'harmful', $5::jsonb)",
                tenant_id,
                rule_id,
                session_id,
                request_id,
                audit_notes_json,
            )

            row["effectiveness_score"] = effectiveness
            row["maturity"] = new_maturity
            row["metadata"] = metadata

            return row


# ---------------------------------------------------------------------------
# Anti-pattern inversion
# ---------------------------------------------------------------------------


async def invert_to_anti_pattern(
    pool: Pool,
    rule_id: uuid.UUID,
    embedding_engine: EmbeddingEngine,
) -> dict | None:
    """Invert a repeatedly harmful rule into an anti-pattern warning.

    Rewrites the rule content to serve as a warning, re-embeds it,
    and sets maturity to 'anti_pattern'. The original content is
    preserved in metadata.original_content.

    This is triggered when harmful_count >= 3 AND effectiveness < 0.3.

    Args:
        pool: asyncpg connection pool.
        rule_id: UUID of the rule.
        embedding_engine: EmbeddingEngine for re-embedding the new content.

    Returns:
        Updated rule as dict, or None if rule not found or doesn't
        meet anti-pattern criteria.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM rules WHERE id = $1",
                rule_id,
            )
            if row is None:
                return None

            row = dict(row)

            # Check criteria
            if row["harmful_count"] < 3 or row["effectiveness_score"] >= 0.3:
                return None

            # Already an anti-pattern
            if row["maturity"] == "anti_pattern":
                return row

            # Build anti-pattern content
            original_content = row["content"]
            metadata = row.get("metadata", {})
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            reasons = metadata.get("harmful_reasons", [])
            reasons_text = "; ".join(reasons) if reasons else "repeated failures"

            anti_pattern_content = (
                f"ANTI-PATTERN: Do NOT {original_content}. "
                f"This caused problems because: {reasons_text}"
            )

            # Re-embed the new content
            new_embedding = embedding_engine.embed(anti_pattern_content)
            search_text = preprocess_text(anti_pattern_content)

            # Preserve original content in metadata
            metadata["original_content"] = original_content
            metadata["needs_inversion"] = False
            metadata_json = json.dumps(metadata)

            # Update the rule
            sql = f"""
                UPDATE rules
                SET content = $1,
                    embedding = $2,
                    search_vector = {tsvector_sql("$3")},
                    maturity = 'anti_pattern',
                    metadata = $4
                WHERE id = $5
            """
            await conn.execute(
                sql,
                anti_pattern_content,
                str(new_embedding),
                search_text,
                metadata_json,
                rule_id,
            )

            row["content"] = anti_pattern_content
            row["embedding"] = new_embedding
            row["maturity"] = "anti_pattern"
            row["metadata"] = metadata

            return row


# ---------------------------------------------------------------------------
# Decay sweep — fading & expiring low-confidence memories
# ---------------------------------------------------------------------------


async def run_decay_sweep(pool: Pool) -> dict:
    """Run a confidence decay sweep across all active facts and rules.

    For each active fact and rule (excluding permanent ones with decay_rate=0.0):
    1. Compute effective_confidence = confidence * exp(-decay_rate * days_elapsed)
       where days_elapsed = (now - last_confirmed_at).total_seconds() / 86400
    2. Thresholds are read per-class from memory_policies:
         - fading_threshold  = min_retrieval_confidence
         - expiry_threshold  = min_retrieval_confidence * 0.25
       If the retention_class is not found in memory_policies, fall back to
       the hardcoded defaults (fading=0.2, expiry=0.05) and log a warning.
    3. If effective_confidence < expiry_threshold:
         - For facts with archive_before_delete=true: archive first, then expire.
           If archival fails, skip expiry (fail-closed).
         - Otherwise: set validity='expired' (facts) or metadata.forgotten=true (rules)
    4. If expiry_threshold <= effective_confidence < fading_threshold:
         set metadata.status='fading'
    5. Otherwise: clear metadata.status if it was 'fading'

    Returns:
        dict with keys: facts_checked, rules_checked, facts_fading, rules_fading,
        facts_expired, rules_expired
    """
    # Hardcoded fallback defaults (used when policy is missing for a class)
    _DEFAULT_FADING_THRESHOLD = 0.2
    _DEFAULT_EXPIRY_THRESHOLD = 0.05

    now = datetime.now(UTC)
    stats = {
        "facts_checked": 0,
        "rules_checked": 0,
        "facts_fading": 0,
        "rules_fading": 0,
        "facts_expired": 0,
        "rules_expired": 0,
    }

    async with pool.acquire() as conn:
        # Load all retention policies upfront to avoid per-row queries
        policy_rows = await conn.fetch(
            "SELECT retention_class, min_retrieval_confidence, archive_before_delete "
            "FROM memory_policies"
        )
        policies: dict[str, dict] = {}
        for row in policy_rows:
            rc = row["retention_class"]
            policies[rc] = {
                "min_conf": row["min_retrieval_confidence"],
                "archive_before_delete": row["archive_before_delete"],
            }

        def _get_thresholds(retention_class: str | None) -> tuple[float, float, bool]:
            """Return (fading_threshold, expiry_threshold, archive_before_delete)."""
            rc = retention_class or ""
            if rc in policies:
                min_conf = policies[rc]["min_conf"]
                return min_conf, min_conf * 0.25, policies[rc]["archive_before_delete"]
            logger.warning(
                "run_decay_sweep: retention_class %r not found in memory_policies; "
                "using hardcoded defaults (fading=%.2f, expiry=%.2f)",
                rc,
                _DEFAULT_FADING_THRESHOLD,
                _DEFAULT_EXPIRY_THRESHOLD,
            )
            return _DEFAULT_FADING_THRESHOLD, _DEFAULT_EXPIRY_THRESHOLD, False

        # ----- Process facts -----
        facts = await conn.fetch(
            "SELECT id, confidence, decay_rate, last_confirmed_at, created_at, "
            "metadata, retention_class "
            "FROM facts WHERE validity = 'active' AND decay_rate > 0.0"
        )

        for fact in facts:
            stats["facts_checked"] += 1
            anchor = fact["last_confirmed_at"] or fact["created_at"]
            days = (now - anchor).total_seconds() / 86400.0
            eff = fact["confidence"] * math.exp(-fact["decay_rate"] * days)

            metadata = fact["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            fading_thresh, expiry_thresh, archive_first = _get_thresholds(
                fact.get("retention_class")
            )

            if eff < expiry_thresh:
                if archive_first:
                    try:
                        metadata["archived_at"] = now.isoformat()
                        metadata["archived_content"] = True
                        await conn.execute(
                            "UPDATE facts SET validity = 'expired', metadata = $1 WHERE id = $2",
                            json.dumps(metadata),
                            fact["id"],
                        )
                    except Exception:
                        logger.error(
                            "run_decay_sweep: archival failed for fact %s; skipping expiry "
                            "(fail-closed for archive_before_delete class)",
                            fact["id"],
                        )
                        continue
                else:
                    await conn.execute(
                        "UPDATE facts SET validity = 'expired' WHERE id = $1",
                        fact["id"],
                    )
                stats["facts_expired"] += 1
            elif eff < fading_thresh:
                metadata["status"] = "fading"
                await conn.execute(
                    "UPDATE facts SET metadata = $1 WHERE id = $2",
                    json.dumps(metadata),
                    fact["id"],
                )
                stats["facts_fading"] += 1
            else:
                if metadata.get("status") == "fading":
                    del metadata["status"]
                    await conn.execute(
                        "UPDATE facts SET metadata = $1 WHERE id = $2",
                        json.dumps(metadata),
                        fact["id"],
                    )

        # ----- Process rules -----
        rules = await conn.fetch(
            "SELECT id, confidence, decay_rate, last_confirmed_at, created_at, "
            "metadata, retention_class "
            "FROM rules WHERE maturity != 'anti_pattern' AND decay_rate > 0.0 "
            "AND (metadata->>'forgotten')::boolean IS NOT TRUE"
        )

        for rule in rules:
            stats["rules_checked"] += 1
            anchor = rule["last_confirmed_at"] or rule["created_at"]
            days = (now - anchor).total_seconds() / 86400.0
            eff = rule["confidence"] * math.exp(-rule["decay_rate"] * days)

            metadata = rule["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            fading_thresh, expiry_thresh, _archive_first = _get_thresholds(
                rule.get("retention_class")
            )

            if eff < expiry_thresh:
                metadata["forgotten"] = True
                await conn.execute(
                    "UPDATE rules SET metadata = $1 WHERE id = $2",
                    json.dumps(metadata),
                    rule["id"],
                )
                stats["rules_expired"] += 1
            elif eff < fading_thresh:
                metadata["status"] = "fading"
                await conn.execute(
                    "UPDATE rules SET metadata = $1 WHERE id = $2",
                    json.dumps(metadata),
                    rule["id"],
                )
                stats["rules_fading"] += 1
            else:
                if metadata.get("status") == "fading":
                    del metadata["status"]
                    await conn.execute(
                        "UPDATE rules SET metadata = $1 WHERE id = $2",
                        json.dumps(metadata),
                        rule["id"],
                    )

    return stats


async def purge_superseded_facts(pool: Pool, *, older_than_days: int = 7) -> dict:
    """Delete superseded facts and orphaned machine-generated facts.

    Superseded facts are dead weight — they are never queried. This function
    removes them to reclaim disk space and keep index sizes manageable.

    Also purges ``ha_state`` facts regardless of validity. These are
    machine-generated HA entity snapshots that should not persist — HA state
    is always available in real-time via the HA API. Any surviving active
    ``ha_state`` facts are zombies left over from when the snapshot loop was
    disabled.

    Args:
        pool: asyncpg connection pool.
        older_than_days: Only delete superseded facts created more than this
            many days ago (default 7). Keeps recent superseded facts for
            short-term forensics.

    Returns:
        dict with keys ``deleted`` (superseded rows removed) and
        ``deleted_ha_state`` (ha_state rows removed).
    """
    result = await pool.execute(
        "DELETE FROM facts "
        "WHERE validity = 'superseded' "
        "AND created_at < now() - make_interval(days => $1)",
        older_than_days,
    )
    deleted = int(result.split()[-1]) if result else 0

    # Purge orphaned ha_state facts — machine-generated snapshots that should
    # not persist now that the snapshot loop is disabled.
    ha_result = await pool.execute(
        "DELETE FROM facts WHERE predicate = 'ha_state'",
    )
    deleted_ha = int(ha_result.split()[-1]) if ha_result else 0

    return {"deleted": deleted, "deleted_ha_state": deleted_ha}

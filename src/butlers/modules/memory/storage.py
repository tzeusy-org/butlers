"""Core memory storage operations for the Memory Butler.

Provides async functions for storing episodes, facts, and rules in the
memory database.  All functions accept an asyncpg connection pool and
use the EmbeddingEngine for semantic vector generation.
"""

from __future__ import annotations

import importlib.util
import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncpg import Pool

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
# Public API — Storage
# ---------------------------------------------------------------------------


async def store_episode(
    pool: Pool,
    content: str,
    butler: str,
    embedding_engine: EmbeddingEngine,
    *,
    session_id: uuid.UUID | None = None,
    importance: float = 5.0,
    metadata: dict | None = None,
) -> uuid.UUID:
    """Store a raw episode from a butler runtime session.

    Generates both a semantic embedding and a full-text search vector for the
    content, then inserts a row into the ``episodes`` table.

    Args:
        pool: asyncpg connection pool for the memory database.
        content: Raw episode text content.
        butler: Name of the source butler.
        embedding_engine: EmbeddingEngine instance for generating vectors.
        session_id: Optional UUID of the source runtime session.
        importance: Importance rating (default 5.0).
        metadata: Optional JSONB metadata dict.

    Returns:
        The UUID of the newly created episode row.
    """
    episode_id = uuid.uuid4()
    embedding = embedding_engine.embed(content)
    search_text = preprocess_text(content)
    expires_at = datetime.now(UTC) + timedelta(days=_DEFAULT_EPISODE_TTL_DAYS)

    sql = f"""
        INSERT INTO episodes (id, butler, session_id, content, embedding, search_vector,
                              importance, expires_at, metadata)
        VALUES ($1, $2, $3, $4, $5, {tsvector_sql("$6")}, $7, $8, $9)
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
    )

    return episode_id


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
) -> uuid.UUID:
    """Store a distilled fact with optional supersession.

    If an active fact with the same ``(subject, predicate)`` already exists:

    1. Set the old fact's ``validity`` to ``'superseded'``.
    2. Link the new fact to the old one via ``supersedes_id``.
    3. Create a ``memory_links`` row with ``relation='supersedes'``.

    Returns:
        The UUID of the newly created fact.
    """
    fact_id = uuid.uuid4()
    searchable = f"{subject} {predicate} {content}"
    embedding = embedding_engine.embed(searchable)
    search_text = preprocess_text(searchable)
    decay_rate = validate_permanence(permanence)
    now = datetime.now(UTC)
    tags_json = json.dumps(tags or [])
    meta_json = json.dumps(metadata or {})

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Check for existing active fact with same subject+predicate
            existing = await conn.fetchrow(
                "SELECT id FROM facts "
                "WHERE subject = $1 AND predicate = $2 AND validity = 'active'",
                subject,
                predicate,
            )

            supersedes_id = None
            if existing:
                old_id = existing["id"]
                supersedes_id = old_id
                # Mark old fact as superseded
                await conn.execute(
                    "UPDATE facts SET validity = 'superseded' WHERE id = $1",
                    old_id,
                )

            # Insert new fact
            sql = f"""
                INSERT INTO facts (
                    id, subject, predicate, content, embedding, search_vector,
                    importance, confidence, decay_rate, permanence, source_butler,
                    source_episode_id, supersedes_id, validity, scope,
                    created_at, last_confirmed_at, tags, metadata
                )
                VALUES (
                    $1, $2, $3, $4, $5, {tsvector_sql("$6")},
                    $7, $8, $9, $10, $11,
                    $12, $13, 'active', $14,
                    $15, $15, $16, $17
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

    return fact_id


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
                           source_episode_id, source_butler, created_at, tags, metadata)
        VALUES ($1, $2, $3, {tsvector_sql("$4")}, $5, 'candidate',
                0.5, 0.01, 0.0,
                0, 0, 0,
                $6, $7, $8, $9, $10)
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
) -> dict | None:
    """Mark a rule as having been applied successfully.

    Atomically increments ``applied_count`` and ``success_count``,
    recalculates ``effectiveness_score``, updates ``last_applied_at``,
    and evaluates whether the rule qualifies for maturity promotion.

    Effectiveness formula::

        effectiveness = success_count / applied_count

    Promotion thresholds:

    - candidate -> established: success_count >= 5 AND effectiveness >= 0.6
    - established -> proven: success_count >= 15 AND effectiveness >= 0.8
      AND age >= 30 days

    Args:
        pool: asyncpg connection pool.
        rule_id: UUID of the rule.

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

            row["effectiveness_score"] = effectiveness
            row["maturity"] = new_maturity

            return row


# Rule feedback — mark_harmful
# ---------------------------------------------------------------------------


async def mark_harmful(
    pool: Pool,
    rule_id: uuid.UUID,
    reason: str | None = None,
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

    Args:
        pool: asyncpg connection pool.
        rule_id: UUID of the rule.
        reason: Optional reason why the rule was harmful.

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
    2. If effective_confidence < 0.05: set validity='expired' (facts) or
       metadata.forgotten=true (rules)
    3. If 0.05 <= effective_confidence < 0.2: set metadata.status='fading'
    4. Otherwise: clear metadata.status if it was 'fading'

    Returns:
        dict with keys: facts_checked, rules_checked, facts_fading, rules_fading,
        facts_expired, rules_expired
    """
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
        # ----- Process facts -----
        facts = await conn.fetch(
            "SELECT id, confidence, decay_rate, last_confirmed_at, created_at, metadata "
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

            if eff < 0.05:
                await conn.execute(
                    "UPDATE facts SET validity = 'expired' WHERE id = $1",
                    fact["id"],
                )
                stats["facts_expired"] += 1
            elif eff < 0.2:
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
            "SELECT id, confidence, decay_rate, last_confirmed_at, created_at, metadata "
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

            if eff < 0.05:
                metadata["forgotten"] = True
                await conn.execute(
                    "UPDATE rules SET metadata = $1 WHERE id = $2",
                    json.dumps(metadata),
                    rule["id"],
                )
                stats["rules_expired"] += 1
            elif eff < 0.2:
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

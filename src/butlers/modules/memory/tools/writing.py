"""Memory writing tools — store episodes, facts, and rules."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _storage, get_embedding_engine, validate_tenant_id

logger = logging.getLogger(__name__)


def normalize_predicate(predicate: str) -> str:
    """Normalize a predicate to canonical snake_case form.

    Applies transformations in order:
    1. Lowercase the predicate.
    2. Replace hyphens and spaces with underscores.
    3. Strip a leading ``is_`` prefix.

    This is applied at the MCP tool layer so that LLM-provided predicates
    like ``"Birthday"``, ``"job-title"``, or ``"Is-Parent Of"`` all map to
    their canonical stored forms.  Internal callers of ``store_fact()`` that
    already use canonical predicates are unaffected because they bypass this
    layer.

    Args:
        predicate: Raw predicate string from the MCP caller.

    Returns:
        Normalized predicate string.

    Examples:
        >>> normalize_predicate("Birthday")
        'birthday'
        >>> normalize_predicate("job-title")
        'job_title'
        >>> normalize_predicate("is_parent_of")
        'parent_of'
        >>> normalize_predicate("Is-Parent Of")
        'parent_of'
        >>> normalize_predicate("parent_of")
        'parent_of'
    """
    normalized = predicate.lower().replace("-", "_").replace(" ", "_")
    return normalized.removeprefix("is_")


# ---------------------------------------------------------------------------
# Canonical fact-store layering — writer-side identity boundary
# ---------------------------------------------------------------------------
#
# ``module-memory`` (entity-v3 delta) and ``relationship-entity-lifecycle``
# ("Canonical fact-store layering is binding project-wide") forbid identity-
# contact predicate data from landing in the memory-module ``facts`` table. Its
# single write path is ``relationship_assert_fact()`` into
# ``relationship.entity_facts``. The registry contact predicates are seeded in
# ``relationship.entity_predicate_registry`` (migration
# ``roster/relationship/migrations/014_predicate_registry.py``).
#
# The module-memory spec is explicit that the boundary covers "future contact
# predicates in relationship.entity_predicate_registry", so the rejection set is
# registry-driven, not a fixed list. The static set below is the GUARANTEED FLOOR
# (the predicates seeded at registry 014, in the normalized snake_case form that
# ``normalize_predicate`` produces from the hyphenated registry names) — it is
# always rejected even when the registry cannot be read. ``is_identity_registry_predicate``
# additionally consults a TTL-cached snapshot of the live registry so a newly
# seeded contact predicate is rejected without a code change, while a registry
# read failure (e.g. a role without SELECT grant) degrades to the floor rather
# than failing the write path open.
_IDENTITY_REGISTRY_PREDICATE_FLOOR: frozenset[str] = frozenset(
    {
        "has_email",
        "has_phone",
        "has_handle",
        "has_address",
        "has_birthday",
        "has_website",
    }
)

#: TTL for the cached registry contact-predicate snapshot, in seconds.
_REGISTRY_PREDICATE_CACHE_TTL_S = 300.0

#: Cache state: (expiry_monotonic, normalized predicate set). ``None`` until the
#: first successful (or attempted) registry read.
_registry_predicate_cache: tuple[float, frozenset[str]] | None = None


async def _load_registry_contact_predicates(pool: Pool) -> frozenset[str]:
    """Read the registry's contact predicates, normalized to snake_case.

    Returns the floor on any failure (missing table, missing SELECT grant, DB
    error) so the boundary check never fails open and never raises into the write
    path. The hyphenated registry names (``has-email``) are run through
    :func:`normalize_predicate` so they match the writer's normalized predicate.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT predicate
            FROM relationship.entity_predicate_registry
            WHERE kind = 'contact'
            """
        )
    except Exception as exc:  # noqa: BLE001 — degrade to floor, never fail open
        logger.debug("registry contact-predicate read failed, using floor: %s", exc)
        return _IDENTITY_REGISTRY_PREDICATE_FLOOR
    registry = {normalize_predicate(r["predicate"]) for r in rows}
    # Union with the floor so a registry that is somehow missing a seeded
    # predicate can never relax the boundary below the guaranteed floor.
    return _IDENTITY_REGISTRY_PREDICATE_FLOOR | registry


async def refresh_identity_registry_predicates(pool: Pool) -> frozenset[str]:
    """Refresh and return the cached registry identity-predicate set.

    Caches the union of the static floor and the live registry contact
    predicates for ``_REGISTRY_PREDICATE_CACHE_TTL_S`` seconds. Concurrent
    callers within the TTL reuse the snapshot (no per-write query round-trip).
    """
    global _registry_predicate_cache
    now = time.monotonic()
    cached = _registry_predicate_cache
    if cached is not None and cached[0] > now:
        return cached[1]
    predicates = await _load_registry_contact_predicates(pool)
    _registry_predicate_cache = (now + _REGISTRY_PREDICATE_CACHE_TTL_S, predicates)
    return predicates


def _reset_identity_registry_cache() -> None:
    """Clear the registry-predicate cache (test hook)."""
    global _registry_predicate_cache
    _registry_predicate_cache = None


def is_identity_registry_predicate(
    predicate: str, registry_predicates: frozenset[str] | None = None
) -> bool:
    """Return True if *predicate* is a registry identity-contact predicate.

    Expects the normalized (snake_case) predicate form produced by
    :func:`normalize_predicate`. Identity-contact predicates (``has_email``,
    ``has_phone``, ...) are owned by ``relationship.entity_facts`` and MUST NOT
    be written to the memory-module ``facts`` table.

    ``registry_predicates`` is the registry-driven snapshot (see
    :func:`refresh_identity_registry_predicates`); when omitted, only the static
    floor is checked. Callers on the live write path pass the cached snapshot so
    future-seeded contact predicates are rejected too.
    """
    if registry_predicates is not None and predicate in registry_predicates:
        return True
    return predicate in _IDENTITY_REGISTRY_PREDICATE_FLOOR


# ---------------------------------------------------------------------------
# Canonical fact-store layering — writer-side relational-edge boundary
# ---------------------------------------------------------------------------
#
# ``module-memory`` (relational-edges-single-home delta) forbids edge-facts
# with registry-relational predicates from landing in the memory-module
# ``facts`` table.  Registry-relational edges (``works-at``, ``friend-of``,
# ``knows``, ``family-of``, etc.) MUST be written through
# ``relationship_assert_fact(object_kind='entity')`` into
# ``relationship.entity_facts``.  Narrative edges (``planned_dinner_with``,
# ``wake_coordination``, …) remain legal in ``{schema}.facts``.
#
# The guard fires ONLY when ``object_entity_id`` is set (i.e. the call is an
# edge-fact, not a property-fact).  The rejection set is registry-driven
# (``kind = 'relational'`` rows) with a static floor as the always-on
# guarantee.  Floor includes both the canonical normalized forms and the known
# underscore aliases from ``_PREDICATE_ALIAS_MAP`` in
# ``relationship_assert_fact.py`` that have no exact canonical equivalent
# (``sibling_of → family-of``, ``married_to → partner-of``).
_RELATIONAL_REGISTRY_PREDICATE_FLOOR: frozenset[str] = frozenset(
    {
        # Canonical relational predicates (hyphen→snake after normalize_predicate)
        "knows",
        "family_of",
        "partner_of",
        "parent_of",
        "child_of",
        "colleague_of",
        "friend_of",
        "co_attended",
        "purchased_from",
        "subscribed_to",
        "visited",
        "works_at",
        "member_of",
        # Underscore aliases not covered by the canonical names above
        "sibling_of",  # alias for family-of
        "married_to",  # alias for partner-of
    }
)

#: TTL for the cached registry relational-predicate snapshot, in seconds.
_RELATIONAL_REGISTRY_PREDICATE_CACHE_TTL_S = 300.0

#: Cache state: (expiry_monotonic, normalized predicate set). ``None`` until first read.
_relational_predicate_cache: tuple[float, frozenset[str]] | None = None


async def _load_registry_relational_predicates(pool: Pool) -> frozenset[str]:
    """Read the registry's relational predicates, normalized to snake_case.

    Returns the floor on any failure so the boundary check never fails open
    and never raises into the write path.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT predicate
            FROM relationship.entity_predicate_registry
            WHERE kind = 'relational'
            """
        )
    except Exception as exc:  # noqa: BLE001 — degrade to floor, never fail open
        logger.debug("registry relational-predicate read failed, using floor: %s", exc)
        return _RELATIONAL_REGISTRY_PREDICATE_FLOOR
    registry = {normalize_predicate(r["predicate"]) for r in rows}
    return _RELATIONAL_REGISTRY_PREDICATE_FLOOR | registry


async def refresh_relational_registry_predicates(pool: Pool) -> frozenset[str]:
    """Refresh and return the cached relational-predicate set (TTL-cached)."""
    global _relational_predicate_cache
    now = time.monotonic()
    cached = _relational_predicate_cache
    if cached is not None and cached[0] > now:
        return cached[1]
    predicates = await _load_registry_relational_predicates(pool)
    _relational_predicate_cache = (now + _RELATIONAL_REGISTRY_PREDICATE_CACHE_TTL_S, predicates)
    return predicates


def _reset_relational_registry_cache() -> None:
    """Clear the relational-predicate cache (test hook)."""
    global _relational_predicate_cache
    _relational_predicate_cache = None


def is_relational_registry_predicate(
    predicate: str, registry_predicates: frozenset[str] | None = None
) -> bool:
    """Return True if *predicate* is a registry relational predicate (or alias).

    Expects the normalized (snake_case) predicate form produced by
    :func:`normalize_predicate`. Registry-relational edge-facts MUST NOT be
    written to the memory-module ``facts`` table; use
    ``relationship_assert_fact(object_kind='entity')`` instead.

    ``registry_predicates`` is the TTL-cached registry snapshot (see
    :func:`refresh_relational_registry_predicates`); when omitted, only the
    static floor is checked.
    """
    if registry_predicates is not None and predicate in registry_predicates:
        return True
    return predicate in _RELATIONAL_REGISTRY_PREDICATE_FLOOR


def _extract_request_context(
    request_context: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """Extract tenant_id and request_id from an optional request_context dict.

    Args:
        request_context: Optional dict with 'tenant_id' and/or 'request_id' keys.

    Returns:
        Tuple of (tenant_id, request_id). Defaults to ('shared', None) when
        request_context is None or keys are absent.
    """
    if not request_context:
        return "shared", None
    tenant_id_val = request_context.get("tenant_id")
    request_id = request_context.get("request_id") or None
    tenant_id = "shared" if tenant_id_val in (None, "") else str(tenant_id_val)
    validate_tenant_id(tenant_id)
    return tenant_id, str(request_id) if request_id is not None else None


async def memory_store_episode(
    pool: Pool,
    content: str,
    butler: str,
    *,
    embedding_engine: Any | None = None,
    session_id: str | None = None,
    importance: float = 5.0,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a raw episode from a runtime session.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new episode's ID and expiry timestamp.

    Args:
        pool: asyncpg connection pool.
        content: Episode text.
        butler: Name of the source butler.
        embedding_engine: Optional pre-built EmbeddingEngine. When provided,
            this engine is used directly; when omitted the module-default engine
            (all-MiniLM-L6-v2) is used via ``get_embedding_engine()``.
        session_id: Optional UUID string of the source runtime session.
        importance: Importance rating (default 5.0).
        request_context: Optional dict with 'tenant_id' and 'request_id' for
            multi-tenant isolation and request trace correlation.
    """
    session_id_str = session_id.strip() if session_id else None
    if session_id_str in (None, ""):
        parsed_session_id = None
    else:
        try:
            parsed_session_id = uuid.UUID(session_id_str)
        except ValueError as exc:
            raise ValueError(
                "session_id must be a UUID string for a stored memory episode. "
                "Omit session_id when storing an ad-hoc episode or pass the runtime "
                "session UUID, not a connector message id or other external identifier."
            ) from exc
    tenant_id, request_id = _extract_request_context(request_context)
    result = await _storage.store_episode(
        pool,
        content,
        butler,
        embedding_engine if embedding_engine is not None else get_embedding_engine(),
        session_id=parsed_session_id,
        importance=importance,
        tenant_id=tenant_id,
        request_id=request_id,
    )

    # Backward-compatible: older storage variants may return a mapping.
    if isinstance(result, dict):
        episode_id = result["id"]
        expires_at = result["expires_at"]
    else:
        episode_id = result
        expires_at = await pool.fetchval(
            "SELECT expires_at FROM episodes WHERE id = $1",
            episode_id,
        )
        if expires_at is None:
            ttl_days = getattr(_storage, "_DEFAULT_EPISODE_TTL_DAYS", 7)
            expires_at = datetime.now(UTC) + timedelta(days=ttl_days)

    return {
        "id": str(episode_id),
        "expires_at": expires_at.isoformat(),
    }


async def memory_store_fact(
    pool: Pool,
    embedding_engine,
    subject: str,
    predicate: str,
    content: str,
    *,
    importance: float = 5.0,
    permanence: str = "standard",
    scope: str = "global",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    entity_id: str | None = None,
    object_entity_id: str | None = None,
    valid_at: str | None = None,
    idempotency_key: str | None = None,
    request_context: dict[str, Any] | None = None,
    retention_class: str = "operational",
    sensitivity: str = "normal",
    enable_shared_catalog: bool = False,
    source_schema: str | None = None,
) -> dict[str, Any]:
    """Store a distilled fact, automatically superseding any existing match.

    Accepts an optional ``entity_id`` (UUID string) to anchor the fact to a
    resolved entity.  When ``entity_id`` is provided, uniqueness is enforced
    via ``(entity_id, scope, predicate)``; the ``subject`` field is stored as
    a human-readable label only.  When omitted, existing ``(subject, predicate)``
    behaviour is preserved (backward compatible).

    Accepts an optional ``object_entity_id`` (UUID string) to create an edge-fact
    linking ``entity_id`` (subject) to ``object_entity_id`` (object).  When
    provided, uniqueness is enforced via
    ``(entity_id, object_entity_id, scope, predicate)``.

    Accepts an optional ``valid_at`` ISO-8601 string.  When omitted, the fact
    is stored as a *property fact* (``valid_at = NULL``) and supersedes any
    existing active property fact with the same uniqueness key.  When
    provided, the fact is stored as a *temporal fact* and always coexists with
    other active facts — temporal facts never supersede each other or property
    facts.

    Accepts an optional ``metadata`` dict that is stored verbatim as JSONB in
    the ``facts.metadata`` column.  Callers should include domain-specific
    fields that downstream projectors (e.g. the Chronicler adapters) need to
    derive structured output — for example ``end_time``, ``duration_ms``,
    ``session_id`` for sleep facts.

    Accepts an optional ``request_context`` dict with 'tenant_id' and 'request_id'
    for multi-tenant isolation and request trace correlation.

    Accepts optional ``retention_class`` (default 'operational') and
    ``sensitivity`` (default 'normal') to classify the stored fact for
    lifecycle management and data governance purposes.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new fact's ID and the superseded fact's ID (if any).
    """
    import uuid as _uuid

    # Normalize predicate at the MCP tool layer (D2: normalization is a writing-
    # tool concern, not a storage concern).  Internal callers of store_fact()
    # already use canonical snake_case predicates and bypass this path.
    predicate = normalize_predicate(predicate)

    # Writer-side identity boundary (module-memory + relationship-entity-lifecycle
    # "Canonical fact-store layering"): identity-contact predicates belong in
    # relationship.entity_facts via relationship_assert_fact(), never in the
    # memory-module facts table. The rejection set is registry-driven (the spec
    # covers "future contact predicates in relationship.entity_predicate_registry")
    # via a TTL-cached snapshot, with the static floor as the always-on guarantee.
    registry_predicates = await refresh_identity_registry_predicates(pool)
    if is_identity_registry_predicate(predicate, registry_predicates):
        raise ValueError(
            f"Identity-contact predicate {predicate!r} is out of scope for the "
            "memory facts store. Channel identifiers and identity predicates "
            "(has-email, has-phone, has-handle, has-address, has-birthday, "
            "has-website) MUST be asserted via relationship_assert_fact() into "
            "relationship.entity_facts (canonical identity store). Store only the "
            "narrative context (e.g. 'mentioned switching jobs') as a memory fact."
        )

    # Writer-side relational-edge boundary (module-memory relational-edges-single-home
    # delta): edge-facts with registry-relational predicates belong in
    # relationship.entity_facts via relationship_assert_fact(object_kind='entity').
    # This guard fires only when object_entity_id is set (i.e. this is an edge-fact).
    # Narrative edge-facts (planned_dinner_with, wake_coordination, …) are unaffected.
    if object_entity_id is not None:
        relational_predicates = await refresh_relational_registry_predicates(pool)
        if is_relational_registry_predicate(predicate, relational_predicates):
            raise ValueError(
                f"Registry-relational predicate {predicate!r} is out of scope for the "
                "memory facts store when used as an edge-fact (object_entity_id set). "
                "Registry-relational edges (knows, friend-of, works-at, member-of, "
                "family-of, etc.) MUST be written through "
                "relationship_assert_fact(object_kind='entity') into "
                "relationship.entity_facts. Use a narrative predicate for episodic or "
                "coordination edges that should remain in memory (e.g. "
                "'planned_dinner_with')."
            )

    parsed_entity_id = _uuid.UUID(entity_id) if entity_id is not None else None
    parsed_object_entity_id = _uuid.UUID(object_entity_id) if object_entity_id is not None else None
    parsed_valid_at: datetime | None = None
    if valid_at is not None:
        parsed_valid_at = datetime.fromisoformat(valid_at)
        if parsed_valid_at.tzinfo is None:
            parsed_valid_at = parsed_valid_at.replace(tzinfo=UTC)

    tenant_id, request_id = _extract_request_context(request_context)

    result = await _storage.store_fact(
        pool,
        subject,
        predicate,
        content,
        embedding_engine,
        importance=importance,
        permanence=permanence,
        scope=scope,
        tags=tags,
        metadata=metadata,
        entity_id=parsed_entity_id,
        object_entity_id=parsed_object_entity_id,
        valid_at=parsed_valid_at,
        idempotency_key=idempotency_key,
        tenant_id=tenant_id,
        request_id=request_id,
        retention_class=retention_class,
        sensitivity=sensitivity,
        enable_shared_catalog=enable_shared_catalog,
        source_schema=source_schema,
    )

    # Backward-compatible: older storage variants may return a mapping.
    if isinstance(result, dict):
        fact_id = result["id"]
        superseded_id = result.get("supersedes_id")
        suggestions = result.get("suggestions")
    else:
        fact_id = result
        superseded_id = await pool.fetchval(
            "SELECT supersedes_id FROM facts WHERE id = $1",
            fact_id,
        )
        suggestions = None

    response: dict[str, Any] = {
        "id": str(fact_id),
        "superseded_id": str(superseded_id) if superseded_id else None,
    }
    # Forward fuzzy suggestions when present — omit the key entirely when
    # there are no close matches (keeps the response minimal for registered
    # predicates and truly novel predicates with no similar canonical form).
    if suggestions:
        response["suggestions"] = suggestions
    return response


async def memory_store_rule(
    pool: Pool,
    embedding_engine,
    content: str,
    *,
    scope: str = "global",
    tags: list[str] | None = None,
    request_context: dict[str, Any] | None = None,
    retention_class: str = "rule",
    sensitivity: str = "normal",
    enable_shared_catalog: bool = False,
    source_schema: str | None = None,
) -> dict[str, Any]:
    """Store a new behavioral rule as a candidate.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new rule's ID.

    Args:
        pool: asyncpg connection pool.
        embedding_engine: EmbeddingEngine for semantic vectors.
        content: Rule description text.
        scope: Visibility scope (default 'global').
        tags: Optional list of string tags.
        request_context: Optional dict with 'tenant_id' and 'request_id' for
            multi-tenant isolation and request trace correlation.
        retention_class: Retention policy class for the rule (default 'rule').
            Controls lifecycle management behaviour.
        sensitivity: Data sensitivity classification (default 'normal').
            Use 'pii' for personally-identifiable information, etc.
        enable_shared_catalog: When True, write a catalog entry to
            ``public.memory_catalog`` after the rule is stored.
        source_schema: Butler schema name for the catalog row (e.g. 'health').
    """
    tenant_id, request_id = _extract_request_context(request_context)
    result = await _storage.store_rule(
        pool,
        content,
        embedding_engine,
        scope=scope,
        tags=tags,
        tenant_id=tenant_id,
        request_id=request_id,
        retention_class=retention_class,
        sensitivity=sensitivity,
        enable_shared_catalog=enable_shared_catalog,
        source_schema=source_schema,
    )

    # Backward-compatible: older storage variants may return a mapping.
    if isinstance(result, dict):
        return {"id": str(result["id"])}
    return {"id": str(result)}

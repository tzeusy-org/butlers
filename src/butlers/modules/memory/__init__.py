"""Memory module — wires memory domain tools into the butler's MCP server.

Registers 18 MCP tools that delegate to the existing implementations in
``butlers.modules.memory.tools``. The tool closures strip ``pool`` and
``embedding_engine`` from the MCP-visible signature and inject them from
module state at call time.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field

from butlers.core.tool_call_capture import get_current_runtime_session_routing_context
from butlers.modules.base import Module


def _coerce_json_list(v: Any) -> Any:
    """Coerce a JSON-encoded string array into a Python list.

    LLMs sometimes send list parameters as JSON strings (e.g. '["a","b"]')
    instead of actual arrays.  This validator transparently handles both forms.
    """
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return v


logger = logging.getLogger(__name__)


class MemoryModuleConfig(BaseModel):
    """Configuration for the Memory module."""

    class RetrievalConfig(BaseModel):
        """Config for memory retrieval and context assembly defaults."""

        context_token_budget: int = 3000
        default_limit: int = 20
        default_mode: str = "hybrid"
        score_weights: dict[str, float] = Field(
            default_factory=lambda: {
                "relevance": 0.4,
                "importance": 0.3,
                "recency": 0.2,
                "confidence": 0.1,
            }
        )

    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)

    # Feature flag: write summary entries to shared.memory_catalog on every
    # fact/rule store.  Defaults to False for backward compatibility.
    # Set to True only after the core_023 migration has been applied.
    enable_shared_catalog: bool = False

    # Butler schema name written as source_schema in catalog rows.
    # When empty, the butler's own schema name is used.  Most deployments
    # leave this unset and let the module infer it from context.
    catalog_source_schema: str = ""


class MemoryModule(Module):
    """Memory module providing 18 MCP tools for memory CRUD and retrieval."""

    def __init__(self) -> None:
        self._db: Any = None
        self._embedding_engine: Any = None
        self._config: MemoryModuleConfig = MemoryModuleConfig()

    @property
    def name(self) -> str:
        return "memory"

    @property
    def config_schema(self) -> type[BaseModel]:
        return MemoryModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return "memory"

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db
        if isinstance(config, MemoryModuleConfig):
            self._config = config

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None
        self._embedding_engine = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("MemoryModule not initialised — no DB available")
        return self._db.pool

    def _get_embedding_engine(self):
        """Lazy-load and return the shared embedding engine singleton."""
        if self._embedding_engine is None:
            from butlers.modules.memory.tools import get_embedding_engine

            self._embedding_engine = get_embedding_engine()
        return self._embedding_engine

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all memory MCP tools."""
        self._db = db
        if isinstance(config, MemoryModuleConfig):
            self._config = config
        module = self  # capture for closures
        default_context_budget = self._config.retrieval.context_token_budget

        # Import sub-modules (not individual functions) to avoid name collisions
        # between the MCP closure names and the imported symbols.
        # Deferred to avoid import-time side effects (sentence_transformers).
        from butlers.modules.memory import consolidation as _consolidation
        from butlers.modules.memory.tools import context as _context
        from butlers.modules.memory.tools import entities as _entities
        from butlers.modules.memory.tools import feedback as _feedback
        from butlers.modules.memory.tools import management as _management
        from butlers.modules.memory.tools import reading as _reading
        from butlers.modules.memory.tools import writing as _writing

        # --- Writing tools ---

        @mcp.tool()
        async def memory_store_episode(
            content: str,
            butler: str,
            session_id: str | None = None,
            importance: float = 5.0,
            request_context: Annotated[
                dict[str, Any] | None,
                Field(
                    description=(
                        "Optional request context dict with 'tenant_id' (default 'owner') "
                        "and 'request_id' (optional trace ID). When omitted, defaults to "
                        "tenant_id='owner' and no request_id."
                    )
                ),
            ] = None,
        ) -> dict[str, Any]:
            """Store a raw episode from a runtime session."""
            return await _writing.memory_store_episode(
                module._get_pool(),
                content,
                butler,
                session_id=session_id,
                importance=importance,
                request_context=request_context,
            )

        @mcp.tool()
        async def memory_store_fact(
            subject: Annotated[str, Field(description="Required subject entity key.")],
            predicate: Annotated[str, Field(description="Required predicate key.")],
            content: Annotated[str, Field(description="Required fact content text.")],
            importance: Annotated[
                float, Field(description="Importance score (float, default 5.0).")
            ] = 5.0,
            permanence: Annotated[
                Literal["permanent", "stable", "standard", "volatile", "ephemeral"],
                Field(
                    description=(
                        "Permanence level: permanent | stable | standard | volatile | ephemeral."
                    )
                ),
            ] = "standard",
            scope: Annotated[
                str, Field(description="Scope namespace (default `global`).")
            ] = "global",
            tags: Annotated[
                list[str] | None,
                BeforeValidator(_coerce_json_list),
                Field(
                    description=(
                        "Optional tags as a JSON array of strings (a list), "
                        'for example ["work", "project-x"]. DO NOT pass a '
                        'single string value (e.g. "work,project-x").'
                    )
                ),
            ] = None,
            entity_id: Annotated[
                str | None,
                Field(
                    description=(
                        "Optional UUID of a resolved entity to anchor this fact. "
                        "When provided, uniqueness is enforced via "
                        "(entity_id, scope, predicate) instead of (subject, predicate). "
                        "Use the entity_id from the identity preamble when available "
                        "(e.g. from [Source: Owner (contact_id: ..., entity_id: ...)])."
                    )
                ),
            ] = None,
            object_entity_id: Annotated[
                str | None,
                Field(
                    description=(
                        "Optional UUID of the target entity for edge-facts. "
                        "Creates a directed relationship from subject entity "
                        "to this object entity. Requires entity_id to also be set."
                    )
                ),
            ] = None,
            valid_at: Annotated[
                str | None,
                Field(
                    description=(
                        "Optional ISO-8601 timestamp for when the fact was true "
                        "(e.g. '2026-03-06T12:30:00Z'). Used for temporal predicates "
                        "such as meal_breakfast/lunch/dinner/snack. Temporal facts "
                        "skip supersession so multiple entries at different times can "
                        "coexist. Defaults to now() when omitted."
                    )
                ),
            ] = None,
            idempotency_key: Annotated[
                str | None,
                Field(
                    description=(
                        "Optional dedup key for temporal facts (valid_at IS NOT NULL). "
                        "When provided, duplicate writes with the same key are silently "
                        "ignored and the existing fact's ID is returned. When omitted, "
                        "a key is auto-generated from (entity_id, scope, predicate, "
                        "valid_at, source). Property facts (valid_at omitted) are unaffected."
                    )
                ),
            ] = None,
            request_context: Annotated[
                dict[str, Any] | None,
                Field(
                    description=(
                        "Optional request context dict with 'tenant_id' (default 'owner') "
                        "and 'request_id' (optional trace ID). When omitted, defaults to "
                        "tenant_id='owner' and no request_id."
                    )
                ),
            ] = None,
        ) -> dict[str, Any]:
            """Store a fact and supersede any active match.

            Property facts use `(subject, predicate)` for uniqueness.
            When `entity_id` is provided, uniqueness uses
            `(entity_id, scope, predicate)` instead — the `subject` field
            becomes a human-readable label only.
            Edge-facts (when `object_entity_id` is set) use
            `(entity_id, object_entity_id, scope, predicate)`.

            Temporal predicates (e.g. meal_breakfast, meal_lunch) skip
            supersession — multiple active facts at different `valid_at`
            timestamps can coexist for the same entity/predicate.

            Required fields:
            - `subject` (string)
            - `predicate` (string)
            - `content` (string)

            Optional fields:
            - `importance` (float)
            - `permanence` (enum): `permanent|stable|standard|volatile|ephemeral`
            - `scope` (string)
            - `tags` (array[string]) — must be a JSON array of strings (a list)
              and NOT a JSON-encoded string.
              A single string is invalid and will fail validation.
            - `entity_id` (string, UUID) — anchor fact to a resolved entity
            - `object_entity_id` (string, UUID) — target entity for edge-facts
            - `valid_at` (string, ISO-8601) — when the fact was true (temporal facts)

            Valid JSON example (entity-anchored property fact):
            {
              "subject": "Owner",
              "predicate": "favorite_coffee",
              "content": "drinks espresso",
              "entity_id": "550e8400-e29b-41d4-a716-446655440000",
              "permanence": "stable",
              "tags": ["preferences", "coffee"]
            }

            Valid JSON example (edge fact):
            {
              "subject": "Alice",
              "predicate": "works_at",
              "content": "software engineer",
              "entity_id": "550e8400-e29b-41d4-a716-446655440000",
              "object_entity_id": "660e8400-e29b-41d4-a716-446655440001"
            }

            Valid JSON example (temporal fact):
            {
              "subject": "Owner",
              "predicate": "meal_breakfast",
              "content": "oatmeal with berries",
              "entity_id": "550e8400-e29b-41d4-a716-446655440000",
              "permanence": "stable",
              "valid_at": "2026-03-06T08:00:00Z"
            }
            """
            # If the caller did not supply entity_id, fall back to the sender
            # entity resolved during identity resolution and stored in the
            # runtime session routing context.  This prevents the LLM from
            # having to extract source_sender_entity_id from the preamble text.
            effective_entity_id = entity_id
            if effective_entity_id is None:
                _routing_ctx = get_current_runtime_session_routing_context()
                if isinstance(_routing_ctx, dict):
                    _ctx_entity_id = _routing_ctx.get("source_entity_id")
                    if isinstance(_ctx_entity_id, str) and _ctx_entity_id.strip():
                        effective_entity_id = _ctx_entity_id.strip()
            return await _writing.memory_store_fact(
                module._get_pool(),
                module._get_embedding_engine(),
                subject,
                predicate,
                content,
                importance=importance,
                permanence=permanence,
                scope=scope,
                tags=tags,
                entity_id=effective_entity_id,
                object_entity_id=object_entity_id,
                valid_at=valid_at,
                idempotency_key=idempotency_key,
                request_context=request_context,
                enable_shared_catalog=module._config.enable_shared_catalog,
                source_schema=module._config.catalog_source_schema or None,
            )

        @mcp.tool()
        async def memory_store_rule(
            content: str,
            scope: str = "global",
            tags: Annotated[list[str] | None, BeforeValidator(_coerce_json_list)] = None,
            request_context: Annotated[
                dict[str, Any] | None,
                Field(
                    description=(
                        "Optional request context dict with 'tenant_id' (default 'owner') "
                        "and 'request_id' (optional trace ID). When omitted, defaults to "
                        "tenant_id='owner' and no request_id."
                    )
                ),
            ] = None,
        ) -> dict[str, Any]:
            """Store a new behavioral rule as a candidate."""
            return await _writing.memory_store_rule(
                module._get_pool(),
                module._get_embedding_engine(),
                content,
                scope=scope,
                tags=tags,
                request_context=request_context,
                enable_shared_catalog=module._config.enable_shared_catalog,
                source_schema=module._config.catalog_source_schema or None,
            )

        # --- Reading tools ---

        @mcp.tool()
        async def memory_search(
            query: Annotated[str, Field(description="Search query text.")],
            types: Annotated[
                list[Literal["episode", "fact", "rule"]] | None,
                BeforeValidator(_coerce_json_list),
                Field(
                    description=(
                        "Optional memory types as a JSON array/list. Allowed values: "
                        "episode | fact | rule (singular). Do not pass a single "
                        'string or plural values like "facts".'
                    )
                ),
            ] = None,
            scope: Annotated[
                str | None,
                Field(description="Optional scope namespace filter (for example `global`)."),
            ] = None,
            mode: Annotated[
                Literal["hybrid", "semantic", "keyword"],
                Field(description="Search mode. Allowed values: hybrid | semantic | keyword."),
            ] = "hybrid",
            limit: int = 10,
            min_confidence: float = 0.2,
            filters: Annotated[
                dict[str, Any] | None,
                Field(
                    description=(
                        "Optional structured filters applied as AND conditions. "
                        "Supported keys: scope, entity_id, predicate, source_butler, "
                        "time_from (ISO-8601), time_to (ISO-8601), retention_class, sensitivity. "
                        "Unrecognized keys are silently ignored."
                    )
                ),
            ] = None,
        ) -> list[dict[str, Any]]:
            """Search memory with strict type/mode inputs and deterministic ranking.

            Required fields:
            - `query` (string)

            Optional fields:
            - `types` (array[string]): use singular values from `episode|fact|rule`
            - `scope` (string)
            - `mode` (string enum): `hybrid|semantic|keyword`
            - `limit` (int)
            - `min_confidence` (float)
            - `filters` (dict): AND-conditions; keys: scope, entity_id, predicate,
              source_butler, time_from, time_to, retention_class, sensitivity

            Input shape reminders:
            - `types` must be a JSON array/list, for example `["fact"]`
            - `types="facts"` is invalid (string + plural)

            Valid JSON example:
            {
              "query": "coffee preferences",
              "types": ["fact"],
              "mode": "hybrid",
              "limit": 10
            }
            """
            return await _reading.memory_search(
                module._get_pool(),
                module._get_embedding_engine(),
                query,
                types=types,
                scope=scope,
                mode=mode,
                limit=limit,
                min_confidence=min_confidence,
                filters=filters,
            )

        @mcp.tool()
        async def memory_recall(
            topic: str,
            scope: str | None = None,
            limit: int = 10,
            filters: Annotated[
                dict[str, Any] | None,
                Field(
                    description=(
                        "Optional structured filters applied as AND conditions. "
                        "Supported keys: scope, entity_id, predicate, source_butler, "
                        "time_from (ISO-8601), time_to (ISO-8601), retention_class, sensitivity. "
                        "Unrecognized keys are silently ignored."
                    )
                ),
            ] = None,
            request_context: Annotated[
                dict[str, Any] | None,
                Field(
                    description=(
                        "Optional request context dict with 'tenant_id' (default 'owner') "
                        "and 'request_id' (optional trace ID). When omitted, defaults to "
                        "tenant_id='owner' and no request_id."
                    )
                ),
            ] = None,
        ) -> list[dict[str, Any]]:
            """High-level composite-scored retrieval of relevant facts and rules."""
            return await _reading.memory_recall(
                module._get_pool(),
                module._get_embedding_engine(),
                topic,
                scope=scope,
                limit=limit,
                filters=filters,
                request_context=request_context,
            )

        @mcp.tool()
        async def memory_get(
            memory_type: str,
            memory_id: str,
        ) -> dict[str, Any] | None:
            """Retrieve a specific memory by type and ID."""
            return await _reading.memory_get(
                module._get_pool(),
                memory_type,
                memory_id,
            )

        # --- Feedback tools ---

        @mcp.tool()
        async def memory_confirm(
            memory_type: str,
            memory_id: str,
        ) -> dict[str, Any]:
            """Confirm a fact or rule is still accurate, resetting confidence decay."""
            return await _feedback.memory_confirm(
                module._get_pool(),
                memory_type,
                memory_id,
            )

        @mcp.tool()
        async def memory_mark_helpful(
            rule_id: str,
        ) -> dict[str, Any]:
            """Report a rule was applied successfully."""
            return await _feedback.memory_mark_helpful(
                module._get_pool(),
                rule_id,
            )

        @mcp.tool()
        async def memory_mark_harmful(
            rule_id: str,
            reason: str | None = None,
        ) -> dict[str, Any]:
            """Report a rule caused problems."""
            return await _feedback.memory_mark_harmful(
                module._get_pool(),
                rule_id,
                reason=reason,
            )

        # --- Management tools ---

        @mcp.tool()
        async def memory_forget(
            memory_type: str,
            memory_id: str,
        ) -> dict[str, Any]:
            """Soft-delete a memory by type and ID."""
            return await _management.memory_forget(
                module._get_pool(),
                memory_type,
                memory_id,
            )

        @mcp.tool()
        async def memory_stats(
            scope: str | None = None,
        ) -> dict[str, Any]:
            """System health indicators across all memory types."""
            return await _management.memory_stats(
                module._get_pool(),
                scope=scope,
            )

        # --- Predicate registry tool ---

        @mcp.tool()
        async def memory_predicate_list(
            edges_only: Annotated[
                bool,
                Field(description="If true, return only edge predicates."),
            ] = False,
        ) -> list[dict[str, Any]]:
            """List all registered predicates from the predicate registry.

            Returns known predicates with their expected subject/object types,
            edge flag, and description. Use this to discover consistent predicate
            names before storing facts.
            """
            return await _management.predicate_list(
                module._get_pool(),
                edges_only=edges_only,
            )

        # --- Context tool ---

        @mcp.tool()
        async def memory_context(
            trigger_prompt: str,
            butler: str,
            token_budget: int = default_context_budget,
            include_recent_episodes: Annotated[
                bool,
                Field(
                    description=(
                        "If True, include a ## Recent Episodes section (15% of budget) "
                        "with the most recent episodes for the butler. Default False."
                    )
                ),
            ] = False,
            request_context: Annotated[
                dict[str, Any] | None,
                Field(
                    description=(
                        "Optional request context dict with 'tenant_id' (default 'owner') "
                        "and 'request_id' (optional trace ID). tenant_id scopes all retrieval. "
                        "When omitted, defaults to tenant_id='owner' and no request_id."
                    )
                ),
            ] = None,
        ) -> str:
            """Build a deterministic, sectioned memory context block for CC system prompt injection.

            Sections (in order, empty sections omitted):
            - ## Profile Facts (30% of budget): owner entity facts sorted by importance
            - ## Task-Relevant Facts (35% of budget): recall matches excluding profile facts
            - ## Active Rules (20% of budget): sorted by maturity rank then effectiveness
            - ## Recent Episodes (15% of budget): opt-in via include_recent_episodes=True

            Same inputs always produce identical output (deterministic section compiler).
            """
            return await _context.memory_context(
                module._get_pool(),
                module._get_embedding_engine(),
                trigger_prompt,
                butler,
                token_budget=token_budget,
                include_recent_episodes=include_recent_episodes,
                request_context=request_context,
            )

        # --- Entity tools ---

        @mcp.tool()
        async def memory_entity_create(
            canonical_name: Annotated[str, Field(description="The canonical name of the entity.")],
            entity_type: Annotated[
                Literal["person", "organization", "place", "other"],
                Field(description="Entity type: person | organization | place | other."),
            ],
            tenant_id: Annotated[
                str, Field(description="Tenant scope for isolation. Defaults to 'shared'.")
            ] = "shared",
            aliases: Annotated[
                list[str] | None,
                BeforeValidator(_coerce_json_list),
                Field(description="Optional list of alternative names for the entity."),
            ] = None,
            metadata: Annotated[
                dict[str, Any] | None,
                Field(description="Optional JSONB metadata dict."),
            ] = None,
        ) -> dict[str, Any]:
            """Create a new named entity in the memory entity graph.

            Inserts a new entity record. Fails if (tenant_id, canonical_name, entity_type)
            already exists.

            Returns:
                Dict with key entity_id (UUID string).
            """
            return await _entities.entity_create(
                module._get_pool(),
                canonical_name,
                entity_type,
                tenant_id=tenant_id,
                aliases=aliases,
                metadata=metadata,
            )

        @mcp.tool()
        async def memory_entity_get(
            entity_id: Annotated[str, Field(description="UUID string of the entity.")],
            tenant_id: Annotated[
                str, Field(description="Tenant scope for isolation. Defaults to 'shared'.")
            ] = "shared",
        ) -> dict[str, Any] | None:
            """Retrieve a named entity from the memory entity graph by ID.

            Returns the full entity record including aliases and metadata,
            or None if the entity does not exist within the given tenant.
            """
            return await _entities.entity_get(
                module._get_pool(),
                entity_id,
                tenant_id=tenant_id,
            )

        @mcp.tool()
        async def memory_entity_update(
            entity_id: Annotated[str, Field(description="UUID string of the entity to update.")],
            tenant_id: Annotated[
                str, Field(description="Tenant scope for isolation. Defaults to 'shared'.")
            ] = "shared",
            canonical_name: Annotated[
                str | None,
                Field(description="New canonical name (optional)."),
            ] = None,
            aliases: Annotated[
                list[str] | None,
                BeforeValidator(_coerce_json_list),
                Field(
                    description=("Full replacement aliases list (replace-all semantics). Optional.")
                ),
            ] = None,
            metadata: Annotated[
                dict[str, Any] | None,
                Field(description="Metadata keys to merge into existing metadata. Optional."),
            ] = None,
        ) -> dict[str, Any] | None:
            """Update a named entity in the memory entity graph.

            - canonical_name: replaces current value when provided.
            - aliases: replace-all semantics — pass the full desired list.
            - metadata: merge semantics — keys are merged into existing metadata.

            Returns the updated entity record or None if not found.
            """
            return await _entities.entity_update(
                module._get_pool(),
                entity_id,
                tenant_id=tenant_id,
                canonical_name=canonical_name,
                aliases=aliases,
                metadata=metadata,
            )

        # --- Consolidation and cleanup tools ---

        @mcp.tool()
        async def memory_run_consolidation() -> dict[str, Any]:
            """Run memory consolidation to process unconsolidated episodes.

            Fetches episodes with consolidation_status='pending', groups them by
            source butler, and returns stats about episodes ready for consolidation.

            Returns:
                Dict with keys:
                - episodes_processed: total unconsolidated episodes found
                - butlers_processed: number of distinct butler groups
                - groups: mapping of butler name to episode count
            """
            return await _consolidation.run_consolidation(
                module._get_pool(),
                module._get_embedding_engine(),
            )

        @mcp.tool()
        async def memory_run_episode_cleanup(
            max_entries: int = 10000,
        ) -> dict[str, Any]:
            """Run episode cleanup to delete expired episodes and enforce capacity limits.

            Cleanup proceeds in three steps:
            1. Delete episodes whose expires_at is in the past
            2. Count how many episodes remain
            3. If remaining count exceeds max_entries, delete oldest consolidated
               episodes until within budget (unconsolidated episodes are preserved)

            Args:
                max_entries: Maximum number of episodes to retain (default 10000)

            Returns:
                Dict with keys:
                - expired_deleted: episodes removed because they expired
                - capacity_deleted: consolidated episodes removed for capacity
                - remaining: total episodes still in the table
            """
            return await _consolidation.run_episode_cleanup(
                module._get_pool(),
                max_entries=max_entries,
            )

        @mcp.tool()
        async def memory_entity_neighbors(
            entity_id: Annotated[str, Field(description="UUID string of the starting entity.")],
            tenant_id: Annotated[
                str, Field(description="Tenant scope for isolation. Defaults to 'shared'.")
            ] = "shared",
            max_depth: Annotated[
                int,
                Field(description="Maximum traversal depth (1–5, default 2)."),
            ] = 2,
            predicate_filter: Annotated[
                list[str] | None,
                BeforeValidator(_coerce_json_list),
                Field(description="Optional list of predicates to restrict traversal."),
            ] = None,
            direction: Annotated[
                Literal["outgoing", "incoming", "both"],
                Field(description="Edge direction: outgoing, incoming, or both (default)."),
            ] = "both",
        ) -> list[dict[str, Any]]:
            """Traverse the entity graph and return neighboring entities.

            Follows edge-facts (facts where object_entity_id is set) using a
            recursive CTE.  Returns neighbors with their edge predicate, direction,
            content, fact_id, hop depth, and traversal path.

            Direction controls which edges to follow:
            - outgoing: entity_id → object_entity_id
            - incoming: object_entity_id → entity_id
            - both: traverse in both directions (default)
            """
            return await _entities.entity_neighbors(
                module._get_pool(),
                entity_id,
                tenant_id=tenant_id,
                max_depth=max_depth,
                predicate_filter=predicate_filter,
                direction=direction,
            )

        @mcp.tool()
        async def memory_entity_resolve(
            name: str,
            tenant_id: str = "shared",
            entity_type: str | None = None,
            context_hints: dict[str, Any] | None = None,
            enable_fuzzy: bool = False,
        ) -> list[dict[str, Any]]:
            """Resolve an ambiguous name string to ranked memory entity candidates.

            Returns a list of candidate entities ordered by composite score
            (name-match quality + graph neighborhood similarity). Returns an
            empty list when no candidates are found — does not auto-create.

            context_hints keys: topic (str), mentioned_with (list),
            domain_scores (dict of entity_id -> numeric score).
            """
            return await _entities.entity_resolve(
                module._get_pool(),
                name,
                tenant_id=tenant_id,
                entity_type=entity_type,
                context_hints=context_hints,
                enable_fuzzy=enable_fuzzy,
            )

        @mcp.tool()
        async def memory_entity_merge(
            source_entity_id: Annotated[
                str,
                Field(description="UUID string of the entity to be merged (will be tombstoned)."),
            ],
            target_entity_id: Annotated[
                str, Field(description="UUID string of the surviving entity.")
            ],
            tenant_id: Annotated[
                str, Field(description="Tenant scope for isolation. Defaults to 'shared'.")
            ] = "shared",
        ) -> dict[str, Any] | None:
            """Merge source entity into target entity in the memory entity graph.

            All facts referencing the source entity are re-pointed to the target.
            Uniqueness conflicts are resolved via supersession (higher-confidence fact wins).
            Source aliases are appended to target's alias list (deduplicated). Source metadata
            is merged into target's (target wins on conflict). Source entity is
            tombstoned (excluded from future entity_resolve results). An audit event
            is emitted to memory_events.

            Returns the updated target entity dict, or None if target not found.
            Raises ValueError if source entity not found or IDs are identical.
            """
            return await _entities.entity_merge(
                module._get_pool(),
                source_entity_id,
                target_entity_id,
                tenant_id=tenant_id,
            )

        # --- Cross-butler catalog search tool ---

        @mcp.tool()
        async def memory_catalog_search(
            query: Annotated[str, Field(description="Search query text.")],
            memory_type: Annotated[
                str | None,
                Field(
                    description=(
                        "Optional memory type filter: 'fact' or 'rule'. "
                        "When omitted, both types are searched."
                    )
                ),
            ] = None,
            limit: Annotated[int, Field(description="Max results to return (default 10).")] = 10,
            mode: Annotated[
                str,
                Field(description="Search mode: 'hybrid' (default), 'semantic', or 'keyword'."),
            ] = "hybrid",
        ) -> list[dict[str, Any]]:
            """Search the shared memory catalog for cross-butler memory discovery.

            Queries ``shared.memory_catalog`` — a discovery index aggregating
            summary entries from all butler schemas.  Returns provenance pointers
            (source_schema, source_table, source_id) so the full canonical memory
            can be retrieved from the owning butler.

            This tool is only useful when the ``enable_shared_catalog`` feature
            flag is True and the core_023 migration has been applied.

            Required fields:
            - ``query`` (string)

            Optional fields:
            - ``memory_type``: 'fact' | 'rule' (omit to search both)
            - ``limit`` (int)
            - ``mode``: 'hybrid' | 'semantic' | 'keyword'
            """
            return await _reading.memory_catalog_search(
                module._get_pool(),
                module._get_embedding_engine(),
                query,
                memory_type=memory_type,
                limit=limit,
                mode=mode,
            )

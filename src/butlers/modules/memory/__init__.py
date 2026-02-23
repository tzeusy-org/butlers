"""Memory module — wires memory domain tools into the butler's MCP server.

Registers 17 MCP tools that delegate to the existing implementations in
``butlers.modules.memory.tools``. The tool closures strip ``pool`` and
``embedding_engine`` from the MCP-visible signature and inject them from
module state at call time.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from butlers.modules.base import Module

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


class MemoryModule(Module):
    """Memory module providing 17 MCP tools for memory CRUD and retrieval."""

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
        ) -> dict[str, Any]:
            """Store a raw episode from a runtime session."""
            return await _writing.memory_store_episode(
                module._get_pool(),
                content,
                butler,
                session_id=session_id,
                importance=importance,
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
                Field(
                    description=(
                        "Optional tags as a JSON array of strings (not a comma-separated string)."
                    )
                ),
            ] = None,
        ) -> dict[str, Any]:
            """Store a fact and supersede any active `(subject, predicate)` match.

            Required fields:
            - `subject` (string)
            - `predicate` (string)
            - `content` (string)

            Optional fields:
            - `importance` (float)
            - `permanence` (enum): `permanent|stable|standard|volatile|ephemeral`
            - `scope` (string)
            - `tags` (array[string]) — must be a JSON array of strings

            Valid JSON example:
            {
              "subject": "user",
              "predicate": "favorite_coffee",
              "content": "drinks espresso",
              "importance": 6.5,
              "permanence": "standard",
              "scope": "global",
              "tags": ["preferences", "coffee"]
            }
            """
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
            )

        @mcp.tool()
        async def memory_store_rule(
            content: str,
            scope: str = "global",
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Store a new behavioral rule as a candidate."""
            return await _writing.memory_store_rule(
                module._get_pool(),
                module._get_embedding_engine(),
                content,
                scope=scope,
                tags=tags,
            )

        # --- Reading tools ---

        @mcp.tool()
        async def memory_search(
            query: str,
            types: list[str] | None = None,
            scope: str | None = None,
            mode: str = "hybrid",
            limit: int = 10,
            min_confidence: float = 0.2,
        ) -> list[dict[str, Any]]:
            """Search across memory types using hybrid, semantic, or keyword mode."""
            return await _reading.memory_search(
                module._get_pool(),
                module._get_embedding_engine(),
                query,
                types=types,
                scope=scope,
                mode=mode,
                limit=limit,
                min_confidence=min_confidence,
            )

        @mcp.tool()
        async def memory_recall(
            topic: str,
            scope: str | None = None,
            limit: int = 10,
        ) -> list[dict[str, Any]]:
            """High-level composite-scored retrieval of relevant facts and rules."""
            return await _reading.memory_recall(
                module._get_pool(),
                module._get_embedding_engine(),
                topic,
                scope=scope,
                limit=limit,
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

        # --- Context tool ---

        @mcp.tool()
        async def memory_context(
            trigger_prompt: str,
            butler: str,
            token_budget: int = default_context_budget,
        ) -> str:
            """Build a memory context block for injection into a CC system prompt."""
            return await _context.memory_context(
                module._get_pool(),
                module._get_embedding_engine(),
                trigger_prompt,
                butler,
                token_budget=token_budget,
            )

        # --- Entity tools ---

        @mcp.tool()
        async def entity_create(
            canonical_name: Annotated[str, Field(description="The canonical name of the entity.")],
            entity_type: Annotated[
                Literal["person", "organization", "place", "other"],
                Field(description="Entity type: person | organization | place | other."),
            ],
            tenant_id: Annotated[str, Field(description="Tenant scope for isolation.")],
            aliases: Annotated[
                list[str] | None,
                Field(description="Optional list of alternative names for the entity."),
            ] = None,
            metadata: Annotated[
                dict[str, Any] | None,
                Field(description="Optional JSONB metadata dict."),
            ] = None,
        ) -> dict[str, Any]:
            """Create a new named entity.

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
        async def entity_get(
            entity_id: Annotated[str, Field(description="UUID string of the entity.")],
            tenant_id: Annotated[str, Field(description="Tenant scope for isolation.")],
        ) -> dict[str, Any] | None:
            """Retrieve a named entity by ID.

            Returns the full entity record including aliases and metadata,
            or None if the entity does not exist within the given tenant.
            """
            return await _entities.entity_get(
                module._get_pool(),
                entity_id,
                tenant_id=tenant_id,
            )

        @mcp.tool()
        async def entity_update(
            entity_id: Annotated[str, Field(description="UUID string of the entity to update.")],
            tenant_id: Annotated[str, Field(description="Tenant scope for isolation.")],
            canonical_name: Annotated[
                str | None,
                Field(description="New canonical name (optional)."),
            ] = None,
            aliases: Annotated[
                list[str] | None,
                Field(
                    description=("Full replacement aliases list (replace-all semantics). Optional.")
                ),
            ] = None,
            metadata: Annotated[
                dict[str, Any] | None,
                Field(description="Metadata keys to merge into existing metadata. Optional."),
            ] = None,
        ) -> dict[str, Any] | None:
            """Update a named entity.

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
        async def entity_resolve(
            name: str,
            tenant_id: str = "default",
            entity_type: str | None = None,
            context_hints: dict[str, Any] | None = None,
            enable_fuzzy: bool = False,
        ) -> list[dict[str, Any]]:
            """Resolve an ambiguous name string to ranked entity candidates.

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
        async def entity_merge(
            source_entity_id: Annotated[
                str,
                Field(description="UUID string of the entity to be merged (will be tombstoned)."),
            ],
            target_entity_id: Annotated[
                str, Field(description="UUID string of the surviving entity.")
            ],
            tenant_id: Annotated[str, Field(description="Tenant scope for isolation.")],
        ) -> dict[str, Any] | None:
            """Merge source entity into target entity.

            All facts referencing the source entity are re-pointed to the target.
            Source aliases are appended to target's alias list. Source entity is
            tombstoned (soft-deleted; excluded from future resolution).

            Returns the updated target entity dict, or None if target not found.
            Raises ValueError if source entity not found or IDs are identical.
            """
            return await _entities.entity_merge(
                module._get_pool(),
                source_entity_id,
                target_entity_id,
                tenant_id=tenant_id,
            )

## Why

Butlers currently have no durable memory that is consistently available at runtime. Each runtime session starts from scratch, so user preferences, long-term facts, and learned behavior patterns are lost between sessions.

The previous service-centric design for memory conflicts with the platform's isolation model (`one DB per butler`) and creates unnecessary infrastructure coupling. Memory should be a common module that runs inside each relevant butler.

## What Changes

- **Memory becomes a module** (`[modules.memory]`) enabled per butler, not a standalone role/service.
- **Per-butler storage** — memory tables (`episodes`, `facts`, `rules`, `memory_links`, `memory_events`) live in each hosting butler database.
- **Per-butler tool surface** — memory tools are registered on the hosting butler MCP server when the module is enabled.
- **Three memory types with separate lifecycles:**
  - *Episodes* — short-lived session observations (TTL)
  - *Facts* — subject/predicate knowledge with decay + supersession
  - *Rules* — procedural patterns with maturity progression + anti-pattern inversion
- **Local embeddings + hybrid retrieval** — MiniLM-L6 embeddings with vector + full-text retrieval fused via RRF.
- **Confidence decay** — permanence-based decay rates with effective-confidence thresholds.
- **Consolidation engine** — scheduled conversion of episodes into facts/rules with provenance links.
- **Runtime context injection** — spawner calls `memory_context()` before runtime spawn and injects memory block into system prompt.
- **Session-to-episode pipeline** — daemon stores an episode after each completed session.
- **Dashboard integration** — butler-scoped memory pages and tenant-wide `/memory` aggregation by API fanout.

## Capabilities

### New Capabilities

- `memory-storage`: Memory schema and persistence semantics for per-butler DBs.
- `memory-search`: Semantic/keyword/hybrid retrieval and composite scoring.
- `memory-mcp-tools`: Tool contracts for memory read/write/feedback/context operations on hosting butlers.
- `memory-consolidation`: Consolidation, decay, and cleanup lifecycle jobs.
- `memory-rule-maturity`: Rule effectiveness, promotion/demotion, anti-pattern inversion.
- `memory-module-integration`: Spawner/session integration and module config/registration behavior.
- `memory-dashboard`: API + frontend behavior for memory browsing and editing.

### Modified Capabilities

- Legacy integration capability naming is replaced by `memory-module-integration`.

## Impact

- **No dedicated memory service:** remove dependency on a separate singleton memory process.
- **No shared memory DB:** each enabled butler stores memory in its own DB.
- **Config surface changes:** memory config moves to `[modules.memory]`.
- **Spawner integration remains:** `memory_context()` pre-spawn and `memory_store_episode()` post-session remain required, but calls are local to the hosting butler server.
- **Migrations:** memory migration chain applies to each enabled butler DB.
- **Dashboard:** global memory views aggregate across butlers via APIs/tool calls, not a shared DB.

## Context

Butlers are currently mostly stateless between sessions. We need durable memory for runtime recall, but we should preserve platform invariants:
- each butler owns its own database,
- modules are the reusable extension mechanism,
- runtime failures in optional capabilities are fail-open for user workflows.

Memory therefore becomes a shared module implementation, instantiated per butler, not a dedicated infrastructure butler.

## Goals / Non-Goals

**Goals:**
- Provide memory as a reusable module (`[modules.memory]`) for relevant butlers.
- Keep memory data isolated per butler database.
- Preserve three distinct memory types (episodes, facts, rules) and their lifecycles.
- Support local-first hybrid retrieval and deterministic context assembly.
- Run consolidation/decay/cleanup as scheduled module jobs.
- Expose memory tools on each hosting butler MCP server.
- Keep runtime integration fail-open.

**Non-Goals:**
- Dedicated shared memory service or shared memory database.
- Direct cross-butler SQL memory access.
- Real-time mandatory consolidation on every session.
- Replacing role-specific operational state stores.

## Decisions

### D1: Memory is a module, not a standalone butler role

**Decision:** Implement memory as a common module enabled in each target butler.

**Rationale:** This matches existing plugin architecture and preserves per-butler data isolation. It avoids making memory a special infrastructure singleton.

**Alternative considered:** Dedicated singleton memory service. Rejected due to cross-butler coupling and a shared DB exception.

### D2: Memory data is stored in each hosting butler DB

**Decision:** Memory tables are created in the same database as the hosting butler.

**Rationale:** Aligns with base isolation guarantees and existing migration/runtime model.

**Alternative considered:** One shared memory database. Rejected.

### D3: Memory tools are module-registered on hosting MCP servers

**Decision:** `memory_*` tools are registered by the memory module on each enabled butler MCP server.

**Rationale:** CC/runtime calls can use local tool registration without cross-service dependency.

### D4: Embeddings use local `sentence-transformers/all-MiniLM-L6-v2`

**Decision:** Load MiniLM-L6 in-process per hosting butler with memory enabled.

**Rationale:** Low-latency, no external dependency, acceptable resource footprint.

### D5: Retrieval is hybrid (semantic + keyword via RRF)

**Decision:** Support `semantic`, `keyword`, and default `hybrid` mode.

**Rationale:** Improves recall for both conceptual and exact-match queries.

### D6: Confidence decay uses permanence classes

**Decision:** `effective_confidence = confidence * exp(-decay_rate * days_since_last_confirmed)` with permanence-driven decay rates.

**Rationale:** Balances durable identity facts with naturally fading ephemeral observations.

### D7: Consolidation runs as scheduled module jobs

**Decision:** Run consolidation (6h), decay sweep (daily), and episode cleanup (daily) as schedule-driven module workflows inside hosting butlers.

**Rationale:** Keeps lifecycle maintenance local and deterministic.

### D8: Spawner integration is local and fail-open

**Decision:** Before spawn, call local `memory_context(...)`; after successful session, call local `memory_store_episode(...)`.

**Rationale:** Preserves memory-aware behavior without introducing service startup dependencies. Failures log and continue.

### D9: Dashboard aggregation is API/tool fanout, not shared DB reads

**Decision:** Butler-scoped pages read each butler's memory surfaces directly; tenant-wide `/memory` aggregates via API fanout.

**Rationale:** Maintains DB isolation while still offering cross-butler observability.

## Risks / Trade-offs

**[Risk] Duplicate model memory usage across multiple enabled butlers**
- Mitigation: keep embedding model small (MiniLM-L6), allow selective module enablement.

**[Risk] Inconsistent memory behavior if module configs diverge by butler**
- Mitigation: provide shared defaults in config schema and validate required thresholds/weights.

**[Risk] Consolidation quality variance by butler domain**
- Mitigation: deterministic parsing/validation, strict lifecycle states, and dashboard correction workflows.

**[Trade-off] Isolation over centralized memory pool**
- We lose a single shared store but gain architectural consistency and simpler operations.

## Migration Plan

1. Add memory module implementation and config schema (`[modules.memory]`).
2. Add/adjust memory migration chain to run against each enabled butler DB.
3. Register memory tools through module registration path.
4. Wire spawner/session hooks to local memory tools with fail-open handling.
5. Enable module in selected butler rosters and run targeted tests.
6. Update dashboard endpoints/pages for butler-scoped and aggregated views.
7. Remove/deprecate role-level dedicated-memory-service assumptions in specs/docs.

## Open Questions

1. Should memory module be enabled by default for all butlers or opt-in by roster?
2. Should tenant-wide `/memory` aggregation be synchronous fanout or async materialized summary?
3. Do we need per-butler overrides for consolidation cadence beyond module defaults?

## Context

Butlers is a multi-agent system where each butler operates as an independent MCP server with its own schema, scheduler, and LLM spawner. Butlers share identity data via the `shared` PostgreSQL schema (RFC 0004, 0006) but have no mechanism for sharing the user's current situational state. Each butler makes timing and relevance decisions in isolation, leading to poorly contextualized actions (reminders during meetings, prompts during sleep, etc.).

The `shared` schema already hosts cross-butler tables (`entities`, `contacts`, `contact_info`, `entity_info`, `model_catalog`, `token_limits`, `token_usage_ledger`, `google_accounts`). Adding a `user_context` table follows the established pattern: all butlers read via `search_path`, application-level logic controls writes.

## Goals / Non-Goals

**Goals:**

- Give any butler the ability to check the user's current situation with a single SQL query before acting.
- Provide a fixed vocabulary of context signals so butlers share a common language about user state.
- Enforce per-signal write permissions so only domain-appropriate butlers can assert specific contexts.
- Auto-expire stale signals via TTLs so context never goes permanently stale.
- Keep the implementation minimal: one table, one Python module, no new infrastructure.

**Non-Goals:**

- Real-time push notifications when context changes. Pull-based polling at decision points is sufficient.
- Complex event processing or derived context (e.g., inferring "busy" from multiple signals). Each signal is independent.
- User-facing context UI in the dashboard. Dashboard integration is a separate future change.
- Automated context inference from external data sources (calendar API, location services). Butlers set context based on information they already have.
- Multi-user context. This system assumes a single user per deployment (user-federated model).

## Decisions

### 1. Single table in shared schema (not KV state store)

Context signals have structured semantics (TTL, confidence, permissions, audit trail) that map poorly to the generic KV `state` table. A dedicated table makes these constraints explicit, enables partial indexes for active-signal queries, and provides a natural audit trail of historical context.

**Alternative considered:** Using the per-butler `state` table with a `context:` key prefix. Rejected because reading another butler's state table violates schema isolation (RFC 0006), and duplicating context across all butler state tables creates synchronization problems.

### 2. Pull-based context checks (not pub/sub)

Butlers already query the database at decision points (before sending notifications, before running scheduled tasks). Adding one more query is negligible overhead. Pub/sub would require either a message broker (new infrastructure) or an in-process event loop (coupling between butlers), both violating the MCP-only inter-butler communication model.

**Alternative considered:** PostgreSQL LISTEN/NOTIFY for real-time context change events. Rejected because it adds connection management complexity, requires all butlers to maintain persistent listener connections, and provides minimal benefit over polling since context changes are infrequent (minutes-to-hours granularity, not seconds).

### 3. Application-level write permissions (not database roles)

Database-level per-column or per-row write permissions would require butler-specific PostgreSQL roles with row-level security policies. This contradicts the current model where all butlers connect with the same database role and isolation is enforced at the application level (RFC 0006). Keeping permission checks in Python is consistent and simpler to evolve.

**Alternative considered:** PostgreSQL row-level security (RLS) policies. Rejected because the current shared schema has no RLS infrastructure and adding it for one table would create an inconsistent access model.

### 4. UNIQUE (signal_type, set_by_butler) with upsert

Each butler maintains at most one active signal per type. This prevents unbounded row growth and makes updates idempotent via `INSERT ... ON CONFLICT DO UPDATE`. Multiple butlers can assert the same signal type (e.g., both health and general can set `sleeping`), and the `confidence` field provides a natural tiebreaker for consumers.

**Alternative considered:** UNIQUE on `signal_type` alone (only one source per signal). Rejected because it prevents legitimate multi-source assertions and creates contention when one butler clears a signal that another butler independently confirmed.

### 5. Confidence field for signal quality

Explicit user statements ("I'm traveling") get `confidence = 1.0`. Butler inferences (calendar shows meeting block) get lower confidence (e.g., 0.8). Consumers can filter by `min_confidence` to control sensitivity. This avoids a binary "source priority" system that would require ranking butlers.

### 6. Context preamble is optional and spawner-driven

The spawner already prepends an identity preamble (RFC 0004). Adding a context preamble follows the same pattern. It is opt-in: butlers that do not call `get_active_context()` in their spawner flow see no change. The preamble is formatted as a bracketed text prefix, not a structured tool response, so it works with all runtime adapters.

### 7. Soft delete via superseded_at (not hard delete)

Cleared signals are marked with `superseded_at` rather than deleted. This preserves the audit trail for pattern analysis (e.g., "how often is the user sick?") and enables future context analytics without requiring a separate history table.

## Risks / Trade-offs

**[Risk: Stale context from crashed butler]** A butler crashes after setting a signal but before clearing it. The signal persists until its TTL expires.
- Mitigation: Max TTLs are bounded (longest is 30 days for `traveling`/`away`). For short-lived signals like `meeting` (max 4 hours), staleness is self-correcting. Critical contexts like `dnd` have short max TTLs (24 hours).

**[Risk: Permission model is advisory]** Application-level write permissions can be bypassed by a modified butler. There is no database-level enforcement.
- Mitigation: Acceptable in the user-federated model where the user controls all butlers. The permission check prevents accidental cross-domain writes, not malicious ones.

**[Risk: Context vocabulary grows unbounded]** Developers add signal types without discipline, diluting the shared vocabulary.
- Mitigation: The vocabulary is a Python enum. Adding a new signal type requires updating the enum, the permissions table, and the TTL defaults -- a deliberate, reviewable change. No dynamic signal creation.

**[Risk: Query overhead]** Butlers that check context on every decision point add database round trips.
- Mitigation: The `idx_user_context_active` partial index makes active-signal queries fast (typically <10 rows). A future optimization could cache the context in-memory with a short TTL (e.g., 30 seconds) to reduce queries, but this is not needed initially.

**[Trade-off: No derived context]** The system does not infer composite states (e.g., "busy" = meeting OR focused). Each butler interprets raw signals independently.
- Rationale: Derived context adds a processing layer that needs its own logic, versioning, and testing. Raw signals are simpler and let each butler apply its own domain logic.

## Migration Plan

1. **Core migration:** Add `shared.user_context` table via a new core-chain Alembic revision. This is additive -- no existing tables are modified.
2. **Python module:** Add `src/butlers/context_bus.py` with the four public functions (`get_active_context`, `is_user_in_context`, `set_context`, `clear_context`) plus the `ContextSignal` enum and permission table.
3. **Spawner integration:** Add optional context preamble to the spawner's prompt composition. This is backward-compatible -- the preamble is only added when active signals exist.
4. **Butler adoption:** Individual butlers opt in to context checking in their tick handlers and scheduled tasks. No butler is required to change.
5. **Rollback:** Drop the `shared.user_context` table and remove the Python module. No other tables or schemas are affected.

## Open Questions

1. **Should the context preamble be opt-in per butler via `butler.toml`?** A `[butler.context] preamble = true` flag would let butlers control whether they receive context in their LLM sessions. Alternatively, always inject it (it is a few tokens of overhead).
2. **Should there be a cleanup scheduled task for archiving old signals?** Expired signals accumulate over time. A monthly cleanup task could move signals older than 90 days to a `user_context_archive` table, or simply delete them. Not critical for initial implementation.
3. **Should context signals support multiple concurrent values of the same type from the same butler?** The current UNIQUE constraint allows only one `traveling` signal per butler. If the user has two overlapping trips, the butler would need to update the single row. This is likely fine for v1.

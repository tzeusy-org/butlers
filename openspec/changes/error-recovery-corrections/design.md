## Context

Butlers currently have no structured mechanism for users to correct mistakes. When a butler stores wrong data, routes a message to the wrong specialist, creates an incorrect memory, or takes an action in error, the user has no recourse other than ad-hoc conversation ("no, that was wrong, fix it"). This is distinct from the self-healing system, which handles system-level crash recovery automatically. Error recovery corrections are user-initiated, audited, and append-only.

The existing system already has building blocks that corrections can leverage:
- **Memory module** already supports `retracted` as a validity lifecycle state via `memory_forget`
- **Switchboard** already has `route.execute` dispatch and ingestion event tracking
- **Sessions** already track trigger source, request_id, and ingestion_event_id
- **Core daemon** already has a `CORE_TOOL_NAMES` registry for universal tool availability

## Goals / Non-Goals

**Goals:**
- Provide a single `correct` MCP tool available on every butler that handles all correction types
- Ensure corrections are append-only audit events — the correction log is never modified or deleted
- Preserve original data through soft-delete/versioning, never hard-delete
- Define clear preconditions per correction type so LLM sessions know when corrections apply
- Provide unambiguous failure explanations when corrections cannot be applied
- Support cross-butler corrections (misroute re-dispatch goes through Switchboard)

**Non-Goals:**
- Automated error detection — corrections are always user-initiated
- Undo/redo stack — corrections are one-way, not reversible chains
- Self-healing integration — corrections and self-healing are separate systems
- Bulk corrections — each correction targets one specific item
- Real-time correction propagation to external systems (e.g., undoing a sent email)

## Decisions

### Decision 1: Single `correct` tool with type discrimination

**Choice:** One `correct` tool with a `correction_type` enum parameter rather than separate tools per correction type (`correct_data`, `correct_route`, `forget_memory`, `reverse_action`).

**Rationale:** A single tool with clear type discrimination is easier for LLM sessions to discover and reason about. Separate tools fragment the correction surface and increase the chance an LLM picks the wrong tool. The tool description can enumerate all types with their preconditions in one place.

**Alternatives considered:**
- Separate tools per type: Better type safety but worse discoverability for LLMs
- Extending existing tools (e.g., adding correction mode to `memory_forget`): Mixes operational and correction semantics

### Decision 2: Append-only corrections table per butler schema

**Choice:** Each butler schema gets a `corrections` table. Rows are insert-only; no UPDATE or DELETE operations are permitted on correction records.

**Rationale:** Append-only audit is the strongest guarantee for post-hoc accountability. Per-schema tables follow the existing database isolation model (each butler has its own schema). The corrections table is small (corrections are rare events) so per-schema duplication has negligible storage cost.

**Alternatives considered:**
- Shared `corrections` table in shared schema: Would break schema isolation
- JSONB column on sessions table: Would mix session lifecycle with correction lifecycle

### Decision 3: Correction-to-session bidirectional linkage

**Choice:** Each correction row references both the `corrected_session_id` (the session whose output is being corrected) and the `correcting_session_id` (the session performing the correction). The sessions table itself is NOT modified.

**Rationale:** Bidirectional linkage lets you query "what corrections did this session produce?" and "what sessions corrected this session's output?" without modifying the existing sessions schema. All linkage lives in the corrections table.

### Decision 4: Misroute corrections dispatch via Switchboard MCP call

**Choice:** When a `misroute` correction is applied, the correcting butler calls the Switchboard's `correct_route` tool (new tool on Switchboard) with the original request_id and the correct target butler. The Switchboard re-dispatches to the correct butler.

**Rationale:** Only the Switchboard has the routing context (ingestion events, message_inbox records) to re-dispatch correctly. The correcting butler cannot directly call another butler — all inter-butler communication goes through the Switchboard.

**Alternatives considered:**
- Direct butler-to-butler call: Violates the Switchboard-mediated communication model
- User manually re-sends: Poor UX, loses context

### Decision 5: Memory corrections delegate to existing `memory_forget` with correction metadata

**Choice:** `memory_deletion` corrections call the existing `memory_forget` tool internally, which already sets validity to `retracted`. The correction record stores the memory ID, type, and a snapshot of the original content for audit.

**Rationale:** The memory module already has the `retracted` validity state and `memory_forget` tool. Reusing this path avoids duplicating retraction logic. The correction layer adds audit provenance on top.

### Decision 6: Action reversal is best-effort with status reporting

**Choice:** `action_reversal` corrections attempt to reverse a previously taken action (e.g., cancel a reminder, unsend a notification). The correction records whether reversal succeeded, partially succeeded, or failed, with an explanation.

**Rationale:** Not all actions are reversible (e.g., a sent email cannot be unsent, a Telegram message may have been read). The correction must honestly report what it could and couldn't do. Each module can register reversal handlers for its actions.

## Risks / Trade-offs

- **[Risk] Misroute re-dispatch with expired ingestion event** -- The original message may have been dropped from `message_inbox` (1-month retention). Mitigation: The correction record stores enough context (original prompt, source channel) to reconstruct a re-dispatch even without the original ingestion event. If reconstruction fails, the correction fails with an explanation.

- **[Risk] LLM misuses correction tool for normal operations** -- An LLM might try to use `correct` to update data instead of the normal state/memory tools. Mitigation: Tool description explicitly states corrections are for fixing previous mistakes, not for normal data updates. Preconditions require referencing a specific session or data item that was wrong.

- **[Risk] Correction of a correction (chain depth)** -- A correction could itself be wrong. Mitigation: Corrections can reference other corrections (the `corrected_session_id` could be a session that performed a correction). The append-only log preserves the full chain. No explicit depth limit, but the tool description discourages correction chains.

- **[Trade-off] Per-schema corrections table vs shared** -- Per-schema means the Switchboard cannot see all corrections across butlers without cross-schema queries. Acceptable because corrections are butler-local operations; the only cross-butler operation (misroute) is mediated by the Switchboard's own correction records.

- **[Trade-off] Snapshot of original data in correction record** -- Storing a copy of the original data (e.g., the memory content before retraction) increases storage but is essential for audit. The correction record is the ONLY place the original data is preserved in human-readable form after soft-delete.

## Migration Plan

1. **Database migration**: Add `corrections` table to each butler schema via Alembic migration. No existing tables are modified.
2. **Core tool registration**: Add `correct` to `CORE_TOOL_NAMES` and implement the tool handler in core tools.
3. **Switchboard extension**: Add `correct_route` tool to Switchboard butler for misroute re-dispatch.
4. **Memory module extension**: Wire `memory_deletion` corrections to call `memory_forget` with correction provenance.
5. **Rollback**: Drop `corrections` table, remove `correct` from `CORE_TOOL_NAMES`. No data loss in other tables since corrections only ADD rows, never modify existing data.

## Open Questions

- ~~Should there be a rate limit on corrections per session to prevent LLM correction loops?~~ **Resolved:** Yes. 10 corrections per source session per rolling hour. See spec for details.
- Should corrections be surfaced in the dashboard? (Likely yes, but dashboard design is out of scope for this change.)

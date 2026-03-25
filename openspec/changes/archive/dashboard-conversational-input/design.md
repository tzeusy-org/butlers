## Context

The Butlers dashboard currently serves as a read-heavy admin/control plane. Operators can observe butler sessions, inspect state, manage schedules, and trigger ad-hoc runs — but they cannot have a conversation with a butler without leaving the dashboard to use Telegram, email, or another connector channel. This creates a context-switching penalty during diagnostics and testing.

The system already has a mature ingestion pipeline: connectors normalize messages into `ingest.v1` envelopes, submit them to the Switchboard via MCP, which deduplicates, classifies, routes, and spawns butler sessions. The dashboard can plug into this same pipeline as just another "connector" — an internal one that bypasses the discretion layer and uses the `"interactive"` policy tier for immediate processing.

Key existing infrastructure:
- **Switchboard ingestion API** (`ingest_v1` MCP tool) accepts `ingest.v1` envelopes
- **Session lifecycle** creates session records with `request_id` and `ingestion_event_id` for lineage
- **SSE infrastructure** (`/api/events`) already exists for real-time event broadcasting
- **MCPClientManager** provides lazy MCP client connections to butler daemons
- **DatabaseManager** supports cross-butler fan-out and shared schema access

## Goals / Non-Goals

**Goals:**
- Enable operators to converse with any butler directly from the dashboard without context-switching
- Create real, auditable butler sessions (same lineage tracking as Telegram/email)
- Provide streaming responses with tool call visibility and cost awareness
- Persist conversation threads per-butler with full message history
- Integrate seamlessly with the existing ingestion pipeline (no new spawner logic)

**Non-Goals:**
- Multi-user conversation support (dashboard is single-operator; no concurrent user tracking)
- Real-time collaboration (no WebSocket push for conversation updates to other tabs)
- Rich media attachments in dashboard conversations (text-only for v1)
- Conversation export/import
- Replacing the existing trigger endpoint (`POST /api/butlers/{name}/trigger`) — that remains for fire-and-forget runs
- Custom system prompts per conversation (the butler's CLAUDE.md is always used)

## Decisions

### D1: Dashboard as Internal Connector (not direct spawner call)

**Decision:** Dashboard conversations submit `ingest.v1` envelopes to the Switchboard rather than calling the spawner directly.

**Rationale:** This preserves the architectural invariant that all messages enter through the Switchboard. Benefits: deduplication, lineage via `shared.ingestion_events`, ingestion policy evaluation (global rules), and consistent request context assignment. The dashboard becomes just another source channel, reusable in cost reports, timeline views, and audit logs.

**Alternative considered:** Direct spawner invocation via MCP (like the existing trigger endpoint). Rejected because it bypasses lineage tracking, deduplication, and ingestion policy evaluation. It would also require custom session attribution logic that already exists in the Switchboard pipeline.

### D2: SSE on POST Endpoints (not separate SSE channel)

**Decision:** Streaming responses use SSE on the same POST request that creates/continues a conversation, not a separate `GET /api/events` SSE subscription.

**Rationale:** The existing `/api/events` SSE endpoint is a broadcast channel for system-wide events (butler_status, session_start/end). Conversation responses are per-request, bidirectional (request-response), and must correlate to a specific message. Using the POST response as an SSE stream is simpler — the client makes one request and reads the response stream. No subscription management, no message routing, no state synchronization.

**Alternative considered:** WebSocket for full-duplex communication. Rejected because it adds transport complexity (WS upgrade, reconnection logic, heartbeats) for a use case that is fundamentally request-response. SSE over POST is sufficient since the user sends one message and waits for one streamed response.

### D3: Conversation Tables in `shared` Schema

**Decision:** Conversation and message tables live in the `shared` schema, not in individual butler schemas.

**Rationale:** Conversations are dashboard-scoped, not butler-scoped. A user may want to search across conversations from all butlers, view aggregate costs, or list recent conversations regardless of butler. Placing tables in `shared` enables direct queries without fan-out. Butler name is a column on the conversation table, indexed for per-butler filtering.

**Alternative considered:** Per-butler tables (one `conversations` table per butler schema). Rejected because cross-butler conversation listing would require fan-out queries, adding latency and complexity for what should be a simple list page.

### D4: Conversation Context in Envelope Payload

**Decision:** For follow-up messages, prior conversation context (last N exchange pairs) is included in the `payload.normalized_text` as a preamble, not as a separate mechanism.

**Rationale:** The butler receives a single prompt per session. Including conversation context in the normalized text means the butler sees the full relevant history without any changes to the spawner, session creation, or prompt injection code. This is the same pattern used by Telegram conversations where the connector includes recent chat history.

**Trade-off:** Context length grows with conversation depth. The default of 5 exchange pairs (10 messages) is bounded and configurable. Very long conversations may need summarization in a future iteration.

### D5: New `"dashboard"` Source Channel (not reusing `"api"`)

**Decision:** Add a new `"dashboard"` value to the `SourceChannel` enum rather than reusing `"api"`.

**Rationale:** Attribution clarity. The `"api"` channel is semantically for programmatic external API calls. Dashboard conversations are interactive, operator-initiated, and have different cost attribution, policy, and UX properties. A dedicated channel enables:
- Filtering dashboard conversations in timeline views
- Separate cost reporting for dashboard usage
- Ingestion policy rules scoped to dashboard (e.g., always `interactive` tier)
- Clear audit trail distinguishing operator chat from API calls

### D6: `trigger_source = "dashboard"` (not `"external"`)

**Decision:** Add `"dashboard"` to `TRIGGER_SOURCES` rather than reusing `"external"`.

**Rationale:** Same attribution clarity argument as D5. Sessions triggered by dashboard conversations should be distinguishable in session lists, cost reports, and audit logs. The `"external"` trigger source implies a connector-submitted message; `"dashboard"` is specifically operator-initiated interactive chat.

### D7: Response Streaming via Switchboard Callback

**Decision:** The dashboard API endpoint submits the ingest envelope, then polls/subscribes for the resulting session's output to stream it back as SSE.

**Implementation approach:** After ingestion submission, the dashboard API receives the `request_id`. It then monitors for a session with that `request_id` in the butler's sessions table. Once found, it polls session completion status and streams the result. For real-time token streaming, the endpoint subscribes to the existing SSE broadcast channel filtered by `session_id` for `session_end` events, then reads the completed session output.

**Trade-off:** v1 does not provide true token-by-token streaming from the LLM. The response appears as a complete message once the session finishes. True streaming would require changes to the spawner/adapter to emit partial results, which is a future enhancement. The SSE infrastructure is in place for when that capability is added — the frontend already handles `token` events.

## Risks / Trade-offs

**[Risk] Response latency for v1 (non-streaming)** — Without true token-by-token streaming from the spawner, dashboard conversations will show a typing indicator for the full session duration, then display the complete response. For complex sessions (30s+), this may feel unresponsive.
Mitigation: The SSE infrastructure is designed for streaming; when the spawner gains partial-result emission, the dashboard will automatically benefit. The typing indicator and keepalive comments maintain connection liveness.

**[Risk] Conversation context size** — Including last N messages in the normalized text could hit model context limits for very long conversations.
Mitigation: Default limit of 5 exchange pairs (10 messages). Configurable per-butler. Future enhancement: LLM-generated conversation summaries for longer threads.

**[Risk] Shared schema table growth** — High-volume dashboard usage could grow `dashboard_messages` rapidly.
Mitigation: UUID7 primary keys enable efficient time-range queries. Archiving conversations does not delete rows. A future retention policy (like the memory tier system) can prune old conversations.

**[Risk] SSE connection timeout** — Long-running butler sessions might exceed proxy/browser SSE timeout limits.
Mitigation: 15-second keepalive comments prevent connection drops. FastAPI's `StreamingResponse` with `X-Accel-Buffering: no` header disables Nginx buffering.

## Migration Plan

1. **Database migration**: Create `shared.dashboard_conversations` and `shared.dashboard_messages` tables via Alembic migration
2. **Backend changes** (can be deployed independently):
   - Add `"dashboard"` to `SourceChannel` and channel-provider map
   - Add `"dashboard"` to `TRIGGER_SOURCES`
   - Add conversation API endpoints
3. **Frontend changes**: Add chat panel components, conversation hooks, route integration
4. **Rollback**: Drop the two new tables. Remove `"dashboard"` from enums (no existing data depends on it). Revert frontend components.

No breaking changes to existing APIs or data. All changes are additive.

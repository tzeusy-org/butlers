## Why

The dashboard currently serves as an admin observation/control plane, but operators must leave it entirely to interact with butlers conversationally — switching to Telegram, email, or other connectors. Adding a conversational chat interface directly in the dashboard eliminates this context-switch penalty, letting operators talk to any butler while viewing its sessions, state, and telemetry side-by-side. This also provides a first-party "connector" that creates real, auditable butler sessions (same lineage as Telegram/email), enabling rapid testing, ad-hoc queries, and operational diagnostics without external dependencies.

## What Changes

- **Dashboard chat UI**: Per-butler conversational interface as a slide-out panel on butler detail pages. Supports starting new conversations, continuing existing ones, archiving old threads, markdown rendering, typing indicators, tool call visibility, cost/token display, and quick-switching between conversations.
- **Conversation persistence layer**: New `shared.dashboard_conversations` and `shared.dashboard_messages` tables storing conversation threads with full message history, model attribution, token counts, and duration.
- **Dashboard API endpoints**: REST endpoints for conversation CRUD (list, create, continue, archive, rename) plus SSE streaming for real-time response delivery during active conversations.
- **Dashboard as a connector channel**: Dashboard conversations submit `ingest.v1` envelopes to the Switchboard with `source.channel = "dashboard"` and `source.provider = "internal"`, reusing the existing MCP trigger infrastructure. No new backend spawner logic needed — the dashboard is just another ingestion source.
- **Source channel/provider extension**: Add `"dashboard"` to the `SourceChannel` enum and allow the `dashboard`/`internal` pairing in the channel-provider validation map.
- **Trigger source extension**: Dashboard-originated sessions use `trigger_source = "dashboard"` for attribution and lineage tracking distinct from other external sources.

## Capabilities

### New Capabilities
- `dashboard-conversations`: Conversation data model, persistence layer, and API endpoints for per-butler conversational threads with message history, SSE streaming, and lifecycle management (create, continue, archive, rename, search)
- `dashboard-chat-ui`: Frontend chat interface component — slide-out panel on butler detail pages with markdown rendering, typing indicators, tool call visibility, cost display, conversation switching, and search across history

### Modified Capabilities
- `connector-base-spec`: Add `"dashboard"` to `SourceChannel` enum and `dashboard`/`internal` to the allowed channel-provider pairings
- `core-sessions`: Add `"dashboard"` to the `TRIGGER_SOURCES` frozenset for session attribution
- `ingestion-event-registry`: Dashboard conversations create ingestion events with `source_channel = "dashboard"`, no functional spec changes needed (the existing model handles it) — but documenting the new channel value as a valid source

## Impact

- **Database**: Two new tables in the `shared` schema (`dashboard_conversations`, `dashboard_messages`), plus an Alembic migration
- **Switchboard contracts**: `SourceChannel` and channel-provider validation in `roster/switchboard/tools/routing/contracts.py` gain the `"dashboard"` channel
- **Session tracking**: `TRIGGER_SOURCES` in `src/butlers/core/sessions.py` gains `"dashboard"`
- **Dashboard API**: New endpoints under `/api/butlers/{name}/conversations/` with SSE support
- **Dashboard frontend**: New chat components, conversation hooks, route additions to butler detail pages
- **No new backend spawner logic**: Reuses existing `ingest.v1` → Switchboard → butler session flow

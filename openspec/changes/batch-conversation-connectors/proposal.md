## Why

The `telegram_user_client` and `whatsapp_user_client` connectors flush per-chat batches every 10 minutes (default `flush_interval_s=600`). With active chats this produces ~36 switchboard ingestions per chat per day, each triggering LLM classification in the pipeline. The pipeline routes each batch as a single unit to one butler, but a 10-minute conversation window frequently contains topics spanning multiple butler domains (e.g., a friend mentions a restaurant recommendation AND a shared expense in the same chat window). This results in high token cost from frequent LLM classification calls and lost signal when multi-domain conversations are routed to only one butler.

## What Changes

- **Connector batch interval**: Default `flush_interval_s` increases from 600 to 1800 (30 min); `history_time_window_m` increases from 30 to 35 (5 min overlap for context continuity). Discretion layer remains unchanged (local model, effectively free).
- **Envelope tagging**: Connectors tag conversation history batches with `control.payload_type = "conversation_history"` so the switchboard can distinguish these from single-event or other batch types.
- **Dashboard-configurable flush interval**: The connector detail card exposes `flush_interval_s` as an editable setting (via existing `PATCH /connectors/{type}/{identity}/settings` API and `settings` JSONB column). Connectors read this on each flush scanner cycle.
- **Switchboard conversation decomposition**: New pipeline step triggered by `payload_type == "conversation_history"`. Runs signal-extraction to decompose conversation batches into per-butler "conceptual messages", each containing cherry-picked relevant message excerpts (duplicating messages across concepts when they belong to multiple contexts). Fan-out via existing `route()` mechanism.
- **Empty decomposition handling**: When signal-extraction returns `[]`, the result is logged to `decomposition_output` for dashboard visibility, but no LLM classification or routing is invoked.
- **Decomposition storage**: Results stored in the existing `decomposition_output` JSONB field on `message_inbox`.

## Capabilities

### New Capabilities
- `conversation-decomposition`: Switchboard pipeline step that decomposes conversation history batches into per-butler conceptual messages with cherry-picked excerpts, fans out to multiple butlers from a single ingestion event
- `dashboard-connector-batch-settings`: Dashboard UI card for configuring connector batch parameters (flush_interval_s) with live-reload on connector flush scanner cycle

### Modified Capabilities
- `connector-telegram-user-client`: Default flush_interval_s 600->1800, history_time_window_m 30->35, new `control.payload_type` envelope field, reads flush_interval_s from dashboard settings
- `module-pipeline`: New decomposition branch for `payload_type == "conversation_history"` envelopes — runs signal-extraction before LLM classification, supports multi-butler fan-out from single ingestion event
- `telegram-user-client-conversation-history`: Default flush interval 600->1800, history window 30->35, new payload_type tag on batch envelope

## Impact

- **Connectors**: `telegram_user_client.py`, `whatsapp_user_client.py` — config defaults, envelope assembly, settings reload
- **Switchboard pipeline**: `src/butlers/modules/pipeline.py` — new decomposition branch
- **Signal extraction**: `roster/switchboard/.agents/skills/signal-extraction/` — invoked programmatically from pipeline (currently skill-only)
- **Dashboard frontend**: `ConnectorDetailPage.tsx` — new batch settings card
- **Dashboard API**: Existing `PATCH /settings` endpoint, no new routes needed
- **Database**: Existing `decomposition_output` and `settings` columns used, no schema migration needed
- **Cost model**: ~12 decomposition calls/day/chat (down from ~36 classification calls) — net 3x reduction in call frequency; per-call token cost increases but total cost decreases

## Context

The `telegram_user_client` and `whatsapp_user_client` connectors currently flush per-chat message buffers every 10 minutes (configurable via `flush_interval_s`). Each flush produces a single `ingest.v1` envelope routed as one unit through the switchboard pipeline. The pipeline classifies each batch via LLM and routes it to a single butler.

Problems:
1. **High call frequency**: ~36 LLM classification calls per active chat per day
2. **Single-butler routing**: A 10-minute conversation window often spans multiple butler domains (e.g., restaurant recommendation + shared expense), but the entire batch routes to one butler — losing signal for the others
3. **No dashboard control**: Flush interval is env-var only, requiring connector restart to change

Existing infrastructure that supports this change:
- `settings` JSONB column on `connector_registry` + `PATCH /settings` API + frontend mutation hooks
- `decomposition_output` JSONB column on `message_inbox`
- Signal-extraction skill with per-butler schema definitions
- `dispatch_outcomes` JSONB for fan-out tracking
- Discretion layer runs on local model (effectively free, stays as-is)

## Goals / Non-Goals

**Goals:**
- Reduce LLM classification call frequency by 3x (36 → 12 calls/day/chat)
- Enable multi-butler routing from a single conversation batch via decomposition
- Make flush interval configurable from the dashboard without connector restart
- Cherry-pick relevant messages per conceptual message (allowing duplicates across concepts)
- Log empty decompositions for dashboard visibility without invoking LLM runtimes

**Non-Goals:**
- Urgency escape hatch / early flush for time-sensitive messages — 30-min latency is acceptable for user_client channels
- Changing the WhatsApp bridge protocol or Telethon transport
- Modifying the discretion layer behavior (stays as-is, local model)
- Real-time streaming decomposition (decomposition is batch-only)
- Changes to the bot connector or Gmail connector flush behavior

## Decisions

### D1: Envelope tagging via `control.payload_type`

Add `control.payload_type = "conversation_history"` to batch envelopes from user_client connectors. This signals the pipeline to enter the decomposition branch instead of standard single-target LLM classification.

**Why not a new source channel?** The source channel (`telegram_user_client`) already correctly identifies the connector. Payload type is orthogonal — it describes the shape of the payload, not the source. Future connectors (e.g., Discord user client) can reuse the same `conversation_history` type without inventing new channels.

**Why not check envelope heuristically?** Explicit tagging is cheaper than inspecting `sender.identity == "multiple"` + `payload.raw.conversation_history` existence, and it's forward-compatible with other batch types.

### D2: Decomposition in pipeline.process(), not ingest_v1()

The decomposition step runs in the background pipeline processing (`pipeline.process()`) after persistence, not in the synchronous `ingest_v1()` function.

**Why:** Ingest is a hot path (connector blocks on response). Decomposition involves LLM calls (signal-extraction) that can take seconds. Keeping ingest fast preserves the 202-accepted-and-persist pattern. The `decomposition_output` field on `message_inbox` is already designed for post-persist enrichment.

**Alternative considered:** Pre-persist decomposition in ingest. Rejected because it blocks the connector and adds latency to the ingest acknowledgment loop.

### D3: Signal-extraction invoked programmatically, not via skill framework

The pipeline calls signal-extraction logic as a Python function, not by spawning a Claude Code instance with the skill. The extraction prompt template and registered butler schemas are loaded from the skill directory but executed via direct LLM API call (same pattern as discretion evaluation and pipeline classification).

**Why:** Spawning a full CC instance for each decomposition would be 10-100x more expensive than a direct API call with a focused prompt. The signal-extraction skill already defines the contract (JSON array output, per-butler schemas) — we just invoke it differently.

### D4: Cherry-picked message excerpts per conceptual message

Each conceptual message in the decomposition output contains only the messages relevant to that concept, not the full 35-minute conversation window. Messages that belong to multiple contexts are duplicated across conceptual messages.

**Why:** Downstream butlers receive focused context instead of a wall of chat. This reduces token consumption at the butler LLM level (the most expensive tier) and improves routing accuracy. Duplication is acceptable — dedup at the butler level is by `request_id` + signal type, not by message content.

**Format:** Each conceptual message carries:
- `signal_type`: e.g., "finance", "health", "relationship"
- `target_butler`: destination butler name
- `tool_name` + `tool_args`: MCP tool call on target butler
- `excerpts`: array of cherry-picked `{sender, text, timestamp, message_id}` from the conversation
- `confidence`: HIGH/MEDIUM/LOW

### D5: Dashboard-configurable flush interval with live reload

The connector reads `flush_interval_s` from the `settings` JSONB column on each flush scanner cycle (every 60 seconds). No connector restart needed.

**Precedence:** Dashboard setting > environment variable > hardcoded default (1800s).

**Why per-scanner-cycle reload?** The flush scanner already wakes every 60 seconds to check all buffers. Reading a cached settings value at that point is near-zero cost and gives <60s propagation latency for setting changes.

### D6: Empty decomposition → log only, no LLM

When signal-extraction returns `[]` (no signals found), the result is written to `decomposition_output` as `{"signals": [], "reason": "no_signals_extracted"}` and the message is marked as `lifecycle_state = "decomposed_empty"`. No LLM classification or routing is invoked. The dashboard can query for these to show drop rates.

**Why not fall through to standard classification?** If the decomposition model finds nothing signal-worthy in 30 minutes of chat, the standard classifier is unlikely to do better — it would just cost more tokens for the same null result. The discretion layer (which runs before ingest) already filters truly irrelevant chats.

## Risks / Trade-offs

**[30-min latency for all user_client messages]** → Acceptable per product decision. No urgency escape hatch. If this becomes a problem, `flush_interval_s` can be tuned down per-connector via dashboard.

**[Decomposition model quality]** → Signal-extraction prompt quality determines routing accuracy. If the model misses signals or hallucinates butler mappings, messages get lost. → Mitigation: empty decompositions are logged; `decomposition_output` preserves full extraction results for debugging; confidence thresholds can be added later.

**[Increased per-call token cost]** → 30-min windows are ~3x larger than 10-min windows, so each decomposition call costs more tokens. Net savings still positive because call frequency drops 3x and we eliminate one LLM tier (pipeline classification). → Monitor via existing `evaluation_latency_ms` and token usage metrics.

**[Message duplication across concepts]** → Same message appears in multiple conceptual messages routed to different butlers. → Acceptable by design. Butler-level dedup is by `request_id` + signal type. Memory/relationship extractors already handle overlapping context.

**[Settings live-reload race condition]** → If `flush_interval_s` is changed while a flush is in progress, the in-flight flush uses the old value and the next scanner cycle picks up the new one. → Acceptable; convergence within 60 seconds.

## Migration Plan

1. **Phase 1 — Connector changes (no pipeline changes yet):** Bump defaults, add `payload_type` tag, add settings live-reload. Deploy. Batches still route through standard pipeline (ignores unknown `payload_type`).
2. **Phase 2 — Dashboard UI:** Add batch settings card to ConnectorDetailPage. Deploy independently.
3. **Phase 3 — Pipeline decomposition:** Add decomposition branch in `pipeline.process()`. Deploy. Conversation history batches now decompose and fan out.
4. **Rollback:** Remove `payload_type` check in pipeline → batches fall through to standard classification. Dashboard settings card is harmless without pipeline support.

## Open Questions

None — all decisions settled with user.

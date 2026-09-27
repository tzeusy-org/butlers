## Why

JARVIS pursuit run-07 rank #8 (`docs/redesigns/2026-07-25-jarvis-pursuit.md`,
bu-ep4ks.8) identifies that a butler holds two disconnected memories for one
owner: `public.dashboard_conversations` gives dashboard chat a durable,
session-linked thread, but every other inbound channel (Telegram, email) is
stateless per-flush batches over raw `message_inbox` history even though
those channels already normalize the same `source_thread_identity` concept at
ingest. Separately, every spawned session composes a fresh system prompt from
scratch (`src/butlers/core/spawner.py`) with no provider-native resume
handle, so a conversational turn can never reuse the prior turn's warm
prompt-cache state even when the underlying CLI supports resuming a session.

This change landed the ledger/provider-layer primitives underneath both gaps
and their deliberately narrow live wiring in [#3592](https://github.com/Tzeusy/butlers/pull/3592).
The route processor creates a conversation anchor only after it knows the
target butler, and the spawner resolves and persists a provider-resume handle
only for conversational `trigger_source == "route"` dispatches. It is the
layer beneath bu-27dxl.9 (Telegram context injection/ack/voice/inline
confirms), not that app-layer work itself.

## Status correction (2026-08-13)

PR #3592 reviewed head `3b992b1d70da16a8b5577caeac0f5cc9ca3d7cd9` against
base `10661019436644ba8253a880c3fac385781987f5`, completed CI run
`30185159686` (`check`, `frontend`, `frontend-e2e`, `em-dash-guard`, and
`session-link-guard`), and landed as squash
`91fff3a5a9f9fc067818c882f5e1e9947b74405e` on `2026-07-26T03:28:52Z`.
That landing completes tasks 4.1-4.2 below. Earlier wording that live
routing/spawn wiring was absent is historical and is no longer current.

## What Changes

- `public.dashboard_conversations` (migration `core_185`) gains:
  - `source_channel` / `source_thread_identity` — generalizes the table from
    a dashboard-only creation path into a channel-agnostic conversation
    anchor, addressable by any channel that already normalizes a thread
    identity at ingest.
  - `provider_session_id` / `provider_runtime_type` / `provider_session_updated_at`
    — a one-handle-per-conversation provider resume ledger ("one memory per
    thread").
  - A partial unique index on `(butler_name, source_channel,
    source_thread_identity)` so concurrent ingress for the same thread
    converges on one anchor row via `INSERT ... ON CONFLICT DO NOTHING`.
- `butlers.api.conversations` gains `conversation_get_or_create_by_thread`
  (the generalized upsert), `conversation_get_provider_session` /
  `conversation_set_provider_session` / `conversation_clear_provider_session`,
  and the pure `resolve_resume_handle` TTL/eviction helper (24h staleness
  window, provider-scoped — a handle is only usable by the runtime_type that
  minted it).
- `RuntimeAdapter` gains a `supports_resume: bool` class attribute (default
  `False`) as the capability-check extension point. `ClaudeCodeAdapter` sets
  it `True`, accepts an optional `resume_session_id` to pass `--resume
  <id>` to the Claude CLI, and captures whatever `session_id` the CLI reports
  for the turn (fresh or resumed) into `last_process_info["provider_session_id"]`
  so a caller can persist it for the next turn. No other adapter's `invoke()`
  signature changes.

## What Does Not Change Yet (explicit follow-up)

- The landed wiring remains deliberately scoped: the route processor creates
  an anchor after classification has selected the target butler, and
  `spawner.py` uses provider resume only for conversational
  `trigger_source == "route"` dispatches. It does not turn every ingestion
  path into a generic conversation reader or broaden provider resume beyond
  that routed interactive boundary.
- First-token streaming and the unified Conversations read surface (rank #8's
  slices 3-4) are not part of this change.

## Capabilities

### New Capabilities

(none — this extends `dashboard-conversations`)

### Modified Capabilities

- `dashboard-conversations`: conversation data model gains the anchor +
  provider-resume-ledger columns; new requirements cover the channel-agnostic
  upsert and the provider resume ledger contract.

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

This change lands the ledger/provider-layer primitives underneath both gaps
without wiring them into the live spawn/routing hot path yet (that
integration — deciding where in `pipeline.py`/`spawner.py` to actually
consume these primitives per trigger source — is significant, separately
risky work against an already-intricate same-tier-failover loop, and is
called out as follow-up rather than bundled here). It is the layer beneath
bu-27dxl.9 (Telegram context injection/ack/voice/inline confirms), not that
app-layer work itself.

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

- Nothing calls `conversation_get_or_create_by_thread` from the Telegram/email
  ingest paths yet, and nothing in `spawner.py`'s failover loop looks up or
  passes `resume_session_id` yet. Both are real, mergeable primitives with
  their own test coverage, but wiring them into the live routing/spawn hot
  path is deliberately out of scope for this change (see the discovered
  follow-up items filed against bu-ep4ks.8).
- First-token streaming and the unified Conversations read surface (rank #8's
  slices 3-4) are not part of this change.

## Capabilities

### New Capabilities

(none — this extends `dashboard-conversations`)

### Modified Capabilities

- `dashboard-conversations`: conversation data model gains the anchor +
  provider-resume-ledger columns; new requirements cover the channel-agnostic
  upsert and the provider resume ledger contract.

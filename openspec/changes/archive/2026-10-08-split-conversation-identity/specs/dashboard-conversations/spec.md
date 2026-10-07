## MODIFIED Requirements

### Requirement: Conversation Data Model

The `public.dashboard_conversations` table SHALL store conversation thread metadata. Each conversation belongs to exactly one butler and progresses through a defined lifecycle.

#### Scenario: Conversation table schema

- **WHEN** the migration creates the `public.dashboard_conversations` table
- **THEN** the table SHALL contain the following columns:
  - `id` (UUID7, primary key) — time-ordered unique identifier
  - `butler_name` (TEXT, NOT NULL) — the butler this conversation belongs to
  - `title` (TEXT, nullable): auto-generated or user-edited title; the API always populates it from the first user message (no DB-level default)
  - `status` (TEXT, NOT NULL, default `'active'`) — one of `active`, `archived`
  - `created_at` (TIMESTAMPTZ, NOT NULL, default `now()`) — when the conversation was started
  - `updated_at` (TIMESTAMPTZ, NOT NULL, default `now()`) — when the last message was added
  - `message_count` (INTEGER, NOT NULL, default `0`) — denormalized count of messages
  - `routed_butler` (TEXT, nullable): the butler this conversation's first message was routed to by Switchboard classification; NULL for pinned per-butler conversations (already deterministic) and for classification-routed conversations that haven't routed yet (e.g. a bug-lane report, which never targets a domain butler)
  - `source_channel` (TEXT, NOT NULL, default `'dashboard'`): origin channel for the conversation (`'dashboard'`, `'telegram'`, `'email'`, ...); every pre-existing row backfills as `'dashboard'`
  - `source_thread_identity` (TEXT, nullable): legacy anchor key, written with the same value as `external_conversation_id` so both unique indexes stay congruent; NULL for dashboard-created rows, which are already anchored 1:1 on `id`
  - `external_conversation_id` (TEXT, nullable): the channel-stable conversation key from `event.external_conversation_id` (for example `telegram:<chat_id>`), distinct from any per-message reply target; NULL for dashboard-created rows
  - `provider_session_id` (TEXT, nullable): the most recent provider-native session/resume handle minted for this conversation
  - `provider_runtime_type` (TEXT, nullable): which runtime adapter type minted `provider_session_id` — a handle is only resumable by the same adapter type
  - `provider_session_updated_at` (TIMESTAMPTZ, nullable): when `provider_session_id` was last refreshed; governs TTL-based resume eligibility

#### Scenario: Conversation table indexes

- **WHEN** the migration creates indexes
- **THEN** a composite index on `(butler_name, status, updated_at DESC)` SHALL exist for listing active conversations per butler
- **AND** a composite index on `(butler_name, updated_at DESC)` SHALL exist for chronological listing
- **AND** a unique index on `(butler_name, source_channel, source_thread_identity)` WHERE `source_thread_identity IS NOT NULL` SHALL exist
- **AND** a partial unique index on `(butler_name, source_channel, external_conversation_id)` WHERE `external_conversation_id IS NOT NULL` SHALL exist and SHALL be the anchor upsert target, so concurrent ingress for the same conversation converges on one anchor row

#### Scenario: Legacy per-message Telegram anchors collapse reversibly

- **WHEN** the conversation-identity migration (core_263) finds several Telegram bot anchors for one butler and chat, whether keyed by a legacy `<chat_id>:<message_id>`, a bare `<chat_id>`, or the earlier stable `telegram:<chat_id>`
- **THEN** it SHALL keep one anchor per chat, choosing the row with the newest provider-session handle, and key it `telegram:<chat_id>` in both identity columns
- **AND** it SHALL re-link `dashboard_messages` and `dashboard_conversation_turns` onto the survivor before deleting the other rows, and recount the survivor's `message_count`
- **AND** Telegram user-client and WhatsApp anchors SHALL gain the `telegram:` and `whatsapp:` namespaces their connectors emit, and every other anchored row SHALL copy its identity verbatim
- **AND** the transform SHALL run once, on the first schema to install the column, because core revisions replay per schema against shared `public` tables
- **AND** the rollout SHALL stop every conversation-anchor writer before the upgrade; no mixed-version compatibility trigger exists

#### Scenario: Identity split downgrade restores without reverting later activity

- **WHEN** the last schema at or past core_263 downgrades
- **THEN** it SHALL restore the deleted anchors, their message and turn links, and every original identity from private `core_263_*` snapshots
- **AND** it SHALL keep each survivor's other post-upgrade columns (title, status, provider handle) and recount its `message_count`
- **AND** it SHALL drop the column, the index, and the snapshot tables, so a later upgrade snapshots afresh and refuses to reuse a leftover snapshot

#### Scenario: Sticky routed_butler stamping

- **WHEN** a classification-routed (Switchboard-addressed) conversation's message is submitted and Switchboard's triage produces a `route_to` decision with a target butler, and the conversation has no `routed_butler` yet
- **THEN** `routed_butler` is set to that target butler
- **AND** a later `route_to` decision for the same conversation (e.g. from a follow-up that still goes through classification) does NOT overwrite an already-set `routed_butler` — the first successful route wins

### Requirement: Channel-Agnostic Conversation Anchor

`conversation_get_or_create_by_thread` SHALL let any inbound channel that
supplies a stable `external_conversation_id` at ingest (Telegram, email, ...)
obtain a durable `public.dashboard_conversations` anchor row for that
conversation, without needing its own separate conversation-identity concept.
This generalizes conversation creation beyond the dashboard-only
`conversation_create` path. The key SHALL be the connector's conversation
identity, never its per-message reply target; the helper SHALL NOT rewrite
the key it receives. A dashboard-channel route SHALL instead resolve its
existing conversation by id and never call this helper.

#### Scenario: First ingress for a conversation creates the anchor

- **WHEN** `conversation_get_or_create_by_thread` is called with a
  `(butler_name, source_channel, external_conversation_id)` combination that
  has no existing row
- **THEN** a new `dashboard_conversations` row is inserted with that
  `source_channel`, the key in both `external_conversation_id` and
  `source_thread_identity`, an auto-generated title from `first_message`,
  `status = 'active'`, and `message_count = 0`
- **AND** the function returns `(conversation, is_new=True)`

#### Scenario: Repeat ingress for the same conversation reuses the anchor

- **WHEN** `conversation_get_or_create_by_thread` is called again with the
  same `(butler_name, source_channel, external_conversation_id)` combination
- **THEN** no new row is inserted
- **AND** the function returns the existing row with `is_new=False`, even if
  a different `first_message` was supplied on the repeat call

#### Scenario: Consecutive Telegram messages share one provider lineage

- **WHEN** two routed Telegram ingests from one chat carry distinct
  `reply_target_ref` values and the same `external_conversation_id`
- **THEN** both calls return the same conversation anchor in either arrival order
- **AND** a provider session stored after the first turn is resumed by the
  second turn

#### Scenario: Concurrent anchor conflict resolution is connection-bound

- **WHEN** callers for one conversation overlap, including while an insert
  conflict commits or rolls back
- **THEN** insert, conflict fallback, and result selection execute under one
  transaction-scoped advisory lock keyed by the conversation identity
- **AND** the insert and fallback select use one acquired PostgreSQL connection
- **AND** rollback leaves no anchor from the failed transaction, while retry
  creates or resolves exactly one anchor without timing-based sleeps

#### Scenario: Different channel or conversation identity never collides

- **WHEN** two calls share a `butler_name` but differ in `source_channel` or
  `external_conversation_id`
- **THEN** each gets its own distinct anchor row

#### Scenario: Pre-existing dashboard-created rows are unaffected

- **WHEN** a conversation was created via `conversation_create` (the
  dashboard-only path, `external_conversation_id IS NULL` and
  `source_thread_identity IS NULL`)
- **THEN** it is never matched or overwritten by
  `conversation_get_or_create_by_thread`, and neither partial unique index
  (each governs only rows whose key is non-null) conflicts with it

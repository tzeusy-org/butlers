## ADDED Requirements

### Requirement: Channel-Agnostic Conversation Anchor

`conversation_get_or_create_by_thread` SHALL let any inbound channel that
already normalizes a `source_thread_identity` at ingest (Telegram, email,
...) obtain a durable `public.dashboard_conversations` anchor row for that
thread, without needing its own separate conversation-identity concept. This
generalizes conversation creation beyond the dashboard-only
`conversation_create` path.

#### Scenario: First ingress for a thread creates the anchor

- **WHEN** `conversation_get_or_create_by_thread` is called with a
  `(butler_name, source_channel, source_thread_identity)` combination that
  has no existing row
- **THEN** a new `dashboard_conversations` row is inserted with that
  `source_channel` and `source_thread_identity`, an auto-generated title from
  `first_message`, `status = 'active'`, and `message_count = 0`
- **AND** the function returns `(conversation, is_new=True)`

#### Scenario: Repeat ingress for the same thread reuses the anchor

- **WHEN** `conversation_get_or_create_by_thread` is called again with the
  same `(butler_name, source_channel, source_thread_identity)` combination
- **THEN** no new row is inserted
- **AND** the function returns the existing row with `is_new=False`, even if
  a different `first_message` was supplied on the repeat call

#### Scenario: Different channel or thread identity never collides

- **WHEN** two calls share a `butler_name` but differ in `source_channel` or
  `source_thread_identity`
- **THEN** each gets its own distinct anchor row

#### Scenario: Pre-existing dashboard-created rows are unaffected

- **WHEN** a conversation was created via `conversation_create` (the
  dashboard-only path, `source_thread_identity IS NULL`)
- **THEN** it is never matched or overwritten by
  `conversation_get_or_create_by_thread`, and the partial unique index on
  `(butler_name, source_channel, source_thread_identity)` (which only governs
  rows where `source_thread_identity IS NOT NULL`) never conflicts with it

### Requirement: Provider Resume Ledger

Each conversation SHALL carry at most one provider-native resume handle
("one memory per thread") that a runtime adapter capable of resuming a prior
session (see `RuntimeAdapter.supports_resume`) can use to continue that
conversation's provider-side session state instead of cold-starting.

#### Scenario: Recording a provider session handle

- **WHEN** `conversation_set_provider_session` is called with a
  `provider_session_id` and `provider_runtime_type` for a conversation
- **THEN** those values are stored along with a fresh
  `provider_session_updated_at = now()`
- **AND** a later call for the same conversation overwrites the prior handle
  entirely — only the most recent provider session is ever resumable

#### Scenario: Resolving a usable resume handle

- **WHEN** `resolve_resume_handle` is evaluated for a conversation's stored
  provider session against a target `runtime_type`
- **THEN** it returns the handle only if one is present, it was minted by
  that same `runtime_type`, and `provider_session_updated_at` is within the
  TTL window (24 hours) of the evaluation time
- **AND** it returns `None` in every other case (absent, runtime-type
  mismatch, or expired) — callers MUST treat `None` as an ordinary cold
  start, never as an error

#### Scenario: Evicting a stale or rejected handle

- **WHEN** `conversation_clear_provider_session` is called for a conversation
  (e.g. because a resume attempt was rejected by the provider as expired or
  unknown)
- **THEN** `provider_session_id`, `provider_runtime_type`, and
  `provider_session_updated_at` are all cleared to `NULL`
- **AND** the next call to `resolve_resume_handle` for that conversation
  returns `None`, so the next turn cold-starts cleanly

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
  - `source_thread_identity` (TEXT, nullable): the channel-normalized thread identity (mirrors `message_inbox.request_context ->> 'source_thread_identity'`); NULL for dashboard-created rows, which are already anchored 1:1 on `id`
  - `provider_session_id` (TEXT, nullable): the most recent provider-native session/resume handle minted for this conversation
  - `provider_runtime_type` (TEXT, nullable): which runtime adapter type minted `provider_session_id` — a handle is only resumable by the same adapter type
  - `provider_session_updated_at` (TIMESTAMPTZ, nullable): when `provider_session_id` was last refreshed; governs TTL-based resume eligibility

#### Scenario: Conversation table indexes

- **WHEN** the migration creates indexes
- **THEN** a composite index on `(butler_name, status, updated_at DESC)` SHALL exist for listing active conversations per butler
- **AND** a composite index on `(butler_name, updated_at DESC)` SHALL exist for chronological listing
- **AND** a unique index on `(butler_name, source_channel, source_thread_identity)` WHERE `source_thread_identity IS NOT NULL` SHALL exist so concurrent ingress for the same thread converges on one anchor row

#### Scenario: Sticky routed_butler stamping

- **WHEN** a classification-routed (Switchboard-addressed) conversation's message is submitted and Switchboard's triage produces a `route_to` decision with a target butler, and the conversation has no `routed_butler` yet
- **THEN** `routed_butler` is set to that target butler
- **AND** a later `route_to` decision for the same conversation (e.g. from a follow-up that still goes through classification) does NOT overwrite an already-set `routed_butler` — the first successful route wins

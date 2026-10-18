## MODIFIED Requirements

### Requirement: Readonly Contextualization Role
The user client connector SHALL give butlers passive awareness of the user's Telegram activity. It SHALL be strictly ingestion-only and readonly. Ingested messages SHALL reach goal admission through the Switchboard with no classification or routing to specialist butlers.

Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Readonly access to user's Telegram
- **WHEN** the Telegram user client connector runs
- **THEN** it ingests messages visible to the user's personal Telegram account (not the butler bot)
- **AND** it never sends messages, replies, reacts, edits, deletes, or modifies anything on the user's account
- **AND** all ingested messages flow through the Switchboard to goal admission for contextual awareness, with no classification or routing to specialist butlers

#### Scenario: Contextualization use cases
- **WHEN** the user's Telegram messages are ingested
- **THEN** butlers can automatically learn about: travel plans mentioned in group chats, restaurant recommendations from friends, health appointments shared in DMs, financial discussions, relationship events, and any other life context visible in Telegram
- **AND** this eliminates the need for the user to manually forward messages or tell the butler about events already visible in their Telegram

#### Scenario: Separation from bot connector
- **WHEN** both Telegram connectors run simultaneously
- **THEN** the bot connector handles user↔bot interactive messaging (inbound + outbound via Messenger)
- **AND** the user client connector provides passive, readonly ingestion of the user's broader Telegram activity
- **AND** each has a distinct `endpoint_identity` (e.g., `"telegram:bot:mybot"` vs `"telegram:user:123456"`)
- **AND** each uses a distinct `source.channel` (`"telegram"` for bot, `"telegram_user_client"` for user client) so ingestion policy and goal admission can trivially distinguish the two flows

#### Scenario: Relationship to TelegramContactsProvider
- **WHEN** the Contacts module is configured with a Telegram provider
- **THEN** the `TelegramContactsProvider` uses the same Telethon credentials (`telegram_api_id`, `telegram_api_hash`, `telegram_user_session`) as this connector
- **AND** the contacts provider syncs the user's Telegram address book (contact list), while this connector ingests message streams
- **AND** both operate independently — the contacts provider runs periodic sync via the Contacts module's polling loop, while this connector maintains a persistent live session

### Requirement: Privacy, Consent, and Data Minimization
Because this connector reads a user's personal Telegram messages, it SHALL enforce strict privacy safeguards. Setup SHALL disclose that ingested messages flow through Switchboard ingestion and goal admission, not classification and routing.

Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Explicit user consent
- **WHEN** the owner configures a Telegram user session in Passport
- **THEN** setup clearly discloses that the connector ingests new messages visible to the account in direct messages, groups, supergroups, and channels, with no current per-chat or per-sender exclusions
- **AND** setup explains that ingested messages flow through the normal Switchboard ingestion and goal-admission pipeline and that any historical replay is limited to the separately configured optional backfill
- **AND** the owner MUST explicitly acknowledge that account-wide scope before the setup flow can send an OTP request
- **AND** the API MUST reject a missing or false acknowledgement, even if a client bypasses the disabled setup control
- **AND** successful verification MUST persist a versioned non-secret consent grant in the connector control plane before persisting the owner credentials
- **AND** the connector MUST reject a missing, malformed, or superseded grant before creating a Telegram client or starting ingestion

#### Scenario: Missing or invalid consent remains visibly disabled
- **WHEN** the connector cannot validate the current account-wide ingestion consent grant
- **THEN** it MUST remain running in a non-ingesting `error` health state with the synthetic identity `telegram:user:pending-consent` and an actionable acknowledgement-required message
- **AND** it MUST NOT resolve Telegram user credentials, create a Telegram client, emit connector heartbeats, or submit ingestion events
- **AND** it MUST periodically recheck the control-plane consent grant and continue normal startup only after a valid versioned grant is present
- **AND** a transient shared-control database connectivity failure before the grant can be read MUST retain that disabled state and retry on the same cadence
- **AND** a non-connectivity shared-control-pool failure MUST surface instead of being retried indefinitely

#### Scenario: [TARGET-STATE] Scope controls
- **WHEN** the user client connector is configured
- **THEN** optional chat/sender allow/deny lists are available to limit ingestion scope
- **AND** per-chat filtering (allowlist/denylist), per-sender filtering, and message type filtering (e.g., exclude media, only text) are supported

#### Scenario: [TARGET-STATE] Content redaction
- **WHEN** sensitive content patterns are detected
- **THEN** optional redaction rules can filter sensitive messages before ingest submission

#### Scenario: [TARGET-STATE] Audit trail
- **WHEN** the connector's lifecycle changes (start, stop, config changes)
- **THEN** all events are recorded in an audit trail

#### Scenario: Ingestion-only — no outbound
- **WHEN** the user client connector processes messages
- **THEN** it is strictly ingestion-only — it never sends messages, replies, or performs any write action on the user's Telegram account
- **AND** outbound messaging goes through the Telegram Bot connector and Messenger butler

### Requirement: Conversation History Payload Type Tag
The connector SHALL tag batch envelopes with `control.payload_type = "conversation_history"` so the Switchboard can recognise a conversation-history batch (for example, to keep its observability content-blind). No Switchboard step SHALL decompose a tagged batch into per-butler messages; the batch reaches goal admission as one accepted envelope.

Source: RFC 0038 D1; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Payload type tag on batch envelope
- **WHEN** a chat buffer is flushed and the ingest.v1 envelope is assembled
- **THEN** the envelope's `control` section includes `payload_type: "conversation_history"`

#### Scenario: Payload type tag does not affect single-message mode
- **WHEN** the connector operates in single-message mode (if applicable for future use)
- **THEN** the `payload_type` field is not set

#### Scenario: Tagged batch is not decomposed
- **WHEN** the Switchboard accepts an envelope tagged `payload_type: "conversation_history"`
- **THEN** it hands the envelope to goal admission as one accepted envelope
- **AND** no conversation decomposition, signal-extraction fan-out or per-butler routing runs

### Requirement: Connector-level participant gating for interaction eligibility
The connector SHALL gate interaction-eligible processing for chats exceeding a configurable participant threshold. A gated envelope MAY still reach goal admission; no participant count SHALL route it to a butler.

Source: RFC 0038 D1; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Large group gating threshold
- **WHEN** a chat has `participant_count` exceeding `max_interaction_group_size` (default: 20)
- **THEN** the connector MUST set `control.interaction_eligible = false` in the envelope
- **AND** the envelope MAY still be submitted for goal admission

#### Scenario: Batch envelopes for large groups
- **WHEN** a batch conversation-history envelope is built for a chat exceeding the threshold
- **THEN** the connector MAY skip submission entirely or submit with `policy_tier = "metadata_only"`

#### Scenario: Gating telemetry
- **WHEN** the connector gates a message due to participant count
- **THEN** it SHOULD emit an OTel counter `butlers.telegram_user_client.interaction_gated` with low-cardinality attributes `{chat_type, participant_count_bucket}` where `participant_count_bucket` is one of `"21-50"`, `"51-200"`, `"201-1000"`, `"1000+"`

#### Scenario: Below-threshold chats are unaffected
- **WHEN** a chat has `participant_count` at or below `max_interaction_group_size`
- **THEN** `control.interaction_eligible` MUST default to `true` (or be omitted)
- **AND** the envelope MUST be submitted normally

### Requirement: Bearer material never persists

Connectors and the Switchboard ingest boundary SHALL withhold bearer material (one-time codes,
password-reset links, magic links, and Telegram login codes) from every persisted payload. Only the
fact that an auth artifact arrived SHALL be kept, as a typed placeholder recording the artifact kind,
the provider domain, and the observation time. Scrubbing SHALL NOT change the message's
ingestion-policy decision or its goal admission.

Source: RFC 0038 D1; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: One-time code is replaced by a placeholder
- **WHEN** an `ingest.v1` envelope whose text contains a one-time code (for example "Your code is 482913") reaches the ingest boundary
- **THEN** `message_inbox.raw_payload` and `message_inbox.normalized_text` SHALL NOT contain the code
- **AND** they SHALL contain a placeholder of the form `[auth-code withheld: <provider-domain>]`
- **AND** `raw_payload.control.bearer_scrubbed` SHALL be `true` and `raw_payload.bearer_artifacts` SHALL list `{kind, provider_domain, observed_at}` records
- **AND** scrubbing SHALL NOT change the message's ingestion-policy decision or its goal admission

#### Scenario: Ordinary numbers survive
- **WHEN** a message contains order numbers, dates, or amounts that are not adjacent to code wording and not part of a reset or magic link
- **THEN** the text SHALL be persisted unchanged

#### Scenario: Scrubbing is idempotent
- **WHEN** already-scrubbed text is scrubbed again
- **THEN** no further change SHALL occur

#### Scenario: Detector failure fails closed
- **WHEN** the scrubber raises while processing an envelope
- **THEN** the ingest boundary SHALL persist the message as metadata-only with `payload.raw` null and SHALL NOT persist the unscrubbed text

#### Scenario: Telegram login codes from the service account
- **WHEN** a message arrives from the Telegram service sender `777000`
- **THEN** every code-shaped number in the envelope text and raw payload SHALL be withheld before submit

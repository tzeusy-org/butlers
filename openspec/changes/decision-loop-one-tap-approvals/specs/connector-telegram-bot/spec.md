# Telegram Bot Connector — Delta

## MODIFIED Requirements

### Requirement: Update Type Handling
The connector SHALL process a defined subset of Telegram update types.

#### Scenario: Processed update types
- **WHEN** a Telegram update arrives
- **THEN** the connector processes: `message`, `edited_message`, `channel_post`,
  and `callback_query` (approval-decision callbacks only; see Approval Callback
  Ingestion)

#### Scenario: Skipped update types
- **WHEN** a non-message update other than `callback_query` arrives
  (`inline_query`, `chosen_inline_result`, etc.)
- **THEN** it is silently skipped

#### Scenario: Non-approval callback queries are acknowledged and dropped
- **WHEN** a `callback_query` arrives whose data does not parse as an approval
  callback token
- **THEN** the callback is answered generically (no error toast loop) and no
  further processing occurs

#### Scenario: Gap-interview one-tap callback routed
- **WHEN** a `callback_query` arrives whose `callback_data` begins with the
  `cgi:` prefix (`cgi:<interview_id>:<answer>`)
- **THEN** the connector SHALL route it to the chronicler gap-interview resolve
  path (`POST /api/chronicler/gap-interview/resolve`) instead of dropping it,
  and SHALL acknowledge the tap via `answerCallbackQuery` with an owner-facing
  toast
- **AND** this routing SHALL be strictly additive — any `callback_query` whose
  `callback_data` does not carry the `cgi:` prefix retains its existing drop
  behavior
- **AND** when the internal API URL is unconfigured or unreachable, the tap
  SHALL still be acknowledged with a graceful toast rather than left with a
  loading spinner
## ADDED Requirements

### Requirement: Approval Callback Ingestion

The connector SHALL handle `callback_query` updates carrying approval callback
tokens (`apr1:<action_id>:<verb_char>:<hmac>`, with single-character verb codes
such as `a` for approve and `r` for reject) as deterministic control-plane
events, not as `ingest.v1` messages: it MUST (1) verify the tapping user's chat
resolves to a verified owner channel via identity resolution, (2) validate the
token's HMAC signature and map the verb code to the pending-action decision,
(3) answer the callback query promptly, and (4) deliver the decision to the
approvals decision surface with actor identity
`human:owner@telegram`, which performs the status transition, audit logging,
and dispatch via the standard executor. No LLM session is involved at any step.
Failed verification MUST NOT mutate any state.

#### Scenario: Owner tap approves the action

- **WHEN** a `callback_query` with a valid Approve token arrives from a chat
  that resolves to a verified owner channel
- **THEN** the callback is acknowledged, the decision is delivered to the
  approvals decision surface with actor `human:owner@telegram`, and the pending
  action transitions and dispatches through the standard approve path
- **AND** the decision is audit-logged with the telegram actor identity

#### Scenario: Non-owner tap is ignored

- **WHEN** a `callback_query` with a syntactically valid token arrives from a
  chat that does not resolve to a verified owner channel
- **THEN** the callback is answered generically, the event is logged, and no
  state changes occur

#### Scenario: Invalid or tampered token

- **WHEN** a `callback_query` arrives whose token fails HMAC validation
- **THEN** the callback is answered generically, the failure is logged, and no
  state changes occur

#### Scenario: Expired or already-decided action

- **WHEN** a valid owner tap references an action that is no longer `pending`
- **THEN** the callback is answered with an "already handled" notice and the
  decision surface performs no transition

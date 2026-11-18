# Telegram Bot Connector — Delta

## MODIFIED Requirements

### Requirement: Update Type Handling
The connector SHALL process a defined subset of Telegram update types. It SHALL
also recognize one strictly additive `callback_query` exception: a
`callback_data` carrying the `cgi:` gap-interview prefix SHALL be routed to the
chronicler gap-interview resolve path instead of being dropped, while every
other `callback_query` SHALL retain its existing drop behavior unless it carries
an approval callback token (see Approval Callback Ingestion).

ID: REQ-connector-telegram-bot-006
Source: RFC-0021,RFC-0023; bu-jv1moa.2 S1; design.md D1
Scope: v1-mandatory

#### Scenario: Processed update types
- **WHEN** a Telegram update arrives
- **THEN** the connector processes: `message`, `edited_message`, `channel_post`,
  and `callback_query` (approval-decision callbacks only; see Approval Callback
  Ingestion)

#### Scenario: Skipped update types
- **WHEN** a non-message update arrives (`callback_query`, `inline_query`, `chosen_inline_result`, etc.)
- **THEN** it is silently skipped — no ingest submission — UNLESS it is a
  `callback_query` whose `callback_data` carries the `cgi:` gap-interview
  prefix, which is handled per the gap-interview scenario, or an approval
  callback token, which is handled per Approval Callback Ingestion

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

#### Scenario: Owner /reach lists and revokes break-through clauses
- **WHEN** a message whose text is exactly `/reach` arrives in a private chat
  (`chat.id` equals `from.id`) from the primary owner
- **THEN** the connector SHALL answer deterministically, without ingesting the
  message or invoking a model, with the unrevoked break-through clauses and one
  inline button per clause whose `callback_data` is a signed `rch1:` token
  bound to the chat
- **AND** a tap on an `rch1:` button from the primary owner SHALL revoke that
  clause via `POST /api/attention/reach/telegram` and SHALL always be
  acknowledged via `answerCallbackQuery`, including when the owner lookup fails
- **AND** the surface SHALL be narrowing-only: no Telegram path creates or
  grants a clause
- **AND** `/reach` from a non-owner or from a group chat SHALL fall through
  unanswered as an ordinary message

#### Scenario: Owner /reach add records a pending proposal only
- **WHEN** a message `/reach add <matcher>` arrives in a private chat from the
  primary owner, where the matcher words are `domain:`/a bare domain,
  `channel:`, `kind:` and `scope:` drawn from the closed clause sets
- **THEN** the connector SHALL NOT ingest the message or invoke a model, SHALL
  forward only the parsed matchers (never message text) to
  `POST /api/attention/reach/telegram`, and SHALL reply "Proposed; confirm on
  the dashboard at /settings/reach" with no confirm button
- **AND** an unparseable command (no matcher, an unknown or repeated field, an
  out-of-set value, free text) SHALL be answered with usage and SHALL propose
  nothing
- **AND** from a non-owner, a non-primary sender or a group chat the command
  SHALL fall through as an ordinary message
- **AND** no Telegram path SHALL create a clause


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

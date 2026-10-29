## MODIFIED Requirements

### Requirement: Lifecycle Reaction Emoji Support

The module SHALL support setting Telegram reactions for ingest pipeline lifecycle events. A lifecycle reaction is visible to the chat's other party, so the module SHALL set it only when the chat resolves uniquely to the owner under the `core-notify` owner-resolution rule; in any other chat, including an unknown or ambiguous one, it SHALL skip the reaction rather than send it or park it for approval.

Source: RFC 0038 D4 and owner decision 9; `REQ-capability-authorization-016`
Scope: v1-mandatory

#### Scenario: React for ingest events

- **WHEN** `react_for_ingest` is called with `external_thread_id` (format: `<chat_id>:<message_id>`) and a reaction key for a chat that resolves uniquely to the owner
- **THEN** the reaction emoji is mapped via `REACTION_TO_EMOJI`:
  - `:eye` -> eyes emoji (in-progress)
  - `:thumbsup` -> thumbs-up emoji (success)
  - `:space invader` -> alien emoji (failure)
- **AND** a `setMessageReaction` API call is made

#### Scenario: Unparseable thread identity

- **WHEN** `react_for_ingest` is called with `None` or an unparseable `external_thread_id`
- **THEN** the call is a silent no-op

#### Scenario: Reaction API failure

- **WHEN** the Telegram API rejects a reaction (e.g., unsupported chat type)
- **THEN** processing continues and a debug log is emitted (non-fatal)

#### Scenario: No lifecycle reaction in a non-owner chat

- **WHEN** `react_for_ingest` is called for a chat that does not resolve uniquely to the owner
- **THEN** no `setMessageReaction` call is made and no approval is requested

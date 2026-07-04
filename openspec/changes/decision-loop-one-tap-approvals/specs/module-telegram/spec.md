# Telegram Module — Delta

## REMOVED Requirements

### Requirement: [TARGET-STATE] Inline Approval Buttons

**Reason**: Promoted from target-state to concrete requirements by the
decision-loop change (RFC 0021); replaced by "Inline Keyboard Support" and
"Inline Approval Buttons" below.

## ADDED Requirements

### Requirement: Inline Keyboard Support

The module's send and reply tools SHALL support an optional `reply_markup`
inline keyboard payload (buttons with `text` and `callback_data`), passed
through to the Telegram Bot API. The module SHALL also support editing a
previously sent message's text and removing its inline keyboard
(`editMessageText` / `editMessageReplyMarkup`), addressed by
`chat_id` + `message_id`.

#### Scenario: Send with inline keyboard

- **WHEN** `telegram_send_message` is invoked with `chat_id`, `text`, and a
  `reply_markup` inline keyboard
- **THEN** the `sendMessage` API call includes the `reply_markup` payload and
  the delivered message renders the buttons

#### Scenario: Edit message and remove keyboard

- **WHEN** the message-edit helper is invoked with `chat_id`, `message_id`,
  new text, and keyboard removal
- **THEN** the message text is updated and the inline keyboard is removed

#### Scenario: Callback data length is enforced

- **WHEN** a button's `callback_data` exceeds Telegram's 64-byte limit
- **THEN** the tool returns a structured validation error before any API call

### Requirement: Inline Approval Buttons

The module SHALL support sending an approval-request message with an inline
keyboard so the owner can approve or reject a pending action directly from the
notification. Buttons carry single-purpose signed callback tokens bound to the
pending action (format `apr1:<action_id>:<verb_char>:<hmac>` using
single-character verb codes such as `a` for approve and `r` for reject); button
presses resolve to the corresponding pending-action verb in the approvals system
via the bot connector's callback ingestion. An "Open" button deep-links the
dashboard action detail for edit-then-approve.

#### Scenario: Approval message carries inline buttons

- **WHEN** the Messenger delivers an approval-request notification to the owner
  over telegram
- **THEN** the message includes Approve and Reject inline buttons whose
  `callback_data` are signed tokens bound to the pending action's id, plus an
  Open deep link to the dashboard action detail

#### Scenario: Button press resolves the pending action

- **WHEN** the owner taps Approve on the inline keyboard
- **THEN** the corresponding pending action transitions to `approved` and
  executes via the standard approved-action executor
- **AND** the originating message is edited to reflect the resolved state with
  the keyboard removed

#### Scenario: Tap on an already-decided action

- **WHEN** the owner taps a button for an action that is already decided or
  expired
- **THEN** the callback is answered with a non-destructive "already handled"
  notice, the message is edited to the action's current state, and no state
  transition occurs

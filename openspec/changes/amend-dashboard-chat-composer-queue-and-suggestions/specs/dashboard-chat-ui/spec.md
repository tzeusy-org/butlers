## MODIFIED Requirements

### Requirement: Message Input Area

The message input area SHALL occupy the bottom of the chat panel with a text input and send controls, including cancellation of an active response. The textarea SHALL remain editable in every chat posture regardless of conversation loading, ownership resolution, or active-turn state; those states SHALL gate only Send availability. Clauses elsewhere in this capability that re-enable "the input" after a turn ends SHALL be read as restoring Send availability, because the textarea itself is never disabled by turn state.

ID: REQ-dashboard-chat-ui-006
Source: heart-and-soul/vision.md § What Success Looks Like; dashboard-design-language § Button Forms; bu-0ynlk.14 scope correction; design.md Decisions 1, 2, and 8
Scope: v1-mandatory

#### Scenario: Text input

- **WHEN** the chat panel is active with a conversation (or ready to start a new one)
- **THEN** a textarea renders at the bottom of the panel with placeholder "Type a message..."
- **AND** the textarea auto-grows with content up to a bounded height and scrolls internally beyond that
- **AND** pressing `Enter` sends the message (without Shift)
- **AND** pressing `Shift+Enter` inserts a newline

#### Scenario: Send button

- **WHEN** the textarea has non-empty content
- **THEN** a send button renders at the right edge of the input area
- **AND** clicking the button sends the message and clears the input

#### Scenario: Input stays editable during an active turn

- **WHEN** an assistant response is pending or streaming in the active conversation
- **THEN** the textarea remains enabled, focusable, and editable, and keeps focus if it already had it
- **AND** Send and Stop render as separate adjacent controls, with Stop calling the server-side cancel endpoint (see Stream cancellation below) rather than only detaching the client's own stream watch
- **AND** Stop retains its existing pending ("Stopping…"), unavailable, and status-announcement behavior
#### Scenario: Input disabled during streaming

- **WHEN** an assistant response is currently streaming
- **THEN** neither the textarea nor Send is disabled; the former disabled-input behavior under this scenario name is superseded by "Input stays editable during an active turn"
- **AND** Send during the active turn queues under Requirement: Single-Slot Queued Turn instead of starting a parallel turn

#### Scenario: Loading or unresolved ownership gates only Send

- **WHEN** the conversation list, conversation detail, or conversation ownership is still loading or unresolved
- **THEN** the owner can type in the textarea without delay
- **AND** Send is unavailable until the gating state resolves, with the reason exposed to assistive technology
- **AND** no typed text is cleared or sent when the gate resolves

#### Scenario: Composer text follows the conversation identity

- **WHEN** the owner switches the active conversation within one mounted chat posture while unsent text is present
- **THEN** the composer shows only the unsent text belonging to the newly selected `{butler, conversation | new}` identity
- **AND** returning to the previous conversation within the same posture instance shows its unsent text unchanged
- **AND** text is never copied or carried from one conversation identity to another

#### Scenario: Starting a new conversation from empty state

- **WHEN** no conversation is selected and the user types a message and sends it
- **THEN** a new conversation is created via `POST /api/butlers/{name}/conversations` with the message
- **AND** the new conversation appears in the conversation list and is selected

### Requirement: Typing Indicator

A pending-activity indicator SHALL provide truthful visual feedback while the butler is processing a response, using the Process Status Pill vocabulary (one `StateDot` plus a mono status label) and no animation.

ID: REQ-dashboard-chat-ui-007
Source: dashboard-design-language § Process Status Pill, Status Indicators, and Motion Vocabulary; design.md Decision 7
Scope: v1-mandatory

#### Scenario: Typing indicator during processing

- **WHEN** a user message has been sent and the assistant response has not started streaming
- **THEN** a status pill renders at the bottom of the message thread, left-aligned (assistant position), containing one `StateDot` in the `degraded` state and the current truthful phase or receipt label
- **AND** the pill text is the same text exposed by the existing polite activity status region, so visual and announced state never disagree
- **AND** no element in the indicator animates, bounces, pulses, or uses a staggered animation delay
- **AND** the pill is not interactive; Stop remains the only control for the active turn
- **AND** this scenario supersedes the former three-bouncing-dots indicator

#### Scenario: Typing indicator during streaming

- **WHEN** the assistant response is actively streaming tokens
- **THEN** the typing indicator is replaced by the growing assistant message content

#### Scenario: Pending indicator is suppressed after Stop settles

- **WHEN** Stop is pending, confirmed, or failed for the active turn
- **THEN** the pending pill does not claim the turn is still progressing normally
- **AND** the existing Stop status and inline cancel outcome remain the authoritative state

## ADDED Requirements

### Requirement: Single-Slot Queued Turn

The chat composer SHALL let the owner queue exactly one follow-up message while a turn is active in the same conversation identity. A queued item SHALL hold the trimmed text, one immutable client `message_id`, and the context-chip inclusion choice and context value captured when it was queued. The queued item SHALL be visible, editable through one explicit action, and SHALL never start a parallel server turn.

ID: REQ-dashboard-chat-ui-008
Source: heart-and-soul/vision.md § What Success Looks Like; dashboard-conversations immutable message-id semantics; design.md Decisions 1, 3, and 4
Scope: v1-mandatory

#### Scenario: Enter during an active turn queues one message

- **WHEN** a turn is active for identity `{butler, conversation | new}`, no item is queued, and the owner presses `Enter` or activates Send with non-empty text
- **THEN** the trimmed text, a newly allocated immutable `message_id`, and the current context-chip choice and value are stored as the single queued item for that identity
- **AND** the editor clears and stays focused for further drafting
- **AND** "1 message queued." is announced politely
- **AND** no create or send request is issued while the prior turn is active

#### Scenario: Queued item is visible with its context

- **WHEN** an item is queued
- **THEN** a queued row renders above the textarea showing a mono "Queued" label, one line of the queued text, the context label it will attach (or that context is not attached), and an `Edit` pill action
- **AND** the row is reachable and operable by keyboard and meets the coarse-pointer target floor

#### Scenario: A second Enter cannot replace the queued item

- **WHEN** an item is already queued and the owner presses `Enter` with more text
- **THEN** the queued item is neither replaced nor sent
- **AND** the additional text remains in the editor as ordinary unsent text
- **AND** a visible helper line, also announced politely, states that one message is already queued and sends after the current reply

#### Scenario: Edit returns the queued text without loss

- **WHEN** the owner activates `Edit` on the queued row
- **THEN** the queued item is removed and its text is placed in the editor, before any existing editor text and separated from it by a blank line
- **AND** the caret is placed at the end of the returned text and the context snapshot and `message_id` are discarded
- **AND** no text is sent or deleted

#### Scenario: New-conversation queue follows the created conversation

- **WHEN** an item is queued under a `new` identity and `conversation_created` establishes conversation C for the active turn
- **THEN** the queued item moves to identity `{butler, C}` unchanged
- **AND** no queued item remains under the `new` identity

### Requirement: Queued Turn Dispatch and Demotion

A queued item SHALL be dispatched automatically exactly once, and only after the prior turn in the same identity produces a committed `message_complete`. Every other outcome SHALL demote the queued item: its text returns to that identity's composer as ordinary unsent text, using the placement rule of the Edit action, and it is never sent automatically.

ID: REQ-dashboard-chat-ui-009
Source: dashboard-chat-ui § SSE Client Integration; durable-dashboard-terminal-action-recovery no-replay semantics; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Committed reply releases the queued item once

- **WHEN** the prior turn emits a committed `message_complete` while an item is queued for the same identity
- **THEN** the queued item is dispatched once through the shared turn path using its stored `message_id` and captured context
- **AND** the queued row is removed when dispatch starts
- **AND** a duplicate or replayed `message_complete` does not dispatch it again

#### Scenario: Unconfirmed or interrupted outcomes demote the queued item

- **WHEN** the prior turn ends with `done` but no `message_complete`, a stream error, `INGEST_IN_PROGRESS`, `TURN_OUTCOME_UNKNOWN`, `SESSION_CANCELLED`, or an interrupted watch
- **THEN** the queued item is demoted and its text returns to the composer for that identity
- **AND** "Queued message returned to the composer, not sent." is announced politely
- **AND** no request is issued for the demoted `message_id`

#### Scenario: Stop demotes the queued item immediately

- **WHEN** the owner activates Stop while an item is queued
- **THEN** the queued item is demoted at once, whatever the later cancel outcome
- **AND** a later `message_complete` for the stopped turn does not dispatch the demoted text

#### Scenario: Dispatch failure uses existing retry without replay

- **WHEN** a dispatched queued item fails to send
- **THEN** the existing classified send-error and retry behavior applies, reusing that item's `message_id`
- **AND** the text is not also restored into the editor as a second copy
- **AND** no automatic retry or second queue submission occurs

#### Scenario: Leaving the identity demotes rather than sends

- **WHEN** the owner switches conversation, the chat posture unmounts, or the page unloads while an item is queued
- **THEN** the queued item is demoted into the unsent text of its own identity, not the identity being opened
- **AND** returning to that identity later shows the text as unsent and never sends it automatically

### Requirement: Queued Text Draft Retention

When browser-local chat drafts are available under REQ-dashboard-chat-ui-004 and the shared browser-draft contract, the text of a queued item SHALL be retained in the same identity's draft record while it is queued, with the same origin scope, sliding expiry, size limit, and discard semantics. After a reload it SHALL restore as editable unsent text and SHALL NOT be dispatched automatically. The auto-send intent, the queued `message_id`, and the captured context SHALL NOT be persisted. This requirement is conditional on the owner decision recorded in this change's design (Option A).

ID: REQ-dashboard-chat-ui-010
Source: persist-safe-dashboard-unsent-drafts (owner-adopted 2026-09-12); heart-and-soul/vision.md § What Success Looks Like; design.md § Owner decision
Scope: v1-mandatory

#### Scenario: Queued text survives reload as an unsent draft

- **WHEN** an item is queued and the page reloads before the item is dispatched
- **THEN** reopening the same identity within the draft lifetime shows the queued text as editable unsent text marked "Not sent"
- **AND** the restore is announced through the shared quiet draft-restored status
- **AND** nothing is sent until the owner sends it

#### Scenario: Dispatch hands the text to the adopted submission contract

- **WHEN** a queued item is dispatched
- **THEN** its text and `message_id` are bound as a submitted content revision under REQ-dashboard-chat-ui-005 before the request is issued
- **AND** the queued-text portion of the draft record is cleared only under that requirement's proven-acceptance rules

#### Scenario: Unavailable storage keeps the queue working

- **WHEN** browser draft storage is denied, full, blocked, or unavailable while an item is queued
- **THEN** the queue, Edit, Stop, and dispatch behave as specified without delay
- **AND** the composer discloses that the queued text will not survive a reload

### Requirement: Route-Aware Starting Prompts

The chat surfaces SHALL offer reviewed, static starting prompts only on the `/spend`, `/qa`, and `/entities/:entityId` route families, computed solely from a closed projection containing the route pattern, the registered visible-resource kind, and a coarse window class. Activating a prompt SHALL fill and focus the composer and SHALL NOT send, change lane, attach hidden context, or overwrite text.

ID: REQ-dashboard-chat-ui-011
Source: heart-and-soul/security.md (page context is the one path into prompts); dashboard-design-language § Button Forms and Interface Copy; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Prompts render only for an empty thread and empty composer

- **WHEN** the owner is on a supported route family and the active thread has no messages and the composer is empty
- **THEN** at most three prompts render as pill buttons in a list named "Suggested questions"
- **AND** they disappear as soon as the composer contains text or the thread has a message

#### Scenario: Unsupported routes show no prompts

- **WHEN** the owner is on any route outside `/spend`, `/qa`, and `/entities/:entityId`
- **THEN** no starting prompts render and the empty thread shows only its empty-state copy

#### Scenario: Activation fills without sending

- **WHEN** the owner activates a prompt by pointer, `Enter`, or `Space`
- **THEN** the composer receives exactly that prompt text and focus moves to the end of it
- **AND** no request is sent, the context chip and its inclusion choice are unchanged, and no lane is selected

#### Scenario: Prompt inputs exclude private and page data

- **WHEN** prompts are computed for a route
- **THEN** the computation receives only the route pattern, the registered visible-resource kind or null, and a window class of today, week, month, custom, or null
- **AND** it receives no raw id, label, entity reference, query parameter, visible summary, cached query row, or third-party content
- **AND** no prompt is generated by a model call

#### Scenario: Prompts claim no unavailable capability

- **WHEN** a prompt is displayed
- **THEN** it is a question answerable by the question lane on main
- **AND** it does not offer to perform an action until action proposals in chat are governed by an adopted contract

### Requirement: Lane-Teaching Empty Thread

An empty chat thread SHALL render a page-level empty state that names the four dashboard lanes truthfully in one sentence, without an action button, and SHALL NOT render when conversation history is unavailable.

ID: REQ-dashboard-chat-ui-012
Source: dashboard-design-language § Interface Copy (page-level empty state); design-language.md § Empty states; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Empty thread names the lanes

- **WHEN** the active conversation has no messages and history loaded successfully
- **THEN** the thread shows the title "No messages yet." and the sentence "Ask a question, correct a record, request a change for approval, or report a bug."
- **AND** no action button, illustration, or tour overlay renders
- **AND** the copy uses no exclamation mark, em-dash, first person, or future tense

#### Scenario: Unavailable history does not look empty

- **WHEN** the active conversation's history failed to load or is retained behind a read-recovery alert
- **THEN** neither the empty-state copy nor starting prompts render

### Requirement: Chat Trigger and Popover Chrome

Below the `xl` breakpoint the global chat trigger SHALL be a hairline mono pill button, and the popover SHALL use the page surface with a hairline border, consistent with the Dispatch language. Unread state SHALL be carried by text plus at most one `StateDot`.

ID: REQ-dashboard-chat-ui-013
Source: dashboard-design-language § Button Forms, Status Indicators, Anti-Pattern Prohibitions, and Viewport and Modality Contract; design.md Decision 7
Scope: v1-mandatory

#### Scenario: Trigger renders as a hairline pill

- **WHEN** the popover trigger renders below `xl`
- **THEN** it is a pill-form button with a 1px border, mono label "Chat", no background fill, and no shadow
- **AND** its hit area meets the 44×44px coarse-pointer floor without enlarging the visual pill

#### Scenario: Unread reply uses text and one state dot

- **WHEN** an unread reply exists for the global chat surface
- **THEN** the trigger label reads "Chat · new reply" and exactly one `StateDot` in the `ok` state renders inside the pill
- **AND** no red badge, count bubble, or `error` state is used
- **AND** the existing accessible name change and shell unread announcement are preserved

#### Scenario: Popover has no card chrome

- **WHEN** the popover is open
- **THEN** it uses the page background with a 1px hairline border and no drop shadow or card fill
- **AND** existing focus containment, dismissal, and focus-return behavior are unchanged

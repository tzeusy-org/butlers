## ADDED Requirements

### Requirement: Browser-Local Chat Composer Drafts

The dashboard chat UI SHALL preserve non-empty unsent message text for both the butler-detail chat panel and the floating chat widget under the shared browser-draft contract. Draft identity SHALL follow the target butler and exact conversation, using a distinct new-conversation identity, so component unmounts and surface changes do not discard or misroute the owner's words.

ID: REQ-dashboard-chat-ui-004
Source: heart-and-soul/vision.md § What Success Looks Like; design.md Decisions 1, 3, and 5
Scope: v1-mandatory

#### Scenario: Existing-conversation draft survives unmount

- **WHEN** the owner types a non-empty message for conversation C, then closes, unmounts, navigates away from, or reloads the chat surface before sending
- **THEN** reopening conversation C within the draft lifetime restores the same message text
- **AND** the restored state is announced once through a quiet accessible status
- **AND** a visible one-click Discard action removes only conversation C's draft

#### Scenario: New-conversation draft remains distinct

- **WHEN** the owner types before a conversation has been created
- **THEN** the draft is stored under the target butler's new-conversation identity
- **AND** it is not shown in any existing conversation
- **AND** a new-conversation draft for another butler remains isolated

#### Scenario: Conversation switches restore only the selected draft

- **WHEN** the owner switches between conversations while either has unsent text
- **THEN** each conversation shows only the draft stored for its own butler and conversation identity
- **AND** switching does not clear, merge, or copy text between identities

#### Scenario: Matching chat surfaces share one draft

- **WHEN** the butler-detail chat panel and floating chat widget address the same butler and conversation identity
- **THEN** both surfaces address the same stored draft
- **AND** moving between them does not create component-specific duplicates

#### Scenario: Page context remains current at send time

- **WHEN** a floating-chat draft is restored after the owner changes routes
- **THEN** only the unsent message text is restored
- **AND** the current visible context chip and its current include-or-exclude choice govern the eventual send
- **AND** stale route context is not persisted inside the draft record

### Requirement: Chat Draft Submission and Read-Recovery Outcomes

The chat composer SHALL allocate and persist the existing immutable client `message_id` together with the exact submitted draft revision before issuing a send. It SHALL clear only that submitted revision after the durable conversation read model proves acceptance of the same `message_id`. Validation failure or proven rejection before durable acceptance SHALL retain the editable draft; transport interruption or any outcome that cannot be classified from durable evidence SHALL retain the bound attempt as unknown without text matching or automatic resend.

ID: REQ-dashboard-chat-ui-005
Source: dashboard-chat-ui § Message Input Area; durable-dashboard-terminal-action-recovery REQ-dashboard-chat-ui-002 and REQ-dashboard-chat-ui-003; design.md Decisions 4 and 10
Scope: v1-mandatory

#### Scenario: Send binds the existing immutable message identity

- **WHEN** the owner submits revision R from chat draft K
- **THEN** the client reuses the existing message-ID generator and transactionally records `message_id` M with K and R before sending
- **AND** retry and reconciliation of that attempt reuse M rather than creating a second identity
- **AND** no message-content comparison is used as identity

#### Scenario: Durable send acceptance clears the submitted revision

- **WHEN** the durable read model proves that message ID M from draft K revision R was accepted
- **THEN** one transaction tombstones M's attempt snapshot and clears the composer and stored record for K only if their authoritative revision remains R
- **AND** drafts under every other key remain unchanged
- **AND** assistant processing or streaming may continue under the existing conversation contract

#### Scenario: Late chat acceptance retains newer text

- **WHEN** acceptance of message ID M bound to revision R is proven after the same tab or another tab accepted a newer revision for K
- **THEN** the newer revision remains visible and stored
- **AND** the same transaction replaces M's completed attempt with a content-free tombstone without clearing the newer text

#### Scenario: Proven pre-acceptance failure retains editable text

- **WHEN** local validation or durable server evidence proves that message ID M was rejected before acceptance
- **THEN** the exact message text remains editable in the composer and retained under its draft key
- **AND** the existing classified failure UI remains visible
- **AND** closing and reopening the same composer can restore the retained text

#### Scenario: Unknown send outcome does not advertise a safe resend

- **WHEN** transport interruption, timeout, reload, or missing durable evidence leaves message ID M's acceptance unknown
- **THEN** the text and M-to-submitted-revision binding remain retained until an applicable durable read resolves the outcome or the owner discards it
- **AND** the UI identifies the outcome as unknown and does not automatically resend
- **AND** any retry action remains governed by the existing exact-message recovery contract

#### Scenario: Loaded known-conversation attempt reconciles after reload

- **WHEN** a reloaded draft retains message ID M and the bounded loaded durable read for known conversation C contains M
- **THEN** the client reconciles only the exact M message and dashboard-turn projection
- **AND** accepted or completed evidence conditionally clears the submitted revision, while pending evidence retains it as pending
- **AND** retryable, rejected, cancelled, or ambiguous evidence retains the text with the corresponding existing recovery state
- **AND** absence or read failure remains unknown and never triggers automatic replay

#### Scenario: Observed new-conversation creation retires only the submitted revision

- **WHEN** the existing `conversation_created` SSE event binds submitted message ID M to conversation C
- **THEN** one browser-storage transaction records C against M and treats that exact message as durably accepted
- **AND** it tombstones M's attempt snapshot and the `new` draft only when the draft still has M's submitted revision
- **AND** it neither copies the submitted text into C as an unsent draft nor clears a newer `new` revision

#### Scenario: Unavailable exact-message read remains honestly blocked

- **WHEN** the dashboard reloads after missing a new conversation's `conversation_created` event, or M is absent from a known conversation's bounded loaded messages
- **THEN** the retained message ID and text render as an outcome-unknown attempt rather than apparently unsent text
- **AND** the dashboard does not search by text, scan unbounded conversation history, call a mutating recovery endpoint as a read, or resend automatically
- **AND** automatic reconciliation remains blocked until an approved content-blind read can resolve butler plus message ID to conversation ID and durable turn outcome

#### Scenario: History read recovery preserves the active draft

- **WHEN** the active conversation history fails or refreshes while retaining same-thread messages
- **THEN** the draft selected by the active conversation key remains available beside the existing retryable history alert
- **AND** changing to another loading or failed conversation selects that conversation's draft rather than the previous thread's text
- **AND** draft recovery does not change cached-message, optimistic-message, SSE, or terminal-action ownership

#### Scenario: Empty text leaves no stored draft

- **WHEN** an eligible chat composer becomes empty or contains only whitespace
- **THEN** its stored draft record is removed
- **AND** reopening the composer shows no restored-draft affordance

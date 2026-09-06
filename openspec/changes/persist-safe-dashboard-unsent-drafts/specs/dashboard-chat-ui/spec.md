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

The chat composer SHALL clear a stored draft only after the dashboard has evidence that the exact owner message was durably accepted. Validation failure, transport failure, rejection before durable acceptance, or an unknown send outcome SHALL retain the draft without presenting an unsafe automatic resend, while the existing durable conversation read model remains authoritative for message and terminal-action state.

ID: REQ-dashboard-chat-ui-005
Source: dashboard-chat-ui § Message Input Area; durable-dashboard-terminal-action-recovery REQ-dashboard-chat-ui-002 and REQ-dashboard-chat-ui-003; design.md Decisions 4 and 7
Scope: v1-mandatory

#### Scenario: Durable send acceptance clears the exact draft

- **WHEN** the chat send path proves durable acceptance of message M from draft key K
- **THEN** the composer and stored record for K are cleared
- **AND** drafts under every other key remain unchanged
- **AND** assistant processing or streaming may continue under the existing conversation contract

#### Scenario: Send failure retains editable text

- **WHEN** validation, transport, or application failure occurs before durable acceptance can be proved
- **THEN** the exact message text remains editable in the composer and retained under its draft key
- **AND** the existing classified failure UI remains visible
- **AND** closing and reopening the same composer can restore the retained text

#### Scenario: Unknown send outcome does not advertise a safe resend

- **WHEN** the dashboard cannot prove whether the exact message was durably accepted
- **THEN** the text remains retained until the existing durable-status path resolves the outcome or the owner discards it
- **AND** the UI identifies the outcome as unknown and does not automatically resend
- **AND** any retry action remains governed by the existing exact-message recovery contract

#### Scenario: History read recovery preserves the active draft

- **WHEN** the active conversation history fails or refreshes while retaining same-thread messages
- **THEN** the draft selected by the active conversation key remains available beside the existing retryable history alert
- **AND** changing to another loading or failed conversation selects that conversation's draft rather than the previous thread's text
- **AND** draft recovery does not change cached-message, optimistic-message, SSE, or terminal-action ownership

#### Scenario: Empty text leaves no stored draft

- **WHEN** an eligible chat composer becomes empty or contains only whitespace
- **THEN** its stored draft record is removed
- **AND** reopening the composer shows no restored-draft affordance

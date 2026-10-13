## ADDED Requirements

### Requirement: Domain-Owned Chat Action Cards

The chat thread SHALL render trusted domain-owned proposal cards and current durable receipts according to chat-domain-actions, using the existing approval mutation spine and Dispatch interaction primitives. Cards MUST distinguish approval from execution, use server-authorized controls, and preserve keyboard/focus and irreversible-confirmation requirements. Concierge and the browser MUST NOT own a write registry or execute a command directly.

ID: REQ-dashboard-chat-ui-006
Source: chat-domain-actions REQ-chat-domain-actions-007 through 009; dashboard-design-language; dashboard-approvals Approval Verbs
Scope: v1-mandatory

#### Scenario: Approval acceptance is not execution success

- **WHEN** Confirm commits approval but its owner has not executed, or execution becomes ambiguous
- **THEN** the card renders queued or ambiguous evidence rather than a success/completed claim
- **AND** it exposes neither a fabricated Undo nor an unsafe Retry.

#### Scenario: Supported reversal is separately proposed

- **WHEN** the owner supports an existing inverse or compensation command
- **THEN** the card presents the correctly named new proposal and its normal confirmation flow
- **AND** irreversible/unknown support has no Undo and requires typed confirmation of the current reviewed impact.

## Source References

- Non-Negotiable Rules 3, 4 and 6; heart-and-soul/security.md Approval Gates.
- RFC 0023 (durable approval-delivery recovery); RFC 0030 (system-plane read exception).
- chat-domain-actions and this changeset design.md.

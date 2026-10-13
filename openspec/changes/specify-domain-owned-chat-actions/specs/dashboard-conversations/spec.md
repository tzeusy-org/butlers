## ADDED Requirements

### Requirement: Domain-Owned Chat Action Projection

Conversation message reads SHALL carry nullable server-projected `action_proposals` for trusted stored domain-owner proposals. The projection, after-commit `action_proposed`/`action_receipt` stream events, history replay and authoritative refresh MUST obey chat-domain-actions; they MUST NOT mutate the owning action. The existing message, citation, turn-control, channel-anchor and provider-resume contracts SHALL remain binding. The owner SHALL establish its immutable protected admission/source/action/review binding before projection; a refused, missing or incomplete projection MUST NOT remove that designation or allow approval/dispatch as an ordinary action.

ID: REQ-dashboard-conversations-015
Source: chat-domain-actions REQ-chat-domain-actions-002 through 006; dashboard-conversations Conversation Reply Channel and SSE Response Streaming
Scope: v1-mandatory

#### Scenario: Action projection survives history replay

- **WHEN** a stored trusted proposal's stream event is lost and conversation history is reloaded
- **THEN** the same proposal UUID/revision is returned once with its server-derived owner and durable evidence
- **AND** the read path makes zero approval decisions or domain handler invocations.

#### Scenario: Legacy and active conversation model contracts remain compatible

- **WHEN** a legacy message without a trusted proposal is read
- **THEN** action_proposals is null and existing message fields retain their contracts
- **AND** no action is inferred from text, citations or tool logs; existing anchor/resume and durable turn recovery behavior is preserved.

## Source References

- Non-Negotiable Rules 3, 4 and 6; heart-and-soul/security.md Approval Gates.
- RFC 0023 (durable approval-delivery recovery); RFC 0030 (system-plane read exception).
- chat-domain-actions and this changeset design.md.

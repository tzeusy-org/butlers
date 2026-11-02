## Purpose

This capability specifies the future D19 dashboard goal presentation owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: Goal voice retains canonical briefing doctrine

Goal, Region, Overview and approval copy SHALL retain the existing ban on first-person pronouns and future-tense claims. It SHALL describe observed facts, pending decisions and unknown outcomes without predicting receipt or delivery. No D19 exception to the briefing voice lint or canonical design-language doctrine is authorized.

ID: REQ-dashboard-goal-presentation-001
Source: dashboard-briefing Voice Lint; about/heart-and-soul/design-language.md; RFC 0038 D19
Scope: post-v1

#### Scenario: Prepared effect with an unknown prior delivery

- **WHEN** a goal has a parked prepared message and a prior unknown delivery
- **THEN** its copy says that the message is ready for review and automatic retry is unavailable, without claiming completion, first-person authorship or a future receipt.


### Requirement: Approval fixture decisions have one coherent state

The synthetic approval mockup SHALL allow at most one local decision per artifact load. Approve, Reject and Defer SHALL become mutually unavailable after a selection, and every displayed state for that same effect SHALL change coherently. Approval SHALL remain distinct from unconfirmed dispatch/delivery; defer SHALL remain pending. No local interaction shall call a product endpoint or simulate a server-authorized production receipt.

ID: REQ-dashboard-goal-presentation-002
Source: dashboard-approvals decision/execution contracts; RFC 0038 D11/D13/D19; design.md
Scope: post-v1

#### Scenario: Approve once

- **WHEN** the mockup's parked exact call is locally approved
- **THEN** all three incompatible decision buttons are disabled and the queue and effect fields show Approved with dispatch unconfirmed.

#### Scenario: Reject once

- **WHEN** the mockup's parked exact call is locally rejected
- **THEN** all incompatible decisions are disabled and both state fields show Rejected with readable lineage.

#### Scenario: Defer once

- **WHEN** the mockup's parked exact call is locally deferred
- **THEN** all incompatible decisions are disabled and both state fields remain Pending with deferred presentation, without a delivery claim.

#### Scenario: A repeated or incompatible local decision

- **WHEN** a second local decision is attempted for that artifact load
- **THEN** the first decision and its displayed state remain unchanged and no product request occurs.

## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: dashboard-briefing Voice Lint; about/heart-and-soul/design-language.md; RFC 0038 D19
- Requirement-level source: dashboard-approvals decision/execution contracts; RFC 0038 D11/D13/D19; design.md

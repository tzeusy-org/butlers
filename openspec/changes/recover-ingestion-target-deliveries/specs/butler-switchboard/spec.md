## ADDED Requirements

### Requirement: Receipted reasoning ingress and legacy route drain

In ISOLATED_CANDIDATE and SWITCHED_RELEASE, Switchboard SHALL submit each accepted envelope that needs reasoning to goal admission exactly once, keyed by its durable source key, and SHALL return that admission's durable receipt to every duplicate submission, buffer restart and recovery request. It SHALL NOT classify, decompose, fan out or route the envelope to a target butler. Deterministic ingestion SHALL stay on the service plane and receive no admission receipt. In KEY_ABSENT_STAGE, the frozen classify-and-route lane SHALL keep its legacy target receipts readable and SHALL gain no per-target delivery outbox, classifier or retry worker. Before the switch, Switchboard SHALL stop accepting new routes, let in-flight legacy route work drain, and record unresolved legacy work as visible residue that is never re-routed.

ID: REQ-butler-switchboard-003
Source: RFC 0038 D1, D5 and Hard cutover; generalist-lead-focused-delegation Ingest Envelopes Feed Goal Admission; design.md Decisions 1 and 4
Scope: v1-mandatory

#### Scenario: A duplicate envelope returns the original receipt

- **WHEN** in ISOLATED_CANDIDATE or SWITCHED_RELEASE, a connector resubmits an envelope whose source key already has an admission decision
- **THEN** Switchboard SHALL return the original admission receipt
- **AND** no second goal SHALL be admitted and no classifier or route dispatch SHALL run

#### Scenario: A batch envelope is one goal

- **WHEN** in ISOLATED_CANDIDATE or SWITCHED_RELEASE, a conversation-history batch that mentions several domains is accepted
- **THEN** Switchboard SHALL admit one goal with one admission receipt for the batch
- **AND** it SHALL NOT create per-target segments, decomposition output or delivery intents

#### Scenario: Deterministic ingestion creates no admission

- **WHEN** an envelope is withheld by a deterministic ingestion rule or bound to a domain's deterministic channel ingest
- **THEN** it SHALL follow its service-plane path and create neither a goal nor an admission receipt

#### Scenario: Legacy routes drain before the switch

- **WHEN** the switch is prepared while legacy route-inbox work is in flight
- **THEN** Switchboard SHALL stop accepting new routes and let in-flight legacy work finish
- **AND** legacy work it cannot prove accepted SHALL remain visible as content-blind residue and SHALL NOT be re-routed or admitted except through owner-gated historical admission

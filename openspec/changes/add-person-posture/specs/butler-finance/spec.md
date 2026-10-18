## ADDED Requirements

### Requirement: Memorial counterparty loan-claim question

Finance's cost-claim reconciliation sweep MUST, after reconciling, open one owner condition (source `finance:memorial-claim`, identity from the claim's `asserted_by` and `claim_key`, which survive an amendment that supersedes the row) for each live, unsettled `receivable` or `payable` claim whose counterparty entity has posture `memorial`, and MUST propose exactly one owner insight asking whether to retract or keep the claim, only on that condition's `opened` transition. The sweep MUST NOT retract, supersede, settle, write off or otherwise change the claim or its resolution because of posture, MUST NOT ask for `active`, `quiet` or `no_contact` counterparties, and MUST resolve the condition when the claim closes or the counterparty's posture is anything other than `memorial`. The notice MUST name the person and the loan direction and MUST NOT carry an amount or description. When the posture or claim read fails, the sweep MUST reconcile nothing and propose nothing.

ID: REQ-butler-finance-002
Source: add-person-posture proposal §Deferred; bu-q7vx1q.45
Scope: v1-mandatory

#### Scenario: A memorial counterparty's open loan is asked about once

- **WHEN** a live `receivable` claim has a `memorial` counterparty and the sweep runs twice
- **THEN** exactly one `finance:memorial-claim` condition and one candidate SHALL exist
- **AND** the claim row SHALL be unchanged

#### Scenario: Other postures are never asked

- **WHEN** the counterparty posture is `active`, `quiet` or `no_contact`
- **THEN** no condition and no candidate SHALL be created

#### Scenario: An unreadable claim set changes nothing

- **WHEN** the claim and posture read raises
- **THEN** no condition SHALL be reconciled or resolved and no candidate SHALL be proposed

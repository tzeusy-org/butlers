# Finance Cost Claims Specification

## Purpose

Define the shared, typed money-claim projection and Finance reconciliation
contract without weakening per-butler schema isolation.

## Requirements

### Requirement: Claims are typed, currency-explicit projections

The system SHALL store shared claims in typed columns. Currency SHALL be an
explicit uppercase three-letter code with no default. The asserting butler's
own domain record SHALL remain the source of truth.

#### Scenario: Relationship asserts a loan

- **WHEN** Relationship creates a valid loan with an explicit currency
- **THEN** it SHALL retain the canonical loan fact and assert one live claim
  keyed `loan:{fact_id}`
- **AND** Finance SHALL read the claim without any grant on the Relationship schema

#### Scenario: Legacy currency is absent

- **WHEN** a legacy loan fact has no currency
- **THEN** every read SHALL return null for currency
- **AND** backfill SHALL skip and count it rather than infer a denomination

### Requirement: Database roles split write authority

The public claim tables SHALL use enabled and forced row-level security keyed on
the active runtime role. Assertion fields SHALL be immutable after insertion.

#### Scenario: Assertion and verdict roles differ

- **WHEN** `butler_relationship_rw` inserts a Relationship claim
- **THEN** the insert SHALL succeed
- **AND** a forged `asserted_by` or another role's update SHALL fail
- **AND** only `butler_finance_rw` SHALL write a resolution
- **AND** assertion SHALL NOT create a resolution before Finance evaluates it
- **AND** no runtime role SHALL delete claims, resolutions, or their event history

#### Scenario: Bootstrap is replayed

- **WHEN** the privileged bootstrap reapplies broad public-table grants
- **THEN** forced RLS SHALL preserve the same effective write boundary

#### Scenario: The ledger is backed up and restored

- **WHEN** the documented backup and certified restore path runs
- **THEN** claims, resolutions, and events SHALL round-trip without loss
- **AND** FORCE RLS, table ownership, and SECURITY DEFINER ownership SHALL match the source
- **AND** ordinary `pg_dump` SHALL remain in fail-loud row-security-off mode

### Requirement: Loan settlement is atomic

Superseding the active loan fact and inserting its settled successor SHALL run
in one transaction.

#### Scenario: Successor storage fails

- **WHEN** successor fact storage raises after the supersession statement
- **THEN** the transaction SHALL roll back
- **AND** the original loan SHALL remain active on tool and API reads

### Requirement: Reconciliation never fabricates visibility

Finance SHALL reconcile claims deterministically under an advisory lock and
SHALL distinguish settled, ambiguous, unreconciled, and unverifiable outcomes.

#### Scenario: No Finance account exists

- **WHEN** a claim currency has no active Finance account
- **THEN** its verdict SHALL be `unverifiable` with reason `no_account`
- **AND** it SHALL NOT be reported as unreconciled

#### Scenario: Fresh coverage has no candidate

- **WHEN** Finance has a fresh feed for the currency but no candidate in the window
- **THEN** its verdict SHALL be `unreconciled/no_candidate_in_window`

#### Scenario: Candidate cannot be chosen safely

- **WHEN** candidates are multiple, cross-currency, or already bound
- **THEN** Finance SHALL record an ambiguous verdict and SHALL NOT guess or convert currency

#### Scenario: Exactly one matching transaction exists

- **WHEN** exactly one eligible same-currency transaction matches
- **THEN** Finance SHALL bind it to the claim and record settled or partially settled
- **AND** the transaction SHALL NOT bind to a second claim

#### Scenario: Previously matched evidence changes

- **WHEN** a bound transaction ceases to match its claim
- **THEN** Finance SHALL release the stale binding before evaluating active claims
- **AND** a newly matching claim MAY bind that transaction in the same sweep

### Requirement: Public APIs project the same claim truth

`GET /api/finance/cost-claims` SHALL return claims with their latest resolution.
`GET /api/relationship/entities/{id}/loans` SHALL preserve null legacy currency
and include the matching claim and resolution when present.

#### Scenario: Both domains project one loan claim

- **WHEN** a Relationship loan has a live cost claim
- **THEN** the Relationship loan endpoint SHALL include its claim verdict
- **AND** the Finance claims endpoint SHALL expose the same claim and verdict

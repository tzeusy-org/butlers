## ADDED Requirements

### Requirement: Candidate Cooldown Is Not the Only Standing-State Record

Owner condition ledger reconciliation (`owner-condition-ledger` capability) SHALL NOT change `insight_candidates`' cooldown/dedup/verbosity/budget
semantics, which remain the sole delivery-gating mechanism defined elsewhere
in this specification. A producer MAY additionally reconcile a category it
submits candidates for into the owner condition ledger as a state side
effect alongside candidate submission.

#### Scenario: Owner condition reconciliation does not alter candidate delivery

- **WHEN** a producer reconciles a category into the owner condition ledger
  on the same scheduled run it submits an insight candidate for that
  category
- **THEN** the candidate's dedup key, cooldown, expiry, and priority
  evaluation proceed exactly as they would without the reconciliation call
- **AND** a reconciliation failure never blocks, delays, or alters candidate
  submission for that run

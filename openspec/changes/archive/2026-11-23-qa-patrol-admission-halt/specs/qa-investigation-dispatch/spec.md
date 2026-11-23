## ADDED Requirements

### Requirement: Patrol-Level Admission Halt

An open QA circuit breaker SHALL halt the rest of a patrol's dispatch as one decision. The batch
dispatcher SHALL read the breaker before each launch, and the halt SHALL run before and take
precedence over the per-finding gates. It SHALL hold every remaining finding for a later patrol,
record one dispatch event for the patrol, and claim or delete no attempt.

ID: REQ-qa-investigation-dispatch-014
Source: bu-ejow5p.3 (run 25 move 3), slice S1; owner decision on bu-ejow5p.3 adopting the bead design
Scope: v1-mandatory

#### Scenario: An open breaker holds the whole patrol

- **WHEN** the breaker is open when a patrol dispatches its novel findings
- **THEN** every finding is stamped `dedup_reason = circuit_breaker` and `dispatch_queued = TRUE` in one write
- **AND** exactly one `healing_dispatch_events` row with `decision = circuit_breaker` is recorded for the patrol
- **AND** no `healing_attempts` row is inserted, claimed or deleted
- **AND** each finding's dispatch result carries reason `circuit_breaker`

#### Scenario: A breaker that trips mid-patrol holds the rest

- **WHEN** the breaker trips after the first k findings of a patrol were dispatched
- **THEN** findings k+1 through n are all held and queued behind one dispatch event
- **AND** none of them is dropped or claimed

#### Scenario: A failed hold is not reported as held

- **WHEN** the write that holds the findings fails
- **THEN** the `hold_failed` dispatch decision is counted
- **AND** the findings' dispatch results carry reason `hold_failed`, not `circuit_breaker` or `concurrency_cap`
- **AND** no dispatch event claims the findings were held

#### Scenario: A reset between the read and the hold costs one patrol

- **WHEN** an owner reset closes the breaker after it was read open and before the hold is written
- **THEN** the findings stay held and queued
- **AND** the next patrol admits them, so the worst case is a one-patrol delay

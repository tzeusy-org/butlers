## ADDED Requirements

### Requirement: Held Findings Are Queued For The Next Patrol

A novel finding that a halting dispatch gate (open circuit breaker or full concurrency cap) kept
from investigation SHALL be recorded with that gate as its authoritative `dedup_reason` and queued
with `dispatch_queued = TRUE`, so the next patrol reloads it through triage. The concurrency cap
SHALL queue every leftover finding, including the first finding over the cap.

ID: REQ-qa-triage-006
Source: bu-ejow5p.3 (run 25 move 3), slice S1; owner decision on bu-ejow5p.3 adopting the bead design
Scope: v1-mandatory

#### Scenario: Every finding over the cap is queued

- **WHEN** the concurrency cap is reached after k investigations of a patrol with n novel findings
- **THEN** findings k+1 through n each have `dedup_reason = concurrency_cap` and `dispatch_queued = TRUE`
- **AND** the first of them is queued exactly like the rest

#### Scenario: Breaker-held findings resurface after the breaker closes

- **WHEN** findings were held behind an open breaker and the breaker later closes
- **THEN** `get_dispatch_queued_findings()` returns them for re-triage

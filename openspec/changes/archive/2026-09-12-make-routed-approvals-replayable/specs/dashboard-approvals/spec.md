## ADDED Requirements

### Requirement: Retry Reports Dispatch Failure Class Truthfully

Approval Retry endpoints MUST distinguish failure to reach the owning butler from a
reachable executor or tool rejection. Neither failure class may be presented as
successful execution, and safe actionable detail MUST be returned for a reachable
rejection.

#### Scenario: Owning butler is unreachable

- **WHEN** Retry cannot establish a dispatch path to the owning butler
- **THEN** the API returns an unavailable response identifying that no owning butler is reachable

#### Scenario: Reachable executor rejects stored action

- **WHEN** Retry reaches the owning butler and its executor or native handler rejects the stored action
- **THEN** the API returns a failure response identifying an executor or tool rejection
- **AND** the response includes bounded safe detail suitable for operator diagnosis
- **AND** it MUST NOT claim that no butler was reachable

#### Scenario: Retry execution fails

- **WHEN** either Retry endpoint receives a dispatch failure
- **THEN** the action remains `approved` with `execution_result = null`

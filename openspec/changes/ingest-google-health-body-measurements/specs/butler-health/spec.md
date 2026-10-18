# butler-health

## ADDED Requirements

### Requirement: Owner-logged measurements dominate device readings

`measurement_latest`, `measurement_history` and `GET /measurements/latest` SHALL let an owner-logged value win over a device-reported value on the same owner-local calendar day. Device rows SHALL stay stored with their `metadata.source` and SHALL NOT be deleted.

ID: REQ-butler-health-003
Source: bu-q7vx1q.35; [Observed] `roster/health/tools/owner_dominance.py`
Scope: v1-mandatory

#### Scenario: Latest value on a day with an owner log

- **WHEN** `measurement_latest` or `GET /measurements/latest` finds a device row (a stamped `metadata.source` other than `owner_log`) as the newest row and the owner logged the same type on the same owner-local day
- **THEN** it SHALL return the owner's latest value for that day, even if the device reading is later in the day

#### Scenario: History on a day with an owner log

- **WHEN** `measurement_history` returns rows for a type
- **THEN** it SHALL omit device rows whose owner-local day contains an owner-logged row of that type
- **AND** it SHALL keep device rows on days without an owner log

#### Scenario: Device-only types are readable

- **WHEN** `measurement_history` or `measurement_latest` is called with `body_fat`
- **THEN** it SHALL read `measurement_body_fat` facts
- **AND** `measurement_log` SHALL continue to accept only its existing manual types

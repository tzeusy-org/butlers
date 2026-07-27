## ADDED Requirements

### Requirement: Dashboard Terminal-Action QA MCP Contract

The QA staffer SHALL preserve the existing ordinary `report_finding` contract
and add a dashboard-specific durable mode. An authenticated internal
Switchboard relay MAY invoke dashboard mode only when it supplies all of
`terminal_action_id` (UUID), `terminal_effect_id` (UUID), and
`terminal_effect_idempotency_key` (opaque stable string) in addition to the
ordinary finding arguments. QA SHALL reject a partial dashboard identity or a
caller that is not the internal Switchboard relay. In dashboard mode, QA SHALL
durably upsert one receipt and canonical finding before returning
`{"accepted": true, "delivery": "dashboard_durable", "receipt": {"terminal_action_id":
"...", "terminal_effect_id": "...", "finding_id": "...", "created_at": "..."}}`.
The durable receipt store SHALL have a uniqueness boundary on
`(terminal_action_id, terminal_effect_id)` and retain the idempotency key. A
non-dashboard call SHALL continue to queue the finding in `butler_reports` and
return the existing `{"accepted": true}` volatile acknowledgement; it SHALL
not be forced into the dashboard receipt path.

ID: REQ-staffer-qa-001
Source: staffer-qa § QA module MCP tool registration; RFC 0015 § V1 sources; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-002; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Dashboard report is accepted

- **WHEN** an authenticated Switchboard relay calls `report_finding` with all
  three dashboard identity fields
- **THEN** QA SHALL durably upsert one receipt and canonical finding record for
  that action/effect identity before it reports the dashboard effect accepted
- **AND** the finding SHALL remain available to the normal QA discovery/triage
  path after a QA daemon restart

#### Scenario: Dashboard report is retried

- **WHEN** the same dashboard action/effect IDs and idempotency key are delivered
  again
- **THEN** QA SHALL return the same `dashboard_durable` receipt payload without
  creating a second report record or second buffered finding

#### Scenario: Ordinary report retains the existing volatile behavior

- **WHEN** a normal butler relay calls `report_finding` without dashboard
  identity fields
- **THEN** QA SHALL retain the existing volatile `butler_reports` queue behavior
  and `{"accepted": true}` response

#### Scenario: Caller supplies an invalid dashboard identity

- **WHEN** a caller supplies only part of the dashboard identity, a mismatched
  duplicate idempotency key, or lacks the internal Switchboard principal
- **THEN** QA SHALL reject the request without creating a receipt, finding, or
  buffered report

### Requirement: Dashboard Report Receipt Lookup

The QA staffer SHALL register an authenticated internal MCP tool named
`get_dashboard_report_receipt`. Only the internal Switchboard relay may invoke
it with `{terminal_action_id: UUID, terminal_effect_id: UUID}`. It SHALL return
exactly either `{"status": "found", "receipt": {"terminal_action_id": "...",
"terminal_effect_id": "...", "finding_id": "...", "created_at": "..."}}` or
`{"status": "not_found"}`. The result SHALL query the durable receipt store;
it SHALL NOT infer a receipt from the volatile `butler_reports` buffer.

ID: REQ-staffer-qa-002
Source: staffer-qa § QA module MCP tool registration; RFC 0015 § V1 sources; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-003; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Switchboard reconciles a possibly delivered report

- **WHEN** the terminal-action reconciler needs to determine whether a
  dashboard QA-report effect completed after a crash
- **THEN** it SHALL call `get_dashboard_report_receipt` with the stable action
  and child-effect identities
- **AND** QA SHALL return only the specified `found` receipt shape or the
  specified `not_found` shape

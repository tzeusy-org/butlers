## MODIFIED Requirements

### Requirement: Read-Only Tool Surface

Concierge SHALL expose only read-only MCP tools. No tool in the `dashboard_read`
module (or any other module Concierge loads) may perform an INSERT, UPDATE,
or DELETE against any table, or trigger a mutation on another butler
(no Operator-style fleet controls).
- Chat action presentation SHALL NOT extend Concierge's authority. Its staffer roster, infrastructure contract and actual registered tool surface MUST remain read-only; it MUST NOT register a domain action proposal/decision/execution tool or act as an alternate domain command owner.

ID: REQ-butler-concierge-001
Source: butler-concierge Read-Only Tool Surface; RFC 0030; chat-domain-actions REQ-chat-domain-actions-001
Scope: v1-mandatory

#### Scenario: No write tools registered

- **WHEN** Concierge's MCP tool surface is enumerated
- **THEN** every tool in the `dashboard_read` module is a pure read (SELECT
  only) against `concierge.v_fleet_sessions`, `concierge.v_fleet_spend`, or
  `public.insight_candidates`
- **AND** no tool accepts a mutation-shaped argument (create/update/delete/
  trigger) for any resource outside Concierge's own core session log

#### Scenario: Chat does not register write authority on Concierge

- **WHEN** domain-owned chat actions are enabled or their projections are read
- **THEN** Concierge has no action proposal/decision/execution tool or writable domain registry
- **AND** its MANIFESTO.md, staffer-typed butler.toml and module registrations preserve the existing system-plane read boundaries
- **AND** `cross_butler_access = ["*"]` does not grant domain action authority.

## Source References

- Non-Negotiable Rules 3, 4 and 6; heart-and-soul/security.md Approval Gates.
- RFC 0023 (durable approval-delivery recovery), RFC 0030 (system-plane read exception).
- chat-domain-actions and this changeset design.md.

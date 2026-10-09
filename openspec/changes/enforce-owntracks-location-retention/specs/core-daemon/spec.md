# Core Daemon

## MODIFIED Requirements

### Requirement: Delegation Core Tool Inventory And Admission Boundary

The core-tool inventory SHALL reserve a delegation group for non-staffer
butlers. Its original delegation-tool inventory remains delegate_ask, delegate_receive, delegate_answer,
and delegate_wake.

delegate_wake SHALL remain a server-to-server return endpoint, not a
user-delivery or free-form peer control surface, and its admission boundary is
enforced by ledger re-verification rather than by the caller channel. In
normal operation delegate_wake is reached only through the trusted Switchboard
route path, but the framework has no LLM-hidden-but-registered tool tier
(known framework limitation): delegate_wake is necessarily registered the same
as its delegation-group siblings, as an ordinary LLM-visible MCP tool, so no
admission-layer signal distinguishes a Switchboard-routed call from a direct
same-butler invocation. delegate_wake SHALL independently re-verify, on every
invocation regardless of how it was reached, that the ledger row exists, is
answered, carries the exact immutable wake_key, and names the calling butler
as its authoritative asking_butler, before creating or reconciling any work —
and SHALL write only to the calling butler's own schema. Registration alone
SHALL not grant a domain butler authority to create work in a sibling schema.

The group SHALL additionally register the infrastructure endpoints
location_retention_prepare_questions and location_retention_question_status,
with non-presentable representation metadata. They SHALL accept stored-plan
and receipt locators only, select their actual fixed owning constructor, obtain
source plans through the adopted Switchboard route, and require exact current
source-generation/body/manifest and committed owning lifetime/disposal evidence.
A source locator, header or principal string SHALL NOT authorize erasure or
receiving admission. Their registration SHALL not widen a role, grant peer
private SQL, change effective groups or activate a new runtime configuration.

ID: REQ-core-daemon-004
Source: bu-s11n0s.7 P5 registered owning receiver disposal; existing Delegation Core Tool Inventory And Admission Boundary
Scope: v1-mandatory

The group SHALL additionally register location_retention_question_owner_plan, location_retention_prepare_question_loan and location_retention_close_owned_questions in the same non-presentable delegation_control representation. These stored-state locators SHALL retain the actual owning constructor, adopted Switchboard route, full immutable original parent/body/loan/manifest binding and separate terminal readback. The source-question status reader SHALL dispatch a non-Chronicle owner only through its actual registered writer identity, not a caller name or fabricated runtime. Registration SHALL NOT infer cross-schema permission, actor authority, receiving incarnation or full source completion.

#### Scenario: Non-staffer delegation inventory is explicit

- **WHEN** a butler-type daemon has the delegation core group enabled by its
  effective runtime configuration
- **THEN** its MCP inventory SHALL include the four delegation tools
- **AND** delegate_wake SHALL reject any invocation whose ledger_id, wake_key,
  or asking_butler do not match an answered ledger row's immutable wake
  identity, independent of whether the call arrived via the Switchboard route
  or directly

#### Scenario: Staffers do not gain delegation tools

- **WHEN** a staffer daemon starts, including when its effective groups contain
  delegation
- **THEN** none of delegate_ask, delegate_receive, delegate_answer, or
  delegate_wake SHALL be registered
- **AND** it SHALL not gain a route around the non-staffer delegation boundary

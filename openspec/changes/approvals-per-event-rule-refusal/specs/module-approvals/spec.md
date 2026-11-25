## ADDED Requirements

### Requirement: Per-Event Tools Never Become Standing Rules

A tool whose approval mode is `always_per_event` (`REQ-capability-authorization-017`) SHALL be a
decision of its own on every call. A standing rule SHALL NOT be created for it: direct creation,
creation from a pending action, approval with a rule, the suggestion preview and the confirmation of a promotion suggestion (including one recorded before this refusal existed) SHALL each refuse
with the typed reason `rule_refused_always_per_event` (HTTP 409 on the dashboard), through the
dashboard routes and the MCP tools alike. The refusal SHALL come before a step-up factor is consumed
and before any rule or `rule_created` event is written, and an approval refused for this reason SHALL
leave the action pending. An ordinary approval of such an action SHALL still complete, SHALL record no
promotion history and SHALL create no promotion suggestion. The approval detail SHALL expose
`rule_eligible: false` for it so the dashboard offers no "always allow" prompt. The mode SHALL be
resolved from the checked-in per-event tool set, which a contract test SHALL keep equal to the
`always_per_event` declarations of every module.

ID: REQ-module-approvals-011
Source: bu-5ii4gj.4.1 (B2 of bu-5ii4gj.4); RFC 0038 D13; bu-p5umi2.4 = A
Scope: v1-mandatory

#### Scenario: A rule for an RSVP is refused

- **WHEN** an owner approves a parked `calendar_respond` with a standing rule, or asks to create a rule for it
- **THEN** the call is refused with `rule_refused_always_per_event`, no `approval_rules` row and no `rule_created` event exist, and the action stays pending

#### Scenario: A legacy promotion suggestion cannot mint a rule

- **WHEN** a pending promotion suggestion for `calendar_respond` is confirmed through the MCP tool or the dashboard
- **THEN** it is refused with `rule_refused_always_per_event` (HTTP 409 on the dashboard), no rule is created and the suggestion stays pending

#### Scenario: An RSVP approval is not promotion evidence

- **WHEN** an owner approves a parked `calendar_respond` without a rule
- **THEN** the action is approved, no approval history row is recorded and no promotion suggestion appears

#### Scenario: An ordinary tool is unaffected

- **WHEN** an owner creates a rule for a tool that is not per-event
- **THEN** the rule is created and its approvals still count toward promotion

#### Scenario: A forgotten declaration fails CI

- **WHEN** a module declares a new `always_per_event` tool that the checked-in set lacks
- **THEN** the contract test fails

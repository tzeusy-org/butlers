Goal-runtime applicability: RFC 0038 D1-D3, D7 and D18; the explicit re-scope
in generalist-lead-focused-delegation/collision-dispositions.md and task 4.2.
The retained requirement/scenario names are stable across the switch. Target
placement is specified below; historical receipts are not target proof.

## ADDED Requirements

### Requirement: Authoritative Codex CLI-Auth Restore at Runtime Startup

The trusted goal runner SHALL construct and retain an explicit system-global
`cli-auth/codex` authority before Codex CLI-auth restore. Every restore that can
populate the session host's Codex runtime home SHALL
use that authority, rather than its schema-local credential namespace or an
implicit fallback pool. The startup path SHALL preserve the current ordering in
which Codex CLI-auth restore completes before a Codex runtime is invoked. This
requirement preserves other-provider authority resolution for applicable D18
deterministic callers; it does not retain domain reasoning launches or provider
credential custody.

After the goal switch, domain daemons SHALL NOT restore provider credentials
for reasoning sessions or launch those sessions. The trusted goal runner SHALL select
the explicit system-global Codex authority; device-auth restore and projection
SHALL reach the session host's credential volume through the control-plane
service listener before new Codex work. The host and invocation SHALL hold no
database or credential-store access. The retained startup scenarios below
specify explicit authority/ordering and safe degradation on that path; they do
not authorize domain credential custody. Flat topology never implies fallback.
D18 functions keep their separately narrow deterministic-caller credentials.

ID: REQ-core-daemon-001
Source: RFC 0001 startup phases; heart-and-soul/security.md; craft-and-care/security-and-secrets.md; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Daemon startup restores from one selected authority

- **WHEN** the trusted runner reaches the session host Codex CLI-auth restore phase
- **THEN** it passes the explicit system-global `cli-auth/codex` authority to
  the restoration path
- **AND** it does not select a schema-local document merely because that
  document exists first in a layered store

#### Scenario: Flat topology remains explicit

- **WHEN** a deployment has one database pool serving both the local and
  system-global stores
- **THEN** the trusted runner supplies that pool as the explicit authority
- **AND** authority reads and writes do not degrade into local fallback
  semantics

#### Scenario: Authority loss is reported without stale credential recovery

- **WHEN** the trusted runner cannot obtain the required Codex authority during restore
- **THEN** it emits safe degraded-startup evidence identifying the unavailable
  authority channel
- **AND** it does not repopulate the provider runtime home from a local
  schema-specific CLI-auth value

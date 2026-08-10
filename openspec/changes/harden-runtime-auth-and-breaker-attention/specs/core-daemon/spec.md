## ADDED Requirements

### Requirement: Authoritative Codex CLI-Auth Restore at Runtime Startup

The daemon SHALL construct and retain an explicit system-global
`cli-auth/codex` authority before Codex CLI-auth restore. Every daemon and
connector startup restore that can populate the shared Codex runtime home SHALL
use that authority, rather than its schema-local credential namespace or an
implicit fallback pool. The startup path SHALL preserve the current ordering in
which Codex CLI-auth restore completes before a Codex runtime is invoked. This
requirement does not change existing startup authority behavior for other CLI
providers.

ID: REQ-core-daemon-001
Source: RFC 0001 startup phases; heart-and-soul/security.md; craft-and-care/security-and-secrets.md; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Daemon startup restores from one selected authority

- **WHEN** a daemon reaches its Codex CLI-auth restore phase
- **THEN** it passes the explicit system-global `cli-auth/codex` authority to
  the restoration path
- **AND** it does not select a schema-local document merely because that
  document exists first in a layered store

#### Scenario: Flat topology remains explicit

- **WHEN** a deployment has one database pool serving both the local and
  system-global stores
- **THEN** the daemon supplies that pool as the explicit authority
- **AND** authority reads and writes do not degrade into local fallback
  semantics

#### Scenario: Authority loss is reported without stale credential recovery

- **WHEN** the daemon cannot obtain the required Codex authority during restore
- **THEN** it emits safe degraded-startup evidence identifying the unavailable
  authority channel
- **AND** it does not repopulate the provider runtime home from a local
  schema-specific CLI-auth value

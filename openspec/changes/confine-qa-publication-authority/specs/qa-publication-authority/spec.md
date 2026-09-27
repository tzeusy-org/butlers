## ADDED Requirements

### Requirement: Credential-free investigation containment
Investigation and follow-up agents SHALL receive no GitHub credential or generic publication authority and SHALL be unable to inspect publisher memory/files/environment/IPC/inherited descriptors. Unproven isolation disables automated publication.

ID: REQ-qa-publication-authority-001
Source: adopted-contract.md requirement 1; RFC0015
Scope: v1-mandatory

#### Scenario: Credential-free investigation containment
- **WHEN** isolation cannot be proven
- **THEN** publication remains unavailable without token fallback

### Requirement: Immutable attempt publication binding
Publisher SHALL use dispatcher-held exact repository, branch, base, artifact/head and PR binding; agent payload cannot choose these or an operation. Duplicate requests cannot allocate another resource.

ID: REQ-qa-publication-authority-002
Source: adopted-contract.md requirement 2; RFC0015
Scope: v1-mandatory

#### Scenario: Immutable attempt publication binding
- **WHEN** a request changes its bound target or payload
- **THEN** it refuses before egress without altering prior status

### Requirement: Closed publication operations
Only validated non-force QA branch publication/update and bound PR create/update/fixed sanitized labels SHALL be available. Merge/review/approval/queue/protected-ref writes and generic provider operations are forbidden.

ID: REQ-qa-publication-authority-003
Source: adopted-contract.md requirement 3; RFC0015
Scope: v1-mandatory

#### Scenario: Closed publication operations
- **WHEN** an unsupported operation is requested
- **THEN** no provider mutation occurs

### Requirement: Whole reachable artifact sanitization
All newly reachable tree/commit content, author metadata, messages and PR/labels SHALL be validated before first push and frozen against replacement. Publisher SHALL never execute agent git configuration/hooks/helpers/code.

ID: REQ-qa-publication-authority-004
Source: adopted-contract.md requirement 4; RFC0015
Scope: v1-mandatory

#### Scenario: Whole reachable artifact sanitization
- **WHEN** a reachable prior commit leaks content or validated bytes are swapped
- **THEN** publication refuses without first push or implicit deletion

### Requirement: Coarse grants remain explicit residual risk
Provider grants SHALL be minimum one-repository dedicated authority without bypass; independent effective protection evidence is required. Publisher compromise risk MUST NOT be described as an absent provider permission.

ID: REQ-qa-publication-authority-005
Source: adopted-contract.md requirement 5; RFC0015
Scope: v1-mandatory

#### Scenario: Coarse grants remain explicit residual risk
- **WHEN** local denial tests pass without provider-role verification
- **THEN** activation remains held

### Requirement: Durable ambiguous outcome recovery
Operation binding/stage/expected head/outcome SHALL persist before egress outside agent control. Ambiguous mutation MUST reconcile only its exact bound resource read-only or remain held; no blind retry/deletion. An unresolved publication lineage SHALL retain a durable active admission hold across initial/follow-up completion, watchdog, triage/cooldown and restart/stale-attempt recovery; a terminal transport record cannot authorize a fresh attempt, branch or PR. Cleanup retains the sealed artifact and binding until exact reconciliation resolves the hold.

ID: REQ-qa-publication-authority-006
Source: adopted-contract.md requirement 6; RFC0015
Scope: v1-mandatory

#### Scenario: Durable ambiguous outcome recovery
- **WHEN** response is lost after remote success
- **THEN** another resource is not created and cleanup is not inferred


#### Scenario: Cooldown and restart cannot bypass ambiguous publication
- **WHEN** an ambiguous publication is followed by watchdog timeout, cooldown expiry, follow-up or process recovery
- **THEN** the same unresolved lineage remains held and no fresh publication attempt/ref/PR is allocated until exact bound-resource reconciliation is durably recorded

### Requirement: Adversarial source and isolated live evidence
Synthetic transport and actual process-isolation tests SHALL cover malicious exports/config, authority substitutions/races, duplicate requests and ambiguity. Live controlled-resource proof requires separate authority and no forbidden mutation negative.

ID: REQ-qa-publication-authority-007
Source: adopted-contract.md requirement 7; RFC0015
Scope: v1-mandatory

#### Scenario: Adversarial source and isolated live evidence
- **WHEN** only mock transport tests passed
- **THEN** deployed credential capability remains unproven

### Requirement: Operational gates survive source delivery
Receipts SHALL contain only approved categories/digests. Credential/provider/account changes, live canary/cleanup and deployment/activation remain outside repository authority; original operation beads stay open until actual evidence.

ID: REQ-qa-publication-authority-008
Source: adopted-contract.md requirement 8; RFC0015
Scope: v1-mandatory

#### Scenario: Operational gates survive source delivery
- **WHEN** source implementation lands
- **THEN** no live capability or operational closure is inferred

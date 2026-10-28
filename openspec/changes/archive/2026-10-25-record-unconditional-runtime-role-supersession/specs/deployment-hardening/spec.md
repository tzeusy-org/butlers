## RENAMED Requirements
- FROM: `### Requirement: Deployment Posture Profiles`
- TO: `### Requirement: Environment-Independent Runtime Isolation`
- FROM: `### Requirement: Anonymous Metrics Access Disabled Outside Dev`
- TO: `### Requirement: Anonymous Metrics Access Requires Explicit Configuration`
- FROM: `### Requirement: Strict DB-Role Enforcement Under Hardened Posture`
- TO: `### Requirement: Unconditional DB-Role Enforcement`

## MODIFIED Requirements

### Requirement: Environment-Independent Runtime Isolation

Every butler runtime pool SHALL establish its designated runtime role before serving queries, regardless of deployment environment. No profile or opt-out flag SHALL permit shared-user runtime continuation. Infrastructure credential and anonymous-metrics diagnostics SHALL remain independent observations of configured services; they SHALL NOT attest fleet-wide runtime security or alter dashboard DatabaseManager authority. Existing network isolation, explicit service selection, runtime-probe controls and deployment approvals SHALL remain unchanged.

ID: REQ-deployment-hardening-011
Source: about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Environment does not waive runtime role enforcement
- **WHEN** the designated runtime role is missing, unconfigured for a butler schema, unverifiable or cannot be assumed
- **THEN** startup or acquisition refuses in every environment
- **AND** no deployment profile disables the boundary
### Requirement: Anonymous Metrics Access Requires Explicit Configuration

Anonymous Grafana access SHALL default to disabled. A retained local Compose operator MAY explicitly configure anonymous viewing through `GF_AUTH_ANONYMOUS_ENABLED`; enabled access SHALL be reported by the configuration diagnostic without a profile exemption. This SHALL NOT change the live Kubernetes LGTM access policy.

ID: REQ-deployment-hardening-012
Source: docs/operations/grafana-monitoring.md; docs/operations/deployment-posture.md
Scope: v1-mandatory

#### Scenario: Anonymous viewer disabled in configured services
- **WHEN** anonymous viewing is not explicitly enabled
- **THEN** Grafana anonymous authentication is disabled and the metrics surface
  requires authentication

#### Scenario: Anonymous viewer explicitly configured locally
- **WHEN** the local operator explicitly enables anonymous Grafana viewing
- **THEN** anonymous Grafana viewer access MAY be enabled for that session
### Requirement: Unconditional DB-Role Enforcement

Butler runtime role verification and assumption failures SHALL fail closed in every environment. Missing or unreadable roles and missing role configuration for a butler schema SHALL refuse startup or acquisition before queries can use the connecting identity. Every acquire SHALL re-establish the designated role; revoked membership SHALL refuse. Owned partial resources SHALL be closed and errors SHALL retain causal identity without disclosing private connection values. No development or deployment setting SHALL waive this boundary. Dashboard pools, connector pools and role/login/grant provisioning remain separate authority boundaries. Supported online migration/startup requests SHALL independently satisfy REQ-database-security-011 and SHALL NOT self-bootstrap or acquire recovery authority.

ID: REQ-deployment-hardening-010
Source: about/heart-and-soul/security.md (Schema Isolation); bu-wws1eq recorded owner decision (2026-10-21); REQ-database-security-011
Scope: v1-mandatory

#### Scenario: Missing role fails closed in every environment
- **WHEN** a butler runtime role is missing, unconfigured for its schema, unverifiable or cannot be assumed
- **THEN** startup or the affected acquisition refuses before a usable runtime connection is returned
- **AND** no infrastructure configuration permits shared-user continuation

#### Scenario: Infrastructure diagnostic does not attest database isolation
- **WHEN** configured infrastructure defaults or anonymous metrics access are reported
- **THEN** the diagnostic reports those facts independently of runtime role establishment
- **AND** no secure fleet claim or dashboard pool authority is inferred

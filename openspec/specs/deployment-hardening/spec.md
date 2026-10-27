# Deployment Hardening

## Purpose

Defines runtime role enforcement and truthful infrastructure diagnostics. Runtime role isolation is unconditional; infrastructure configuration does not select a different database authority boundary.

## Requirements

### Requirement: Environment-Independent Runtime Isolation

Every butler runtime pool SHALL establish its designated runtime role before serving queries, regardless of deployment environment. No profile or opt-out flag SHALL permit shared-user runtime continuation. Infrastructure credential and anonymous-metrics diagnostics SHALL remain independent observations of configured services; they SHALL NOT attest fleet-wide runtime security or alter dashboard DatabaseManager authority. Existing network isolation, explicit service selection, runtime-probe controls and deployment approvals SHALL remain unchanged.

ID: REQ-deployment-hardening-011
Source: about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Environment does not waive runtime role enforcement
- **WHEN** the designated runtime role is missing, unconfigured for a butler schema, unverifiable or cannot be assumed
- **THEN** startup or acquisition refuses in every environment
- **AND** no deployment profile disables the boundary

### Requirement: Infrastructure Credentials Via Secret Indirection

Infrastructure service credentials SHALL be sourced
from environment or secret indirection (e.g. `env_file`, host-provided
environment variables, or the credential store), and SHALL NOT be hardcoded as
well-known default values in committed service configuration. This SHALL cover at
minimum the MinIO root user/password and the Grafana admin user/password, and
SHALL apply equally to service definitions and to any bootstrap/setup step that
re-uses the same credentials (e.g. the MinIO `mc alias` setup step).

This requirement reinforces `security.md` "Deployment Security" principle 4
("No secrets in compose files. Infrastructure bootstrap vars only.").

#### Scenario: MinIO credentials are not hardcoded defaults
- **WHEN** the deployment configures the MinIO service and its setup step
- **THEN** the root user and password are resolved from secret indirection and are
  not the literal well-known values `minioadmin`/`minioadmin` in any committed
  compose file

#### Scenario: Grafana admin credentials are not hardcoded defaults
- **WHEN** the deployment configures the Grafana service
- **THEN** the admin user and password are resolved from secret indirection and are
  not the literal well-known values `admin`/`admin` in committed service configuration

### Requirement: Known-Default Credential Detection

The deployment SHALL detect when an infrastructure service is configured
with a known-default credential value (specifically the values `minioadmin` or
`admin`) and SHALL fail startup or emit a loud, persistent warning identifying the
offending service and credential. Detection SHALL NOT depend on a runtime role-enforcement opt-out.

#### Scenario: Startup refuses known-default credentials in configured services
- **WHEN** the deployment is started and a MinIO or Grafana credential
  resolves to a known-default value (`minioadmin` or `admin`)
- **THEN** the deployment fails to start, or emits a loud warning that names the
  affected service and credential key, without disclosing credential values

#### Scenario: Non-default credentials pass detection
- **WHEN** the deployment is started and all infrastructure credentials
  resolve to values other than the known defaults
- **THEN** the default-credential detection passes without warning and startup
  proceeds

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

### Requirement: Degraded-Safety Indicator For Insecure Defaults

The deployment SHALL surface, via its metrics/dashboard surface, when it is
running with known-insecure infrastructure defaults (e.g. a detected
known-default credential or anonymous metrics access enabled in this process configuration). The
indicator SHALL be observable without inspecting raw configuration, so the owner
can tell at a glance that the running stack is in a degraded-safety state.

The dashboard authentication indicator and dev-secret export fallback are owned
by the separate `harden-secrets-and-dashboard-honesty` change and are not
re-specified here; this indicator is limited to the infrastructure-default
posture signal.

#### Scenario: Indicator reflects insecure defaults
- **WHEN** the deployment is running with a known-default credential still in
  effect or anonymous metrics access enabled in this process configuration
- **THEN** the metrics/dashboard surface exposes a degraded-safety indicator
  identifying that an insecure default is active

#### Scenario: Infrastructure observations clear independently
- **WHEN** the configured credentials are non-default and anonymous metrics access is disabled
- **THEN** the infrastructure-default indicators clear
- **AND** this does not attest fleet security or alter runtime role enforcement

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

### Requirement: Backup And Restore Verification Path

An always-on personal-data deployment SHALL have a documented, executable
backup-and-restore path for the PostgreSQL data plane, and that path SHALL be
verifiable (a restore drill that proves a backup can be restored to a working
state). This is shipped: `deploy/backup/pg_dump.sh` produces timestamped dumps,
`scripts/pg_restore.sh` restores to a scratch database, and
`scripts/pg_verify_restore.sh` runs the verification drill (schema, table, and
row-count checks), all documented in `docs/operations/backup-restore.md`. Restore
verification protects the owner's irreplaceable personal data against corruption
or accidental loss.

A produced backup SHALL be a stated coverage claim, not merely a zero exit
status. The backup SHALL cover the application data plane. Objects it omits
SHALL be limited to the trusted-bootstrap control plane — objects that the
cluster-superuser bootstrap reconstructs and that a dump could not correctly
restore, because the restoring login is not a member of their fenced owner role.
Every omission SHALL be documented with its cost in
`docs/operations/backup-restore.md`.

The omission set SHALL be verified in both directions against a real
bootstrapped database: an object the dump identity cannot read but the backup
does not omit SHALL fail verification (it would abort the run and publish no
file), and an object the dump identity can read but the backup omits SHALL fail
verification (it would silently narrow the backup). The dump SHALL NOT be made
to succeed by widening the dump identity's privileges or by enabling row-level
security during the dump, either of which trades a loud failure for a silent
one.

A backup run that publishes no artifact SHALL say so on its error stream, and an
artifact that is undersized or does not decompress SHALL NOT be published.

#### Scenario: Documented restore drill exists and is verifiable
- **WHEN** an operator follows the documented backup-and-restore procedure
- **THEN** a backup of the PostgreSQL data plane can be produced and restored to a
  working instance, and the procedure includes a verification step proving the
  restored data is intact

#### Scenario: A fenced object is added without a backup decision
- **WHEN** a new object is fenced away from the dump identity and the backup's
  omission set is not updated
- **THEN** verification fails and names the object, rather than the nightly
  backup silently ceasing to produce a file

#### Scenario: The omission set is widened past what is fenced
- **WHEN** the omission set covers an object the dump identity can read
- **THEN** verification fails and names the object, rather than the backup
  silently ceasing to contain it

#### Scenario: A backup run produces no file
- **WHEN** a backup run fails before publishing an artifact
- **THEN** the run exits non-zero and reports the failure on its error stream,
  and the backup directory retains only previously published artifacts

## Source References
- Non-Negotiable Rule 1 (User-federated: one user, one instance, full
  sovereignty) — protecting the owner's always-on personal-data deployment from
  trivial credential-stuffing access to object storage and the metrics surface.
- `about/heart-and-soul/security.md` "Deployment Security" (principle 4: "No
  secrets in compose files. Infrastructure bootstrap vars only."); these requirements
  reinforce that principle for MinIO/Grafana defaults without contradicting the
  established network-isolation, localhost-binding, egress-firewall, and
  no-privileged/cap_add/docker-socket posture, and without touching the
  documented root-by-design runtime containers or the spawner's required
  `apparmor:unconfined`.

# Deployment Hardening

## Purpose

Defines the contract for hardening an always-on personal-data deployment beyond
the baseline network-level controls. Introduces an explicit deployment-posture
model (`dev` vs `local-private`) that makes insecure infrastructure defaults
visible, opt-in to harden, and enforced under the hardened posture — without
breaking the owner's currently running stack on adoption.

## Requirements

### Requirement: Deployment Posture Profiles

The deployment SHALL define an explicit deployment-posture profile that
distinguishes a convenience-oriented `dev` posture from a hardened posture
(named, e.g., `local-private`). The active profile SHALL be machine-readable
(resolvable at startup, e.g. from an environment variable or compose profile).
The default-when-unset posture SHALL be `dev` so that the owner's currently
running stack does not break on adoption; the hardened posture is an explicit
opt-in. (This mirrors the opt-in, no-surprise-fail-closed stance chosen for
dashboard authentication in `harden-secrets-and-dashboard-honesty`.) The
deployment SHOULD make selecting the hardened posture frictionless and clearly
documented so that an always-on personal deployment can be hardened deliberately.

Convenience defaults that are acceptable under `dev` (well-known infrastructure
credentials, anonymous metrics viewing) SHALL NOT be silently acceptable under
the hardened posture; the hardened posture's contract is defined by the
remaining requirements in this capability. Under the `dev` posture these
defaults are permitted but SHALL be surfaced by the degraded-safety indicator
(see below) so the insecure posture is never silent.

This requirement does not alter the established network-level hardening
documented in `security.md` "Deployment Security" (localhost-only port binding,
four-network isolation, egress private-subnet firewall, no `privileged` /
`cap_add` / Docker-socket mounts), nor does it touch the deliberate root-by-design
runtime/connector containers or the `apparmor:unconfined` setting required by the
LLM spawner.

#### Scenario: Dev posture is the default-when-unset
- **WHEN** the deployment starts without an explicit profile selection
- **THEN** it resolves to the `dev` posture (preserving current behavior so the
  running stack is not broken on adoption)
- **AND** any active convenience defaults are surfaced by the degraded-safety
  indicator rather than silently accepted

#### Scenario: Hardened posture is explicitly opt-in
- **WHEN** the operator explicitly selects the hardened (`local-private`) profile
- **THEN** the deployment enforces the hardened-profile contract for
  infrastructure credentials and metrics access, refusing known-insecure defaults

### Requirement: Infrastructure Credentials Via Secret Indirection

Under the hardened posture, infrastructure service credentials SHALL be sourced
from environment or secret indirection (e.g. `env_file`, host-provided
environment variables, or the credential store), and SHALL NOT be hardcoded as
well-known default values in any committed compose file. This SHALL cover at
minimum the MinIO root user/password and the Grafana admin user/password, and
SHALL apply equally to service definitions and to any bootstrap/setup step that
re-uses the same credentials (e.g. the MinIO `mc alias` setup step).

This requirement reinforces `security.md` "Deployment Security" principle 4
("No secrets in compose files. Infrastructure bootstrap vars only.").

#### Scenario: MinIO credentials are not hardcoded defaults
- **WHEN** the hardened deployment configures the MinIO service and its setup step
- **THEN** the root user and password are resolved from secret indirection and are
  not the literal well-known values `minioadmin`/`minioadmin` in any committed
  compose file

#### Scenario: Grafana admin credentials are not hardcoded defaults
- **WHEN** the hardened deployment configures the Grafana service
- **THEN** the admin user and password are resolved from secret indirection and are
  not the literal well-known values `admin`/`admin` in any committed compose file

### Requirement: Known-Default Credential Detection

The hardened deployment SHALL detect when an infrastructure service is configured
with a known-default credential value (specifically the values `minioadmin` or
`admin`) and SHALL fail startup or emit a loud, persistent warning identifying the
offending service and credential. Detection SHALL NOT be silently skippable under
the hardened posture.

#### Scenario: Startup refuses known-default credentials under hardened posture
- **WHEN** the hardened deployment is started and a MinIO or Grafana credential
  resolves to a known-default value (`minioadmin` or `admin`)
- **THEN** the deployment fails to start, or emits a loud warning that names the
  affected service and the detected default value

#### Scenario: Non-default credentials pass detection
- **WHEN** the hardened deployment is started and all infrastructure credentials
  resolve to values other than the known defaults
- **THEN** the default-credential detection passes without warning and startup
  proceeds

### Requirement: Anonymous Metrics Access Disabled Outside Dev

The deployment SHALL disable anonymous Grafana viewer access (Grafana anonymous
authentication) outside an explicit `dev` context. Under the hardened posture,
viewing the metrics surface SHALL require authentication.

#### Scenario: Anonymous viewer disabled under hardened posture
- **WHEN** the deployment runs under the hardened posture
- **THEN** Grafana anonymous authentication is disabled and the metrics surface
  requires authentication

#### Scenario: Anonymous viewer permitted only in dev
- **WHEN** the operator explicitly selects the `dev` profile
- **THEN** anonymous Grafana viewer access MAY be enabled for that session

### Requirement: Degraded-Safety Indicator For Insecure Defaults

The deployment SHALL surface, via its metrics/dashboard surface, when it is
running with known-insecure infrastructure defaults (e.g. a detected
known-default credential or anonymous metrics access enabled outside `dev`). The
indicator SHALL be observable without inspecting raw configuration, so the owner
can tell at a glance that the running stack is in a degraded-safety state.

The dashboard authentication indicator and dev-secret export fallback are owned
by the separate `harden-secrets-and-dashboard-honesty` change and are not
re-specified here; this indicator is limited to the infrastructure-default
posture signal.

#### Scenario: Indicator reflects insecure defaults
- **WHEN** the deployment is running with a known-default credential still in
  effect or anonymous metrics access enabled outside `dev`
- **THEN** the metrics/dashboard surface exposes a degraded-safety indicator
  identifying that an insecure default is active

#### Scenario: Indicator clears when hardened
- **WHEN** the deployment is running under the hardened posture with no
  known-default credentials and anonymous metrics access disabled
- **THEN** the degraded-safety indicator reports a secure/clear state

### Requirement: Strict DB-Role Enforcement Under Hardened Posture

Under the hardened posture, DB role-enforcement and permission-gate failures SHALL fail closed rather than silently downgrade privileges.

A missing or unverifiable PostgreSQL runtime role SHALL cause startup (or the
affected connection acquire) to fail loudly instead of proceeding with the
connecting user's privileges. Under `dev`, the existing graceful fallback SHALL
be retained but the degraded state SHALL be reported by the degraded-safety
indicator.

This adds an opt-in strict mode bound to the hardened posture; it does not change
the `database-security` graceful-fallback policy for `dev`. Under `dev`,
`src/butlers/db.py` retains fail-open behavior (logs "Could not verify role … SET
ROLE enforcement disabled" or "Role … not found; SET ROLE enforcement disabled"
and proceeds with the connecting user's privileges). Under the hardened posture,
`Database.strict_role_enforcement` (the `Database` class in `src/butlers/db.py`)
is enabled (auto-detected from `is_hardened_posture()`) and the daemon fails
closed rather than silently losing
schema isolation for an always-on deployment.

#### Scenario: Missing role fails closed under hardened posture
- **WHEN** the deployment runs under the hardened posture and a butler's runtime
  role cannot be verified or does not exist
- **THEN** the daemon fails loudly (startup or connection acquire) instead of
  silently disabling `SET ROLE` enforcement

#### Scenario: Graceful fallback retained in dev but surfaced
- **WHEN** the deployment runs under `dev` and a runtime role cannot be verified
- **THEN** the existing graceful fallback applies (enforcement disabled, daemon
  continues) AND the degraded-safety indicator reports that role enforcement is
  disabled

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

#### Scenario: Documented restore drill exists and is verifiable
- **WHEN** an operator follows the documented backup-and-restore procedure
- **THEN** a backup of the PostgreSQL data plane can be produced and restored to a
  working instance, and the procedure includes a verification step proving the
  restored data is intact

## Source References
- Non-Negotiable Rule 1 (User-federated: one user, one instance, full
  sovereignty) — protecting the owner's always-on personal-data deployment from
  trivial credential-stuffing access to object storage and the metrics surface.
- `about/heart-and-soul/security.md` "Deployment Security" (principle 4: "No
  secrets in compose files. Infrastructure bootstrap vars only.") — this change
  reinforces that principle for MinIO/Grafana defaults without contradicting the
  established network-isolation, localhost-binding, egress-firewall, and
  no-privileged/cap_add/docker-socket posture, and without touching the
  documented root-by-design runtime containers or the spawner's required
  `apparmor:unconfined`.

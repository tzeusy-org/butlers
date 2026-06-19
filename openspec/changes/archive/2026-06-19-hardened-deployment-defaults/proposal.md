## Why

The Butlers deployment is a single user's always-on, full-sovereignty instance
holding their database, credentials, and LLM keys (vision.md Rule 1). Today the
shipped Docker Compose files hardcode well-known default credentials for two
infrastructure services — MinIO root (`minioadmin`/`minioadmin`) and Grafana
admin (`admin`/`admin`), with anonymous Grafana viewer access also enabled. On
localhost-bound ports these defaults are not LAN-reachable, but they violate the
spirit of the Deployment Security doctrine ("No secrets in compose files;
infrastructure bootstrap vars only") and leave the owner's deployment one
misconfigured port-binding or compromised container away from trivial
credential-stuffing access to object storage and the metrics surface. There is
also no machine-readable notion of deployment posture, so nothing distinguishes
a convenience-first dev stack from a hardened personal deployment, and nothing
surfaces when the running stack is relying on insecure defaults.

## What Changes

- Add a **deployment-hardening** capability defining a deployment-profile concept
  with two postures: `dev` (convenience defaults acceptable) and a hardened
  posture (e.g. `local-private`) that refuses known-insecure defaults.
- Require infrastructure service credentials (MinIO root, Grafana admin) to be
  sourced via env/secret indirection rather than hardcoded well-known defaults in
  a hardened deployment.
- Require the deployment to detect known-default credential values
  (`minioadmin`, `admin`) and fail or warn loudly under the hardened profile.
- Require anonymous Grafana viewer access to be disabled outside an explicit dev
  context.
- Require a **degraded-safety indicator**: the deployment SHALL be able to
  surface (via metrics/dashboard) when it is running with known-insecure
  infrastructure defaults.
- Add **strict DB-role enforcement under the hardened posture**: the
  `database-security` graceful fallback (silently disabling `SET ROLE` when a
  runtime role is missing/unverifiable — observed in `src/butlers/db.py`) becomes
  fail-closed under hardened posture, while `dev` retains graceful fallback with
  the degraded state surfaced.
- Add a **backup-and-restore verification path**: a documented, executable, and
  verifiable backup/restore drill for the PostgreSQL data plane (none ships
  today).
- **Non-goals (explicitly out of scope for this change):**
  - **Non-root runtime/connector containers.** Runtime and connector containers
    run as root by deliberate, documented design — the LLM spawner needs
    `HOME=/root` for the CLI config volumes (`runtime_claude` at `/root/.claude`,
    etc.). This change does not touch that.
  - **Removing `apparmor:unconfined` outright.** Documented as needed by the
    spawner. This change may at most express a SHOULD to scope/tighten apparmor
    where feasible without breaking the spawner — never a hard requirement to
    remove it.
  - **Dashboard API-key auth and dev-secret export fallback.** Owned by the
    separate `harden-secrets-and-dashboard-honesty` change; this change only
    references the relationship in prose and does not duplicate those
    requirements.

## Capabilities

### New Capabilities
- `deployment-hardening`: Deployment-posture profiles and the hardened-profile
  contract for infrastructure credentials (no well-known defaults, secret
  indirection, default-credential detection, anonymous-access disablement) plus a
  degraded-safety indicator surfacing insecure defaults.

### Modified Capabilities
<!-- None. No existing capability spec's requirements change. -->

## Impact

- **Compose files:** `docker-compose.yml` (MinIO `minio` + `minio-setup`
  services) and `docker-compose.observability.yml` (`grafana` service) —
  credential and anonymous-auth env wiring. Spec/planning artifact only; this
  change does not modify compose or source.
- **Deployment tooling:** `compose.sh` (or equivalent startup path) gains
  profile selection and default-credential detection.
- **Observability:** a metric/dashboard signal for the degraded-safety indicator.
- **Doctrine alignment:** reinforces `security.md` "Deployment Security"
  principle 4 ("No secrets in compose files. Infrastructure bootstrap vars
  only.") and vision.md Rule 1 (user-federated sovereignty). Does not contradict
  the established network-isolation, localhost-binding, four-network, egress
  firewall, or no-privileged/cap_add/docker-socket posture.
- **Related change:** `harden-secrets-and-dashboard-honesty` (dev-secret export
  fallback, dashboard auth indicator) — coordinated, not duplicated.

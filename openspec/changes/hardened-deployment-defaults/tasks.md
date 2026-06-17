## 1. Deployment Posture Profile

- [ ] 1.1 Define the deployment-posture profile concept (`dev` vs hardened, e.g. `local-private`) and where it is resolved at startup (env var and/or compose profile)
- [ ] 1.2 Make the hardened posture the default when no explicit `dev` selection is present
- [ ] 1.3 Document the profile selection mechanism for the operator (how to opt into `dev`)

## 2. Infrastructure Credentials Via Secret Indirection

- [ ] 2.1 Replace hardcoded MinIO `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` in `docker-compose.yml` with env/secret indirection
- [ ] 2.2 Update the MinIO `minio-setup` `mc alias` step to consume the same indirected credentials (no literal `minioadmin`)
- [ ] 2.3 Replace hardcoded Grafana `GF_SECURITY_ADMIN_USER`/`GF_SECURITY_ADMIN_PASSWORD` in `docker-compose.observability.yml` with env/secret indirection
- [ ] 2.4 Provide a documented secret source path (env_file / host env / credential store) so the operator can supply non-default values

## 3. Known-Default Credential Detection

- [ ] 3.1 Add a startup check (e.g. in `compose.sh` or the equivalent startup path) that detects known-default values (`minioadmin`, `admin`) for the infrastructure credentials
- [ ] 3.2 Under the hardened posture, make detection fail startup or emit a loud, persistent warning naming the offending service and value
- [ ] 3.3 Ensure detection is not silently skippable under the hardened posture

## 4. Anonymous Metrics Access

- [ ] 4.1 Disable Grafana anonymous authentication (`GF_AUTH_ANONYMOUS_ENABLED`) outside the explicit `dev` context
- [ ] 4.2 Permit anonymous viewer only when the `dev` profile is explicitly selected

## 5. Degraded-Safety Indicator

- [ ] 5.1 Emit a metric/signal reflecting whether known-insecure infrastructure defaults are active (known-default credential or anonymous metrics outside `dev`)
- [ ] 5.2 Surface the degraded-safety indicator on the metrics/dashboard surface so it is observable without inspecting raw config
- [ ] 5.3 Ensure the indicator reports a clear/secure state when the hardened posture is fully satisfied

## 6. Strict DB-role enforcement under hardened posture

- [ ] 6.1 Under the hardened posture, make a missing/unverifiable runtime role fail loudly (startup or connection acquire) instead of silently disabling SET ROLE enforcement (current fail-open at `src/butlers/db.py` ~lines 345-363)
- [ ] 6.2 Retain the `database-security` graceful fallback under `dev`, but report the disabled-enforcement state via the degraded-safety indicator (task 5.x)
- [ ] 6.3 Assess the permission-gate / audit paths (`src/butlers/core/spawner.py`, `src/butlers/api/dashboard_audit_middleware.py`) for analogous fail-open behavior; file gap beads if any are found

## 7. Backup and restore verification path

- [ ] 7.1 Add a documented, executable backup procedure for the PostgreSQL data plane (e.g. `pg_dump`-based script)
- [ ] 7.2 Add a restore procedure and a verification drill proving a backup can be restored to a working state with data intact
- [ ] 7.3 Document the drill cadence and where backups are stored

## 8. Validation

- [ ] 8.1 Verify hardened-posture startup with non-default credentials passes detection and disables anonymous access
- [ ] 8.2 Verify hardened-posture startup with a known-default credential triggers the fail/warn path and the degraded-safety indicator
- [ ] 8.3 Verify hardened-posture startup with a missing runtime role fails closed; verify `dev` retains graceful fallback with the degraded indicator set
- [ ] 8.4 Verify the backup/restore drill produces and restores an intact PostgreSQL backup
- [ ] 8.5 Confirm no committed compose file contains literal `minioadmin` or `admin` credential defaults for the hardened path
- [ ] 8.6 Confirm the change does not regress network isolation, localhost binding, egress firewall, root-by-design runtime containers, or the spawner's `apparmor:unconfined`

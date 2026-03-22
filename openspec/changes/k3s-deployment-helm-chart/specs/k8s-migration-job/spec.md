## ADDED Requirements

### Requirement: Pre-install migration Job

The Helm chart SHALL include a Job with `helm.sh/hook: pre-install,pre-upgrade` and `helm.sh/hook-weight: "-5"` that runs `butlers db migrate` before any Deployment is created or updated.

#### Scenario: Migration runs before butler pods start
- **WHEN** `helm install` or `helm upgrade` is executed
- **THEN** the migration Job runs to completion before any butler Deployment pods are scheduled

#### Scenario: Migration Job uses correct database credentials
- **WHEN** the migration Job runs
- **THEN** it SHALL have `DATABASE_URL` or `POSTGRES_*` env vars injected from the same k8s Secret used by butler Deployments

### Requirement: Migration concurrency guard

The migration Job SHALL have `spec.parallelism: 1` and `spec.completions: 1` to prevent concurrent Alembic migration execution.

#### Scenario: Only one migration pod runs at a time
- **WHEN** the migration Job is active
- **THEN** at most one pod is running (`parallelism: 1`)

### Requirement: Migration failure handling

The migration Job SHALL have `backoffLimit: 3` (configurable) and `activeDeadlineSeconds: 300` (configurable). If the Job fails, the Helm hook blocks the release, preventing butler pods from starting against an unmigrated database.

#### Scenario: Migration fails and blocks deployment
- **WHEN** `butlers db migrate` exits non-zero
- **THEN** the Job retries up to `backoffLimit` times, and if all attempts fail, the Helm release fails

#### Scenario: Migration timeout
- **WHEN** migration runs longer than `activeDeadlineSeconds`
- **THEN** the Job is terminated and the Helm release fails

### Requirement: Migration Job cleanup

The Job SHALL have `helm.sh/hook-delete-policy: before-hook-creation` so that stale Jobs from previous releases are cleaned up before a new migration runs.

#### Scenario: Stale Job is cleaned up
- **WHEN** a new `helm upgrade` is run and a previous migration Job exists
- **THEN** the old Job is deleted before the new migration Job is created

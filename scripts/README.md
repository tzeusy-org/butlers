# Scripts

Utility scripts for repository maintenance and fixes.

## init-db.sql

Privileged PostgreSQL bootstrap script to run **before** the first Alembic
migration on a fresh database. It must be executed by a privileged cluster
superuser. Supply the normal connecting/migration user separately through the
`butlers.connecting_user` GUC; it must not be the active bootstrap identity.
It is safe to re-run later if the managed schema/role surface expands.

**Prerequisites:** The PostgreSQL server must have the `pgvector` binary
installed.  The standard `postgres` Docker image does not include it; use
`pgvector/pgvector:pg16` (or later) instead, or install the extension manually.

What it does:

1. Installs required extensions: `pgcrypto`, `uuid-ossp`, `vector` (pgvector),
   and `pg_trgm`.
2. Creates the managed schemas and runtime roles if they do not already exist.
3. Grants each runtime role (`butler_{schema}_rw`, `butler_qa_rw`,
   `connector_writer`) to the connecting user (`POSTGRES_USER`, typically
   `butlers`) so `SET ROLE` works at runtime.
4. Grants database/schema ACLs to runtime roles.
5. Grants schema `USAGE, CREATE` to the migration/runtime user so Alembic can
   create future objects without a second privileged follow-up step.
6. Configures `ALTER DEFAULT PRIVILEGES FOR ROLE <connecting user>` so objects
   created later by Alembic inherit the runtime ACLs immediately.

**Why this is privileged:** Database-level grants, schema ownership/ACLs, and
`ALTER DEFAULT PRIVILEGES FOR ROLE <connecting user>` require a privileged
cluster-superuser bootstrap role. After this script runs once, normal `docker compose` /
Alembic flows can continue using the lower-privilege `butlers` user.

### Usage

```bash
# Typical dev: run as a cluster superuser; grants to 'butlers' (default connecting user)
psql -h localhost -U postgres -d butlers -f scripts/init-db.sql

# Targeting a different connecting user
PGOPTIONS="-c butlers.connecting_user=myappuser" \
    psql -h localhost -U postgres -d butlers -f scripts/init-db.sql
```

The script is idempotent — safe to re-run on an already-provisioned database.

**Note on ordering:**
- Run `init-db.sql` **after** the database and connecting user exist.
- Run Alembic migrations next: `butlers db migrate` (or `docker compose run
  migrations`).
- You should not need a post-migration privileged grant pass for new objects
  created by the connecting user; re-run the bootstrap only when the managed
  schema/role surface itself changes.

## provision_restore_drill_executor.sh

The managed one-shot provisioner enables the distinct
`restore_drill_executor` login after `init-db.sql` has reserved it and the
restore-drill migration has installed its constrained persistence functions.
It reads the executor password only from the private path named by
`RESTORE_DRILL_EXECUTOR_PASSWORD_FILE`; do not pass that value through shared
database environment variables or tracked configuration. See
[`docs/operations/backup-restore.md`](../docs/operations/backup-restore.md)
for the deployment boundary and rollback rules.

## restore-drill-firewall.sh

`restore-drill-firewall.sh` is the reviewed installation source for the
root-owned fixed runtime wrapper at
`/usr/local/libexec/butlers-restore-drill-firewall`. It accepts only validated
literal project, PostgreSQL IPv4, and port arguments. Its policy default-denies
the relay's external bridge at both Docker's `DOCKER-USER`/`FORWARD` hook and
the bridge-to-host `INPUT` path, allowing only TCP to the configured PostgreSQL
endpoint. The credentialed executor sits on a separate Docker `internal`
network; its configured database host MUST be an untrimmed DNS hostname and is
TLS/SNI identity only (including `verify-full`), resolving there to the
uncredentialed relay. It must not be `localhost` or any numeric IPv4 spelling.
The relay alone uses the separately resolved IPv4, with no shared database
credential. That relay/firewall IPv4 must be canonical dotted-decimal remote
unicast and the port must be canonical ASCII decimal matching `[1-9][0-9]{0,4}`
in `1..65535`: loopback,
unspecified, link-local, multicast, documentation,
and policy-reserved targets are rejected, while RFC1918, CGNAT/tailnet,
and valid public unicast remain supported. Legacy decimal, octal, hexadecimal,
and abbreviated `inet_aton` spellings are rejected before DNS resolution. The
pre-source endpoint-literal grammar supports simple `KEY=value` or
`export KEY=value` with optional leading spaces/tabs; raw RHS whitespace is
rejected before sourcing. Other Bash command forms are outside this pre-source
endpoint-literal grammar; their resulting endpoint values are validated without
trimming or reinterpretation.
The relay admits at most two clients without a queue and closes both relay sides on
its finite connect, idle, or session deadlines. A close has at most one second
to flush before the relay aborts the transport, so a non-reading peer cannot
pin the bounded admission slot.

Use `install_restore_drill_firewall_wrapper.sh` only in a root-controlled
deployment setup to install that immutable target. The checked-in
`restore-drill-firewall.sudoers` template grants a deployment group access only
to two fixed wrapper forms: `--prepare-executor-capability-v1 --project` and
the version-gated `--project --db-host --db-port
--require-executor-capability-v1` apply form. Never grant sudo for the checkout
script, a checkout wildcard, `env`, a shell, or the installer.
Ordinary `scripts/compose.sh` dev launches use only `docker-compose.yml` and do
not infer protected execution from the presence of a secret. The only supported
lifecycle paths that include `docker-compose.restore-drill.yml` to start a
protected service are `scripts/compose.sh --with-restore-drill`,
`scripts/compose.sh --prod`, and `butlers deploy`; the read-only inspection
helper is the sole non-lifecycle exception. Those paths stop, call the root-owned
prepare verb, create the relay and executor with its generation-bound nonce,
attest and fence that exact created topology, and only then start the merged
stack. An older installed wrapper rejects the prepare verb before `create`/`up`.
Returning an opted-in dev project to ordinary `scripts/compose.sh` runs the
base-only `down --remove-orphans` path first, which removes the former relay and
executor rather than leaving them as orphaned protected containers. Continue
passing `--with-restore-drill` on later dev launches to keep them enabled.
The post-fence root marker is boot-, project-, nonce-, executor-container/IP/
gateway-, and relay-alias/IP-bound. The wrapper separately discovers the
created relay/container/network topology while it fences both bridges, so a
same-boot manual down/recreate cannot replay it. A stop/start of that
unchanged, already-fenced container generation is not a new authorization; it
retains the same container, network, marker, and host
policy. It is still not a supported operational path. Any `down`, topology
recreation, or root Docker/firewall intervention requires the canonical
prepare/create/fence sequence again.
A bare `docker compose up` uses `docker-compose.yml` alone and therefore omits
the executor, its internal relay network, its external relay bridge, and its
private secret mount; a direct merged invocation lacks a valid prepared marker
and executor generation, so it fails before reading the secret. `restart:
"no"` also prevents an unfenced daemon/host auto-start. Do not compose the
protected fragment to start services directly.
The wrapper derives both created bridge interfaces and the relay's internal
peer address from the Compose project: its relay egress policy permits only the
configured PostgreSQL endpoint, and its executor bridge policy is
default-denied except for the created relay peer at that same configured port.
An `internal` Docker network limits membership but alone does not deny
bridge-gateway or host traffic; the wrapper's bridge `INPUT` and
`DOCKER-USER` rules provide that boundary.

For read-only status, logs, or rendered configuration that includes the
protected topology, use `scripts/restore-drill-compose-inspect.sh`. It accepts
only `config`, `ps`, and `logs`; it never accepts `up` or another lifecycle
verb, so it cannot replace the prepared-launch sequence. Rendered config is a
read-only inspection artifact, not endpoint validation.

## dev.sh

Bootstraps the full local Butlers development stack in `tmux` (dashboard, frontend, connectors, backend, OAuth gate, and postgres preflight).

Contacts sync contract: contacts incremental sync is a module-internal poller
inside `uv run butlers up`. `dev.sh` does not launch a standalone contacts
connector process.

### Usage

```bash
# Preferred compatibility entrypoint
./dev.sh

# Direct script path
./scripts/dev.sh
```

## clear-processes.sh

Kills processes currently listening on the expected local dev ports.

Default ports:
- `POSTGRES_PORT` (default `54320`)
- `FRONTEND_PORT` (default `41173`)
- `DASHBOARD_PORT` (default `41200`)

You can override with `EXPECTED_PORTS` (comma/space separated), for example:

```bash
EXPECTED_PORTS="54320,41173,41200" ./scripts/clear-processes.sh
```

## cleanup_logs.sh

Removes old log files and prunes empty directories under `logs/`.

- Deletes files older than 3 days (default retention)
- Removes empty subdirectories after file cleanup

### Usage

```bash
# Use repository logs/ directory (default)
./scripts/cleanup_logs.sh

# Use a custom logs directory
./scripts/cleanup_logs.sh /path/to/logs
```

Optional environment variable:
- `RETENTION_DAYS` (default: `3`)

## setup_main_ruleset.sh and the merge queue

`setup_main_ruleset.sh` installs the `main-merge-queue` ruleset for
`tzeusy-org/butlers`. The live rule uses squash merges, ALLGREEN grouping, and
non-strict required contexts `check`, `guards`, and `frontend`.

After review and the PR-head gates are complete, add the PR to the queue:

```bash
gh pr merge "$PR" --repo tzeusy-org/butlers --squash --auto
```

The queue creates a `merge_group` and reruns the required contexts against the
exact combined tree it will land. Do not rebase a clean PR merely to refresh it;
rebase only to resolve a real conflict or when review requires it. A queued PR
remains `OPEN` until the queue succeeds, so verify the completed squash merge
before closing source or review Beads:

```bash
gh pr view "$PR" --repo tzeusy-org/butlers --json state,mergedAt
```

The former exact-base merge and between-merges target-health helpers were
deleted when the queue became the sole merge route. Their incident rationale is
preserved in the archived OpenSpec change
`2026-09-05-add-batch-merge-health-gate`.

## fix_beads_dependency_timestamps.py

Detects and fixes dependency records with zero timestamps (`created_at="0001-01-01T00:00:00Z"`) in `.beads/issues.jsonl`.

### Background

Due to a bug in the `bd` CLI when running in no-daemon worktree flows, dependency records created via `bd dep add` may have their `created_at` timestamp set to the zero timestamp instead of a real timestamp. This breaks downstream auditing and timeline reasoning.

### Usage

```bash
# Dry-run mode (shows what would be fixed without making changes)
python scripts/fix_beads_dependency_timestamps.py --dry-run

# Apply fixes
python scripts/fix_beads_dependency_timestamps.py

# Specify custom path
python scripts/fix_beads_dependency_timestamps.py --jsonl-path /path/to/issues.jsonl
```

### How it works

1. Scans all issues in `issues.jsonl`
2. Finds dependency records with `created_at="0001-01-01T00:00:00Z"`
3. Replaces the zero timestamp with the parent issue's `updated_at` timestamp (or current time as fallback)
4. Writes the corrected records back to the file

### Example output

```
Fixing issue butlers-2bq.7:
  - Dependency butlers-2bq.7 -> butlers-886 (type: blocks): 0001-01-01T00:00:00Z -> 2026-02-15T02:15:24.686020053+08:00

Summary: scanned 746 issues, modified 9 issues, fixed 9 dependencies
```

## Maintainer command index

The sections above carry the full procedures for the highest-risk bootstrap,
restore, development, and merge paths. The commands below are also direct
maintainer or operator entry points. Their entries name the purpose and boundary;
read the script's own usage text and the linked runbook before running a command
that changes infrastructure or historical data.
Scripts whose only callers are Docker, Compose, or CI plumbing are intentionally
omitted from this human command index; they are not maintainer entry points.

### Quality and review guards

| Script | Purpose and invocation boundary |
| --- | --- |
| [`check-no-em-dashes.py`](check-no-em-dashes.py) | Ratchets prohibited em dashes in doctrine and roster prose; frontend user-facing copy is enforced separately by ESLint. Use `make check-em-dashes` or the script while editing the scanned prose. |
| [`check_archived_requirements_landed.py`](check_archived_requirements_landed.py) | Confirms archived OpenSpec requirements reached their canonical specs; run as the archived-requirements CI guard. |
| [`check_cited_requirements_resolve.py`](check_cited_requirements_resolve.py) | Confirms requirement IDs cited by tests resolve to a live or active definition; run as the cited-requirements CI guard. |
| [`check_countable_tasks.py`](check_countable_tasks.py) | Fails when an unarchived change's tasks cannot be counted by the archive gate; use `make check-countable-tasks` before archival work. |
| [`check_duplicate_toplevel_names.py`](check_duplicate_toplevel_names.py) | Finds module-level Python definitions that would silently shadow each other after a merge; use `make check-duplicate-names` as the local or CI duplicate-name guard. |
| [`check_for_update_joins.py`](check_for_update_joins.py) | Statically rejects `FOR UPDATE` on nullable outer-join sides; use `make check-for-update-joins` after SQL query changes. |
| [`check_ci_test_shards.py`](check_ci_test_shards.py) | Verifies checked-in backend CI test-shard manifests cover the current unit and integration selections; run `make check-ci-test-shards` when changing the CI test lanes or manifests. |
| [`check_spec_overwrites.py`](check_spec_overwrites.py) | Compares active OpenSpec MODIFIED blocks against current canonical bodies; run `make check-spec-overwrites` before archival work. |
| [`extract-frontend-copy.py`](extract-frontend-copy.py) | Regenerates the checked-in frontend copy inventory; run it only when the inventory guard reports the generated file stale. |
| [`lint_decision_beads.py`](lint_decision_beads.py) | Validates the structured decision-bead convention; run through the `lint-decision-beads` Make targets when working that workflow. |
| [`pytest_gate.py`](pytest_gate.py) | Records a positive pytest completion receipt and classifies it as PASS, FAILED, or UNKNOWN; use its `run` and `verdict` subcommands through the quality-gate recipes rather than grepping a log. |
| [`session_link_guard.py`](session_link_guard.py) | Scans supplied PR metadata, commit ranges, and review-comment data for prohibited tool-session links; run `make check-session-links` before pushing, while CI and PR review tooling provide the broader scoped inputs. |
| [`reap_orphaned_testcontainers.py`](reap_orphaned_testcontainers.py) | Reports Docker testcontainers left behind by dead pytest runs; use `--reap` only after reviewing the reported orphan candidates. |

### Development, release, and recovery commands

| Script | Purpose and invocation boundary |
| --- | --- |
| [`compose.sh`](compose.sh) | Supported Compose launcher for the local stack and protected restore-drill variants; use it instead of composing protected fragments directly. |
| [`setup_worktree.sh`](setup_worktree.sh) | Prepares a newly created worktree with its machine-local pointers and cache links; run from that worktree. |
| [`bump_version.py`](bump_version.py) | Updates the project version in `pyproject.toml`; use only as part of a reviewed release preparation. |
| [`release_tag.py`](release_tag.py) | Creates the annotated tag for the current project version locally; pushing the tag remains a separate release action. |
| [`staging.py`](staging.py) | Holds an E2E ecosystem open for interactive load testing; use only in a local environment that meets the E2E prerequisites. |
| [`egress-firewall.sh`](egress-firewall.sh) | Applies or removes the host Docker egress policy; run only with the required host-administrator authority. |
| [`pg_restore.sh`](pg_restore.sh) | Restores a PostgreSQL backup into the restore-drill target; follow the backup/restore runbook rather than treating it as a general production restore command. |
| [`pg_verify_restore.sh`](pg_verify_restore.sh) | Verifies schema and data integrity in a restored database; run after the restore-drill restore step. |

### Historical data repairs

These are one-off, scoped maintenance commands. They change historical state;
review each script's selection criteria and dry-run or scope controls before
applying it to a database.

| Script | Repair scope |
| --- | --- |
| [`backfill_batch_sender_identities.py`](backfill_batch_sender_identities.py) | Recovers per-sender identities from historical batch message inbox rows. |
| [`backfill_email_identity_facts.py`](backfill_email_identity_facts.py) | Adds unambiguous existing-entity `has-email` facts for historical email senders. |
| [`backfill_entity_fact_observed_at.py`](backfill_entity_fact_observed_at.py) | Fills missing `observed_at` values on historical relationship facts. |
| [`backfill_point_event_entity_id.py`](backfill_point_event_entity_id.py) | Links historical owner-only chronicler point events to the owner entity. |
| [`backfill_tombstone_heartbeat_episodes.py`](backfill_tombstone_heartbeat_episodes.py) | Tombstones historical chronicler episodes generated by butler-internal heartbeats. |
| [`backfill_transitory_entities.py`](backfill_transitory_entities.py) | Creates and links transitory entities for historical string-anchored facts. |
| [`cleanup_bulk_email_identity_proposals.py`](cleanup_bulk_email_identity_proposals.py) | Retracts email-identity proposals created for bulk or automated senders. |
| [`dedupe_orphan_contacts.py`](dedupe_orphan_contacts.py) | Deduplicates orphan contact rows that share one entity. |
| [`migrate_blobs_to_s3.py`](migrate_blobs_to_s3.py) | Moves scoped local attachment blobs to the configured S3-compatible backend. |
| [`migrate_fact_subjects.py`](migrate_fact_subjects.py) | Normalizes historical fact subjects and backfills their entity links. |
| [`reconcile_whatsapp_entities.py`](reconcile_whatsapp_entities.py) | Runs the content-blind WhatsApp entity reconciliation maintenance pass. |
| [`retract_digest_measurements.py`](retract_digest_measurements.py) | Retracts measurement-weight facts created from butler-generated digest or briefing text. |

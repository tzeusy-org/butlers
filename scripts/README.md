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

## compose.sh

Launches the Docker Compose stack and configures its Tailscale Serve mappings.

### Tailscale Serve data-plane probe

`tailscale_serve_probe.py` performs one read-only HTTPS GET against the
configured health route.  It uses strict certificate-chain, hostname, and
expiry validation, requires HTTP 200 with top-level JSON `{"status":"ok"}`,
and retries timeouts and other transient transport failures with fixed bounds.
Its transport is intentionally injectable for tests.

The Compose launcher validates that Serve status is readable, well-formed, and
contains the expected mappings before lifecycle startup.  These failure classes
remain distinct: `status-unreadable`, `status-malformed`, and `mapping-missing`.
After the stack and egress firewall are up, it runs the data-plane probe only
when `TAILSCALE_SERVE_PROBE_COMMAND` is set and
`TAILSCALE_SERVE_PROBE_CONTEXT=off-host`.  The context label is only an operator
policy gate: the probe derives its actual Tailscale DNS identity on the executor,
rejects the target host, and emits the identity attestation that the launcher
requires before accepting success. Both target and executor identities must be
qualified `.ts.net` names; a local or single-label name cannot be off-host
evidence. The command is split into argv (no shell evaluation), its first
executable must resolve locally before Serve or Compose lifecycle work, and it
receives `--url`, `--timeout`, `--retries`, and `--retry-delay` arguments.
Timeout is finite and at most 30 seconds, retries are limited to three, retry
delay is finite and at most five seconds, and the launcher wraps the whole
executor (including DNS, SSH, and authentication setup) in a derived outer
deadline. Remote/runtime executor failures remain post-start probe failures.
Executor stdout, stderr, and configured argv are never echoed; the launcher
emits only stable, content-blind outcome classes. An operator-supplied executor
can therefore run `python3 scripts/tailscale_serve_probe.py` from an independent
tailnet client. Without a
configured executor, the launcher reports `control-plane mappings only` instead
of claiming data-plane readiness.  With an explicitly configured executor, a
usable `Self.DNSName` is required before any Serve or Compose lifecycle mutation;
an absent or malformed target fails as `data-plane target-unavailable` rather
than silently skipping the HTTPS check.  Probe failures classify as `cert-invalid`,
`route-404`, `timeout`, or `executor-timeout` with sanitized, actionable guidance
and never alter Serve state.

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

## merge_pr_exact_base.py

Performs the final REST squash merge for a reviewed pull request without
silently accepting a target-branch retarget or base-branch advance. GitHub's
REST merge endpoint can condition only on the pull request head SHA; it cannot
atomically require the reviewed target ref or base SHA. This helper therefore:

1. confirms the currently open PR still has the final reviewed head, target
   branch name, and live target-branch SHA,
2. keeps the supported head-SHA pin on the REST squash request, and
3. re-reads the merged PR's retained target branch name through GraphQL, and
4. verifies that the resulting squash commit has exactly the reviewed base as
   its sole parent and the same immutable result tree as the reviewed head.

It is the sole final merge route, not a substitute for terminal hosted CI,
independent review, or resolved review threads. Every hosted check must be
terminal green before invoking it; branch-protection required-check settings do
not relax that gate. Do not use bare REST merge requests, `gh pr merge`, or
automatic merge. Capture `headRefOid`, `baseRefName`, and the *live target
branch tip* from the same final revalidation. Do not use a PR's `baseRefOid` as
the expected base: it can remain stale while the target branch has advanced.

```bash
HEAD_SHA=$(gh pr view "$PR" --json headRefOid --jq .headRefOid)
BASE_REF=$(gh pr view "$PR" --json baseRefName --jq .baseRefName)
BASE_SHA=$(gh api "repos/Tzeusy/butlers/git/ref/heads/$BASE_REF" --jq .object.sha)
```

Then pass all three exact values to the helper:

```bash
python3 scripts/merge_pr_exact_base.py \
  --pr "$PR" \
  --expected-head "$HEAD_SHA" \
  --expected-base-ref "$BASE_REF" \
  --expected-base "$BASE_SHA"
```

Only `merged-exact-base` (exit `0`) permits the coordinator to close the source
Bead. A `premerge-head-drift`, `premerge-base-ref-drift`, or
`premerge-base-drift` result sends no merge request; rebase onto current
`origin/main`, then repeat the full exact-head review and CI gates.
`postmerge-base-drift` means GitHub merged the SHA-pinned head on a newer base
during the unavoidable API race: leave the source Bead open and record/run the
required post-merge race audit instead of treating it as exact-current-base
evidence. `postmerge-base-ref-drift` means the post-merge GraphQL lookup either
could not verify the retained target ref or found a different ref name. It is
also exit `4` and blocks closure even if the squash commit's sole parent still
matches the reviewed base SHA.
`postmerge-patch-drift` is likewise exit `4`: it means the helper could not
obtain immutable commit-tree evidence for both commits, or their result trees
differed. With the verified sole parent equal to the reviewed base, matching
tree IDs are an authoritative proof that the landed squash has the reviewed
net patch, including binary, rename, and empty changes. The JSON audit records
`expected_patch_tree_sha`, `landed_patch_tree_sha`, and
`patch_identity_matches`; no nonmatching or unavailable evidence permits
source-Bead closure.
`postmerge-unexpected-squash-parent-shape` is also exit `4`: GitHub has already
merged the PR, but the result did not have exactly one parent. Its audit retains
the parent evidence; leave the source Bead open and run the documented
post-merge audit/investigation rather than treating it as exact-base evidence.

### Target-branch health gate

Before it sends the merge request, the helper reads the target branch's own
post-merge detectors for the exact base SHA it is about to merge onto, via
`main_health_gate.py`. Two new outcomes come from that gate and neither issues a
merge request:

- `premerge-target-branch-red` (exit `6`) means a push-triggered workflow on the
  target branch concluded red for that base SHA. Halt the batch and repair the
  branch; do not merge the next PR onto it.
- `premerge-target-branch-health-unknown` (exit `7`) means there is no
  trustworthy verdict yet: the run has not been created, is still in flight, or
  was cancelled. Wait for it to settle (`main_health_gate.py --wait-seconds`)
  and repeat, rather than reading the absence as green.

`--acknowledge-target-red <workflow-filename>` (repeatable) merges despite one
named workflow being red, which is how the fix for a red target branch lands. It
is deliberately not a blanket override: any *other* red still halts the batch.

## main_health_gate.py

Decides whether `main` is healthy enough for the next merge in a batch, and is
the reader the post-merge detectors never had (bu-vul8u). A pull request's CI can
only see its own branch, so two PRs can each be green and still collide once both
have landed. When that happened, the "Migration Chain Integrity (main)" workflow
went red on the merged tree exactly as designed, and several more PRs were merged
onto that red main because nothing consumed the result.

It answers `proceed` / `wait` / `halt` for one SHA, from two halves.

**Hosted half.** Polls the push-triggered workflow runs for that exact SHA. Four
situations look like the same absence on the wire and must not be conflated:

| observed | meaning | action |
| --- | --- | --- |
| no run, and the merged commit touched no path the workflow's `paths:` filter covers | legitimately excluded | proceed |
| no run, and the commit did touch a covered path | webhook lag | wait |
| run exists, `conclusion` is the empty string (not null) | still in flight | wait |
| run exists, `conclusion` is `cancelled` | UNKNOWN | wait, never green |

Only workflows that can earn a per-SHA verdict are polled. A workflow whose
concurrency group is keyed on the branch ref with `cancel-in-progress` cannot
settle mid-batch, because every push to main cancels the previous run, so `ci.yml`
is excluded and the local half covers it instead. A tag-only push workflow such as
`release.yml` never fires on a branch push and is excluded for the same reason.
Runs are looked up by workflow FILENAME (`migration-chain-main.yml`), never by
display name.

**Local half.** Runs the repository's repo-wide guards against a checkout of the
merged tree. This is where a green verdict actually comes from during a batch.
The guards are enumerated from the tree under test (its own workflow definitions
and `Makefile` recipes), not from a list frozen in this script: a new repo-wide
guard is absent from every branch cut before it, and an absent check is invisible
to both a fail-scan and a required-name list. The sweep also fails if any guard
leaves the tree dirty, which is how the frontend copy inventory reports staleness.

```bash
# between merges, against a dedicated scratch worktree (never the repo root)
python3 scripts/main_health_gate.py \
  --tree /path/to/scratch-worktree --sync-tree-to origin/main --wait-seconds 300

# hosted verdicts only, no local checkout needed
python3 scripts/main_health_gate.py --sha "$SHA" --no-local-guards
```

Exit codes: `0` proceed, `1` definitively red, `2` unresolved (wait and re-run),
`3` usage or transport error. An exhausted wait budget halts, but still reports
`2`: it halts for want of evidence, not because something failed.

`--sync-tree-to` refuses to reset anything that is not a linked git worktree, so
a mistyped path cannot touch the repository root.

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

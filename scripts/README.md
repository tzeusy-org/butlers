# Scripts

Entry points that CI, Make, Helm, Compose, git hooks and live runbooks call. The
[script index](#script-index) names every script and its caller.

## repair_attention_ledger_metadata.py

Bounded operator for explicitly selected historical attention-ledger JSONB
strings. Default mode produces a private read-only plan; exact-plan apply uses
the existing normal role, a serializable compare-and-swap transaction, private
beforeimages and independent committed readback. Live planning and owner-approved
data apply are separate operational allocations. See the
[metadata repair runbook](../docs/operations/attention-ledger-metadata-repair.md).

## init-db.sql

Privileged PostgreSQL bootstrap script to run **before** the first Alembic
migration on a fresh database. It must be executed by a privileged cluster
superuser. Supply the normal connecting/migration user separately through the
`butlers.connecting_user` GUC; it must not be the active bootstrap identity.
It is safe to re-run later if the managed schema/role surface expands. Online
entrypoints verify current prerequisite state before side effects, including dev;
they do not invoke bootstrap or demand a blind rerun before every migration. See
[reviewed bootstrap and existing core195 repair](../docs/data_and_storage/reviewed-bootstrap.md).

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

## Script index

`scripts/` keeps only files that CI, Make, Helm, Compose, a Dockerfile, a git hook, or a
live runbook or spec calls. This table lists every script with the caller that keeps it
alive; `tests/scripts/test_scripts_readme_index.py` fails when a script is added without
a row here. A script whose only caller is a human is named as such, with the document
that tells the human to run it. Add a row in the same change that adds a script, and
delete the script (not just the row) when its last caller goes away.

Read the script's own usage text and the linked runbook before running a command that
changes infrastructure or historical data.

### Quality and review guards

| Script | Caller | Purpose |
| --- | --- | --- |
| [`check-no-em-dashes.py`](check-no-em-dashes.py) | `ci.yml`, `make check-em-dashes`, `pre_push.py`, frontend copy lint | Ratchets prohibited em dashes in doctrine and roster prose; frontend copy is enforced separately by ESLint. |
| [`check_archived_requirements_landed.py`](check_archived_requirements_landed.py) | `ci.yml`, Make, `pre_push.py` | Confirms archived OpenSpec requirements reached their canonical specs, honouring `archived-capability-homes.json`. |
| [`check_cited_requirements_resolve.py`](check_cited_requirements_resolve.py) | `ci.yml`, Make, `pre_push.py` | Confirms requirement IDs cited by tests resolve to a live or active definition. |
| [`check_countable_tasks.py`](check_countable_tasks.py) | `ci.yml`, `make check-countable-tasks`, `pre_push.py` | Fails when an unarchived change's tasks cannot be counted by the archive gate. |
| [`check_duplicate_toplevel_names.py`](check_duplicate_toplevel_names.py) | `ci.yml`, `make check-duplicate-names`, `pre_push.py` | Finds module-level Python definitions that would silently shadow each other after a merge. |
| [`check_for_update_joins.py`](check_for_update_joins.py) | `make check-for-update-joins`, `pre_push.py` | Statically rejects `FOR UPDATE` on nullable outer-join sides. |
| [`check_owner_emails.py`](check_owner_emails.py) | `ci.yml`, Make, `pre_push.py` | Fails when a tracked file contains a blocked deployment-owner email address. |
| [`check_requirement_trace_lines.py`](check_requirement_trace_lines.py) | `ci.yml`, `make check-requirement-trace` | Requires ID, Source and Scope lines on ADDED/MODIFIED requirement blocks; today's debt is frozen in `requirement-trace-baseline.json`. |
| [`check_spec_overwrites.py`](check_spec_overwrites.py) | `ci.yml`, `make check-spec-overwrites`, `pre_push.py` | Compares active OpenSpec MODIFIED blocks against current canonical bodies. |
| [`check_pyright_baseline.py`](check_pyright_baseline.py) | `pyright_runtime.py`, `make typecheck` | Strict diagnostic JSON and shrinking error-Counter comparison. |
| [`check_test_budget.py`](check_test_budget.py) | Make, `ci.yml` preflight | Per-lane collected-test budget ratchet against `test-budget-baseline.json`. |
| [`export_openapi.py`](export_openapi.py) | `frontend/scripts/generate-api-types.mjs`, via `npm run generate:api` / `npm run check:api` | Exports the static FastAPI OpenAPI contract without a live database for generated frontend API types. |
| [`extract-frontend-copy.py`](extract-frontend-copy.py) | Manual, on demand | Writes the bounded frontend copy report to stdout and diagnostics to stderr; redirect to ignored `.tmp/` for a local copy. |
| [`frontend_guards.py`](frontend_guards.py) | Make, `pre_push.py` | Runs the frontend guard steps locally in the order CI runs them. |
| [`lint_tools.py`](lint_tools.py) | `make install-lint-tools`, `make lint`, CI | Installs pinned gitleaks and actionlint, scans source and workflows, and self-tests the detectors and allowances. |
| [`lint_backlog_graph.py`](lint_backlog_graph.py) | Make | Reports dispatch-blocking Beads graph states without changing any bead. |
| [`lint_decision_beads.py`](lint_decision_beads.py) | Local Make/CLI | Validates the structured decision-bead convention. |
| [`pre_push.py`](pre_push.py) | `.githooks/*`, Make | Local gate that runs the guards before a commit or push. |
| [`pre_push_sandbox.py`](pre_push_sandbox.py) | `pre_push.py` | Denies network and database socket operations before a read-only guard starts (Linux). |
| [`pytest_gate.py`](pytest_gate.py) | Make quality-gate recipes, watchdog profile | Records a positive pytest completion receipt and classifies it as PASS, FAILED, or UNKNOWN. |
| [`reap_orphaned_testcontainers.py`](reap_orphaned_testcontainers.py) | root `conftest.py` | Reports Docker testcontainers left behind by dead pytest runs; `--reap` only after reviewing the candidates. |
| [`session_link_guard.py`](session_link_guard.py) | `ci.yml`, Make, `pre_push.py`, `combine_review_comment_sources.py` | Scans PR metadata, commit ranges and review comments for prohibited tool-session links. |

### CI plumbing

These are called by workflows, composite actions, or each other. They are not
maintainer entry points. Retiring the orchestration among them belongs to the CI
pipeline beads (`bu-29wkwj.2`, `bu-29wkwj.3`), not to this index.

| Script | Caller | Purpose |
| --- | --- | --- |
| [`check_ci_coverage.py`](check_ci_coverage.py) | `nightly.yml` | Binds and validates the complete same-run coverage population before reporting. |
| [`check_ci_test_shards.py`](check_ci_test_shards.py) | its own contract tests | Legacy file-level shard runner; retained outside this retirement scope. Current hosted lanes use `ci_lanes.py`. |
| [`ci_affected.py`](ci_affected.py) | `ci.yml` | Scoped collection, execution and reconciliation for the PR affected-test lane. |
| [`ci_affected_collector.py`](ci_affected_collector.py) | `ci_affected.py` (pytest plugin) | Source-owned collector for that scoped run. |
| [`ci_evidence.py`](ci_evidence.py) | `ci_affected.py`, `ci_affected_collector.py` | Shared identity and evidence helpers for the scoped CI lane. |
| [`ci_group_count.py`](ci_group_count.py) | `ci_lanes.py` (pytest plugin) | Records each backend group's actual collection count and test durations. |
| [`ci_lanes.py`](ci_lanes.py) | `ci.yml`, `nightly.yml` | Runs pytest-split backend groups, reconciles their counts against fresh collection, and merges measured durations. |
| [`pyright_runtime.py`](pyright_runtime.py) | `make typecheck`, `make prepare-typecheck`, `make update-type-baseline` | Pinned offline checker supervision, immutable-public bootstrap and atomic baseline shrink. |
| [`ci_environment.py`](ci_environment.py) | `ci.yml`, `ci-python-environment` action, watchdog workflow | Advisory CI environment cache with mandatory frozen repair on every restore. |
| [`ci_inventory_collector.py`](ci_inventory_collector.py) | `ci_partition.py` (pytest plugin) | Source-owned collector that records the test inventory used for sharding. |
| [`ci_latency.py`](ci_latency.py) | `ci-latency.yml` | Bounded, read-only Actions timestamp report; never a required merge gate. |
| [`ci_partition.py`](ci_partition.py) | `ci_watchdog_diagnostics.py` (imports) | Legacy recorded-inventory partition helpers; current hosted lanes use `ci_lanes.py`. |
| [`ci_route.py`](ci_route.py) | `ci.yml` | Conservative classification of a change into the lanes it needs. |
| [`ci_shard_comparison.py`](ci_shard_comparison.py) | none; documented in `docs/operations/ci-shard-overhead.md`, imported by one test | Compares genuine shard observations. Kept for `bu-29wkwj.3`, which owns the observer and comparison scripts. |
| [`ci_shard_observer.py`](ci_shard_observer.py) | `check_ci_test_shards.py`, `ci_partition.py`, `ci_watchdog_diagnostics.py` (imports) | Private testcase identities and completion times for CI shards. |
| [`ci_test_images.py`](ci_test_images.py) | root `conftest.py`, `test_owner_auth_browser.py` | Preloads the test container images by public immutable content, without credentials. |
| [`ci_test_plan.py`](ci_test_plan.py) | none; imported only by its own test | Wrapper that decides scoped versus full mode for the affected lane. Kept for `bu-29wkwj.3`, which deletes CI routing. |
| [`ci_vitest.py`](ci_vitest.py) | `ci.yml` | Executes the locked Vitest shards and retains actual-item evidence. |
| [`ci_vitest_reporter.mjs`](ci_vitest_reporter.mjs) | `ci_vitest.py` | Vitest reporter. |
| [`ci_watchdog_diagnostics.py`](ci_watchdog_diagnostics.py) | `ci-shard-watchdog-capture` action, watchdog workflow | Bounded incomplete CI observations, separate from every execution verifier. |
| [`combine_review_comment_sources.py`](combine_review_comment_sources.py) | `ci.yml` | Flattens raw GitHub review-comment dumps into the session-link guard's input shape. |
| [`fetch_pr_metadata.py`](fetch_pr_metadata.py) | `ci.yml` | Producer of validated PR title/body guard inputs. |
| [`install_ci_uv.py`](install_ci_uv.py) | `ci.yml`, `nightly.yml`, `ci-python-environment` action, watchdog workflow | Installs the pinned `uv` in CI. |
| [`summarize_junit_durations.py`](summarize_junit_durations.py) | `ci.yml`, `nightly.yml` | Summarizes JUnit durations for the weight pipeline. |
| [`ci_branch_hygiene.py`](ci_branch_hygiene.py) | the live runbook [stale branch hygiene](../docs/operations/stale-branch-hygiene.md) (human) | Inventories, plans and verifies recovery for stale remote branches. Kept: `AGENTS.md` points agents at the runbook. |
| [`test_owner_auth_browser.py`](test_owner_auth_browser.py) | `frontend/tests/owner-auth/README.md` (human), `ci_test_images.py` | Native HTTPS owner-auth evidence against a disposable PostgreSQL instance. |

### Development, release and runtime

| Script | Caller | Purpose |
| --- | --- | --- |
| [`compose.sh`](compose.sh) | developers, `butlers deploy`, Dockerfile, Helm values | Supported Compose launcher for the local stack and protected restore-drill variants. |
| [`base-image-input-fingerprint.sh`](base-image-input-fingerprint.sh) | `compose.sh`, `k8s/build-push.sh`, `nightly.yml` | Fingerprints the base image build inputs. |
| [`generate_runtime_cli_sandbox_manifest.py`](generate_runtime_cli_sandbox_manifest.py) | `Dockerfile.base`, `compose.sh`, `k8s/build-push.sh`, `nightly.yml` | Generates the runtime CLI sandbox input manifest. |
| [`render_connector_catalog.py`](render_connector_catalog.py) | developers; `tests/contracts/test_connector_catalog.py::test_generated_recovery_projection_matches_catalog_and_refuses_stale_output` | Regenerates the public connector recovery projection with `python3 scripts/render_connector_catalog.py`; `--check` refuses stale generated TypeScript. |
| [`runtime_cli_sandbox_init.c`](runtime_cli_sandbox_init.c) | `Dockerfile.base` | Source of the sandbox init binary. |
| [`dev_entrypoint.sh`](dev_entrypoint.sh) | `docker-compose.yml`, Helm templates | Container entrypoint for dev services. |
| [`dashboard_proxy_peer.py`](dashboard_proxy_peer.py) | `compose.sh` | Measures the host-published API's TCP peer for the dashboard-proxy readiness check. |
| [`oauth_gate.py`](oauth_gate.py) | `docker-compose.yml`, Helm helpers | Polls the database for a Google refresh token and exits 0 when found. |
| [`site-env.sh`](site-env.sh) | `.envrc`, `k8s/*.sh` | Loads site-specific environment. |
| [`k8s/`](k8s/) | developers, `make` and Helm flows | Build, push and deploy helpers for the dev cluster. |
| [`setup_worktree.sh`](setup_worktree.sh) | developers (`AGENTS.md`) | Prepares a newly created worktree with its machine-local pointers and cache links. |
| [`setup_main_ruleset.sh`](setup_main_ruleset.sh) | maintainers (`AGENTS.md`) | Installs the `main-merge-queue` ruleset; see the merge queue section above. |
| [`bump_version.py`](bump_version.py) | Make | Updates the project version in `pyproject.toml` during release preparation. |
| [`release_tag.py`](release_tag.py) | Make | Creates the annotated tag for the current version locally; pushing stays a separate action. |
| [`egress-firewall.sh`](egress-firewall.sh) | `compose.sh`, `docker-compose.yml` | Applies or removes the host Docker egress policy; needs host-administrator authority. |
| [`staging.py`](staging.py) | developers (human only) | Holds an E2E ecosystem open for interactive load testing. |
| [`clear-processes.sh`](clear-processes.sh) | developers (human only) | Kills processes on the expected local dev ports. |
| [`cleanup_logs.sh`](cleanup_logs.sh) | developers (human only) | Removes old log files under `logs/`. |

### Database, backup and restore

| Script | Caller | Purpose |
| --- | --- | --- |
| [`init-db.sql`](init-db.sql) | `docker-compose.yml`, operators, migrations tests | Privileged bootstrap run before the first migration. |
| [`pg_restore.sh`](pg_restore.sh) | operators; `pg_verify_restore.sh` | Restores a PostgreSQL backup into the restore-drill target. |
| [`pg_verify_restore.sh`](pg_verify_restore.sh) | `pg_restore.sh` flow | Verifies schema and data integrity in a restored database. |
| [`provision_restore_drill_executor.sh`](provision_restore_drill_executor.sh) | operators, per [backup and restore](../docs/operations/backup-restore.md) | Enables the `restore_drill_executor` login. |
| [`restore-drill-firewall.sh`](restore-drill-firewall.sh) | `docker-compose.yml`, `install_restore_drill_firewall_wrapper.sh` | Reviewed source for the root-owned firewall wrapper. |
| [`restore-drill-firewall.sudoers`](restore-drill-firewall.sudoers) | root-controlled deployment setup | Sudoers template for the two fixed wrapper forms. |
| [`install_restore_drill_firewall_wrapper.sh`](install_restore_drill_firewall_wrapper.sh) | root-controlled deployment setup, `core/deploy.py` | Installs the immutable wrapper target. |
| [`restore-drill-compose-inspect.sh`](restore-drill-compose-inspect.sh) | operators | Read-only `config`, `ps` and `logs` for the protected topology. |
| [`restore_drill_tcp_proxy.py`](restore_drill_tcp_proxy.py) | `docker-compose.restore-drill.yml` | Uncredentialed TCP relay for the restore-drill executor's internal network. |

### Operator repairs kept on purpose

These change historical state. Each is named by a live runbook, spec, or active change,
which is why it survives the deletion of the other one-shot repairs. Review its selection
criteria and dry-run controls before applying it to a database.

| Script | Caller | Why it stays |
| --- | --- | --- |
| [`backfill_fleet_cases.py`](backfill_fleet_cases.py) | operators, per `REQ-fleet-case-file-011` in the [fleet-case-file spec](../openspec/specs/fleet-case-file/spec.md) | Previews or backfills resolved owner-condition episodes as closed historical cases using the Switchboard writer role; never scheduled. |
| [`migrate_blobs_to_s3.py`](migrate_blobs_to_s3.py) | operators, per the `s3-blob-storage` spec and RFC 0016 | Moves scoped local attachment blobs to the S3-compatible backend. |
| [`recover_chronicler_day_close_cache.py`](recover_chronicler_day_close_cache.py) | operators, per [`chronicler-day-close-recovery.md`](../docs/operations/chronicler-day-close-recovery.md), after separate operational authorization | Plans bounded day-close cache recovery; applying the reviewed plan requires separate authorization. |
| [`repair_attention_ledger_metadata.py`](repair_attention_ledger_metadata.py) | operators, per [metadata repair](../docs/operations/attention-ledger-metadata-repair.md), after separate live allocation | Plans exact bounded UUID selections; owner-approved apply repairs one encoded object layer with CAS and committed readback. |
| [`reconcile_whatsapp_entities.py`](reconcile_whatsapp_entities.py) | operators, per the active `repair-whatsapp-identity-reconciliation` change (tasks 6.3 and 6.4) | Content-blind WhatsApp entity reconciliation pass. Delete when that change archives. |
| [`scrub_filtered_event_previews.py`](scrub_filtered_event_previews.py) | operators, per [connectors overview](../docs/connectors/overview.md) | Scrubs preview text from filtered-event buffers; imported by two connector tests. |

## ci_branch_hygiene.py and fetch_pr_metadata.py

`uv run --no-sync python scripts/ci_branch_hygiene.py` inventories, plans,
independently bundles/verifies recovery, and observes complete CI push windows.
Live apply/remote restoration/setting reversal require explicit execution and
an exact separately approved manifest; default calls cannot mutate remote state.
See [the operational procedure](../docs/operations/stale-branch-hygiene.md).

`fetch_pr_metadata.py` is the mandatory guard producer for validated current
fixed-PR title/body. It writes runner-temp inputs and digests privately, removes
stale title/body/digest-receipt outputs before reading and on completed failure,
and emits only categorical diagnostics. Subprocess failures receive at most three
30-second attempts with 2/5-second backoffs under one 97-second monotonic budget;
a successful response is validated once, and invalid JSON/identity or local I/O
errors never retry or use cached metadata. The workflow independently uses GNU
`timeout` (TERM at 100 seconds, KILL after 3 more) and its surviving step shell
attempts cleanup of all three outputs on nonzero exit. This nominal 103-second envelope
is not measured CI latency or a cleanup guarantee under runner/kernel loss;
incomplete execution remains UNKNOWN/non-pass. The GNU-dependent group control
fails closed on unsupported timeout implementations. The scanner's
`--redact-findings` CLI mode retains detection and omits matched text; its private
formatter and JSON interfaces remain available for existing consumers.

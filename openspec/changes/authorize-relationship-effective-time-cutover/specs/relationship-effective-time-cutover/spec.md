## ADDED Requirements

### Requirement: Signed content-blind cutover receipt
The Relationship effective-time cutover SHALL be authorized only by a canonical signed receipt that
binds one authorization id, one purpose, one Compose environment and project, the resolved Compose
invocation and its redacted configuration digest, the exact target Git SHA, the immutable target
image id, the target `roster/` tree id, the complete instance set, the mutator-inventory digest, the
digest of the test receipt the signing wrapper produced by running the required tests itself, the
database target, the active fence identity, the issue time, and an expiry at most 15 minutes after
issue.

ID: REQ-relationship-effective-time-cutover-001

The receipt SHALL be UTF-8 JSON with the schema tag
`butlers.relationship-temporal-cutover-receipt/v1`. Its signed bytes SHALL be the RFC 8785 (JCS)
canonical encoding of the whole document without the `signature` member. The document SHALL reject
unknown members, duplicate members, non-canonical base64url, non-UTC or sub-second timestamps, and
any value outside its declared vocabulary. `purpose` SHALL be exactly `cutover` or
`rollback_before_first_temporal_write`; a receipt for one purpose MUST NOT satisfy the other.

The receipt, the verifier's output, and every log line produced by the cutover path SHALL be
content-blind. They MAY contain stable error codes, counts, digests, Git SHAs, Git tree ids, image
ids, container ids, Compose service names, timestamps, the database name and OID, the cluster system
identifier, and the fence and authorization ids. They MUST NOT contain fact subjects, predicates,
objects, contact values, entity names, row ids, SQL text, container environments or labels other
than the projected `GIT_SHA`, credentials, key material, or host paths outside the fixed cutover
directories.

#### Scenario: Receipt binds one exact cutover

- **WHEN** a receipt is verified for the rel036 upgrade
- **THEN** its target Git SHA, mutator-inventory digest, database target, and fence id MUST each
  equal the value the migration recomputes from its own image, mounted files, database session, and
  fence file
- **AND** its Compose-configuration, instance-set, image, roster-tree, and test-receipt claims MUST
  be accepted only because the signing wrapper collected them itself in the same fenced invocation,
  including running the required tests itself
- **AND** any single mismatch MUST abort before DDL with the stable code for that field

#### Scenario: Rollback receipt cannot authorize cutover

- **WHEN** a correctly signed `rollback_before_first_temporal_write` receipt is supplied to the
  cutover upgrade
- **THEN** the upgrade MUST abort `receipt_purpose_mismatch` with no schema change

#### Scenario: Evidence stays content-blind

- **WHEN** the verifier, wrapper, or migration logs any success or failure
- **THEN** the output MUST contain only the permitted evidence categories
- **AND** it MUST NOT contain any fact, contact, entity, SQL, environment, or credential content

### Requirement: Dedicated root signer and custody
Receipts SHALL be signed only by a dedicated host-root Ed25519 key used for no other purpose, held by
a fixed root-owned wrapper that gathers its own evidence before signing. The verifying migration
MUST accept a receipt only from a root-owned, non-writable, non-symlink file and only under a key
present in a root-owned, non-writable verifier keyring.

ID: REQ-relationship-effective-time-cutover-002

The signing document SHALL live at
`/etc/butlers/relationship-temporal-cutover/signing-key.json` (regular file, `root:root`, mode
`0400`) and the verifier keyring at
`/etc/butlers/relationship-temporal-cutover/verifiers.json` (regular file, `root:root`, not group- or
world-writable). Both SHALL reuse the runtime-probe control document shapes (`version`, `alg`
`EdDSA`, `kid`, unpadded base64url key, `sign_from`/`sign_until`, `current`/`retiring`) but MUST use
a distinct `kid` namespace `rtc-*` and a distinct key. Receipts SHALL be written atomically to
`/var/lib/butlers/relationship-temporal-cutover/receipts/<authorization_id>.json`, `root:root`, mode
`0444`, in a root-owned directory that is not group- or world-writable.

The wrapper MUST sign only evidence it collected itself in the same invocation. It MUST NOT sign an
operator-supplied inventory, test receipt, `pytest_gate.py` log, Compose configuration, or digest,
it MUST NOT accept any of them as input, and it MUST NOT expose a generic "sign this document" verb.
Test evidence in particular SHALL come only from the wrapper running the fixed test node list itself
(see the mutator inventory and test receipt requirement). Provisioning
the key, installing the wrapper, and adding its sudoers rule are live host acts outside repository
authority; adopting this signer is an owner decision.

#### Scenario: Operator-authored receipt is refused

- **WHEN** a receipt file is owned by a non-root user, is group- or world-writable, or is a symlink
- **THEN** the migration MUST abort `receipt_custody_invalid` before reading its claims

#### Scenario: Forged or foreign-key receipt is refused

- **WHEN** the signature does not verify, the `kid` is not in the keyring, or the key is outside its
  `sign_from`/`sign_until` window
- **THEN** the migration MUST abort `receipt_signature_invalid` or `receipt_signer_unknown` before DDL

#### Scenario: Operator-produced test evidence is never signed

- **WHEN** an operator supplies, points to, or places a test receipt or `pytest_gate.py` log for the
  wrapper to use
- **THEN** the wrapper MUST ignore it, MUST run the fixed node list itself, and MUST bind only the
  receipt it produced in that invocation
- **AND** no wrapper verb MAY accept a test receipt, gate log, or digest argument

#### Scenario: Another key cannot stand in

- **WHEN** a receipt is signed with the runtime-probe control key or any key outside the `rtc-*`
  keyring
- **THEN** it MUST be refused as `receipt_signer_unknown`

### Requirement: Complete Docker Compose instance proof
The wrapper SHALL prove, from read-only Docker inspection of the named Compose project, that every
container of every database-credentialed service in the one authorized Compose invocation is
accounted for, that every such container runs its required image with the exact target `roster/`
tree mounted from the verified checkout where it mounts one, that no other container, image, or tree
is present, and that the set equals the service set of that invocation's resolved configuration at
the target SHA.

ID: REQ-relationship-effective-time-cutover-003

Relationship mutator code runs from two sources: `src/` baked into the `butlers-app` image, and
`roster/` bind-mounted read-only from the host checkout into `butlers-up`, `dashboard-api`,
`migrations`, and their hotreload variants. Image identity alone therefore MUST NOT count as code
identity.

**Supported invocations.** The live authorization SHALL name exactly one row of this table plus its
optional flags. The wrapper SHALL resolve that row at the target SHA from the launcher itself
(`DeployConfig` for `butlers deploy`; a non-mutating resolution mode of `scripts/compose.sh`) and
MUST abort `compose_invocation_mismatch` if the resolution differs from the row. File order is
significant.

| Row | Launcher | Compose files | Project | Env file | Profiles |
| --- | --- | --- | --- | --- | --- |
| `prod-deploy` | `butlers deploy` | `docker-compose.yml`, `docker-compose.restore-drill.yml` | `butlers` | `.env.prod` | exactly the authorized `DeployConfig.profiles`, default none |
| `prod-launcher` | `scripts/compose.sh --prod` with optional `--observability`, `--audio` | `docker-compose.yml`, `docker-compose.restore-drill.yml` | `butlers` | `.env.prod` | `dev`, plus `observability` and `audio` when flagged |
| `dev-launcher` | `scripts/compose.sh --no-hotreload` with optional `--with-restore-drill`, `--observability`, `--audio` | `docker-compose.yml`, plus `docker-compose.restore-drill.yml` when flagged | `butlers-dev` | `.env.dev` | `dev`, plus `observability` and `audio` when flagged |

The `hotreload` profile, `docker-compose.observability.yml`, `docker-compose.meeting-prep-evidence.yml`,
and any file or profile not in the named row are unsupported for a cutover.

**Configuration digest.** The receipt SHALL bind the row, the ordered file list, project, env-file
name, sorted profiles, and `compose_config_digest`: the SHA-256 of the JCS encoding of
`docker compose -f <files...> -p <project> --profile <p>... config --no-interpolate --format json`
run in the verified checkout. `--no-interpolate` keeps every `${...}` reference literal, so the digest
and the configuration read never contain credential values.

**Credentialed service rule.** A service in that resolved configuration SHALL be database-credentialed
when any of the following holds, and otherwise SHALL NOT be:

1. its `environment` has a key in `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
   `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`, `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`,
   `PGDATABASE`, `PGPASSFILE`, or `PGSERVICEFILE`;
2. its `environment` has a key matching `^[A-Z0-9_]*_DB_(HOST|PORT|USER|PASSWORD|PASSWORD_FILE)$`
   or starting with `RESTORE_DRILL_EXECUTOR_` or `RESTORE_DRILL_PROXY_`;
3. it mounts a `secrets` entry whose source name matches `(?i)(postgres|password|_db)`;
4. it attaches to the Compose network `db`; or
5. it declares any `env_file`, whose keys the non-interpolated configuration cannot show.

**Instance proof.** The proof SHALL require:

- the inventoried set is every container, in any state, whose `com.docker.compose.project` label
  equals the project, including one-off `compose run` containers;
- every inventoried container's `com.docker.compose.service` label names a service of the resolved
  configuration, and its `com.docker.compose.project.config_files` and
  `com.docker.compose.project.environment_file` labels equal the named row's files and env file;
- every credentialed service whose configured image reference is `butlers-app` or `butlers-app-*`
  runs the receipt's target image id (`docker image inspect` `.Id`), whose specifically projected
  `GIT_SHA` equals the target SHA;
- every other credentialed service (for example `backup-cron` on `postgres:17-alpine`) runs the image
  id its configured reference resolves to locally at inventory, recorded in the receipt;
- every `roster` mount source equals `<working_dir>/roster`, where `working_dir` is the project's
  `com.docker.compose.project.working_dir` label and is the canonical checkout (not a linked
  worktree);
- that checkout's `HEAD` equals the target SHA, `git rev-parse <sha>:roster` equals the receipt's
  roster tree id, and `git status --porcelain --untracked-files=all -- roster src alembic` is empty;
- every credentialed service with a `restart` policy has exactly its configured replica count of
  containers unless the authorization lists it as optional-absent; a credentialed service without a
  `restart` policy (one-shot, such as `migrations`) MAY have no container, and any it has are
  inventoried like the rest; and
- no hotreload service (`butlers-up-hotreload`, `dashboard-api-hotreload`) exists in any state.

Every credentialed container is quiesced and removed by the fence. Non-credentialed containers (for
example `frontend-dev` on `frontend` only) are recorded but need not stop. A process-health check, a
heartbeat, a boot ledger row, elapsed rollout time, or a single writer-symbol check MUST NOT satisfy
this proof.

#### Scenario: Credential rule is mechanical

- **WHEN** the resolved configuration is classified
- **THEN** `backup-cron` MUST be credentialed by rules 1 and 4 although its image is not
  `butlers-app`
- **AND** `restore-drill-executor` MUST be credentialed by rules 2 and 3 although it uses no
  `x-postgres-env` key, and `restore-drill-postgres-proxy` MUST be credentialed by rule 2
- **AND** a service that declares an `env_file` MUST be credentialed by rule 5

#### Scenario: Overlay services follow the named row

- **WHEN** the row includes `docker-compose.restore-drill.yml` and no `restore-drill-executor`
  container exists and the authorization does not list it as optional-absent
- **THEN** preparation MUST abort `instance_missing`
- **AND** when the row excludes that file but such a container exists in the project, preparation
  MUST abort `instance_extra`

#### Scenario: Containers from another invocation abort

- **WHEN** any inventoried container's config-files or env-file label differs from the named row, for
  example because it was started with `docker-compose.observability.yml` or the `hotreload` profile
- **THEN** preparation MUST abort `compose_invocation_mismatch`

#### Scenario: Configuration digest is content-blind and exact

- **WHEN** the same row is resolved twice at the same SHA
- **THEN** `compose_config_digest` MUST be identical
- **AND** the resolved document hashed MUST contain no interpolated value from any env file

#### Scenario: Mixed fleet aborts

- **WHEN** any credentialed `butlers-app` container uses an image id other than the target, any other
  credentialed container's image id differs from its resolved reference, or any `roster` mount
  resolves outside the verified checkout
- **THEN** preparation MUST abort `instance_mixed` or `instance_unknown_image` before quiescing or
  signing

#### Scenario: Extra or missing instance aborts

- **WHEN** a container of the project names a service absent from the resolved configuration, or a
  long-lived credentialed service lacks its replica count and is not optional-absent
- **THEN** preparation MUST abort `instance_extra` or `instance_missing`

#### Scenario: Hotreload cannot be proven

- **WHEN** any hotreload container of the project exists in any state
- **THEN** preparation MUST abort `hotreload_unverifiable`, because live-reloaded source can change
  without a process start

#### Scenario: Dirty or moved checkout aborts

- **WHEN** the checkout's `HEAD`, roster tree id, or cleanliness does not match the target
- **THEN** preparation MUST abort `checkout_mismatch` or `checkout_dirty`

### Requirement: Mutator inventory and test receipt binding
The receipt SHALL bind a mutator-inventory digest recomputed from the exact target code and a
test-receipt digest proving that the named real-PostgreSQL transition and mutator tests passed at
the exact target SHA. Central-writer proof alone MUST NOT satisfy either binding.

ID: REQ-relationship-effective-time-cutover-004

The mutator inventory SHALL be shipped code, not test-only data, so the image and checkout carry it.
Its digest SHALL be the SHA-256 of the JCS encoding of the sorted list of
`{path, qualname, classification, file_sha256}` entries, where `file_sha256` is the SHA-256 of the
file bytes. `src/` entries SHALL be read from the target image by `docker create` plus `docker cp`
without starting the container; `roster/` entries SHALL be read from the verified checkout. The
static inventory guard MUST be clean at the target SHA.

The wrapper SHALL run the required tests itself during preparation, after setting the fence and
verifying the checkout and before quiescing. It SHALL export the verified commit with `git archive
<target_sha>` into a fresh root-owned directory, run `scripts/pytest_gate.py run` over the fixed node
list from that export as a dedicated unprivileged account, capture the log through a pipe into a
root-owned file the test process cannot write, and recompute the verdict with `pytest_gate.py
verdict`. `UNKNOWN` or `FAILED` SHALL abort `test_receipt_not_pass`. The node list SHALL be a
constant of the installed wrapper, not an argument, and SHALL include the rel035 and rel036
migration tests, the static inventory guard, and every real-PostgreSQL scenario named by
`relationship-fact-effective-time` task 3.5. Tests use their own testcontainers databases and MUST
NOT connect to the cutover target.

The resulting test receipt SHALL be JSON with schema tag
`butlers.relationship-temporal-cutover-tests/v1`, binding the target SHA, the SHA-256 of the sorted
node-id list, the SHA-256 of the captured log, the verdict `PASS`, and the authorization and fence
ids. It SHALL be written root-owned beside the receipt, and the receipt SHALL bind its SHA-256. No
operator-produced log, receipt, or CI result SHALL be accepted, read, or signed.

#### Scenario: Inventory drift blocks cutover

- **WHEN** any inventoried file differs from the digest bound at preparation, or the static guard
  finds an unclassified mutator
- **THEN** preparation or the migration MUST abort `mutator_inventory_mismatch`

#### Scenario: Stale or incomplete test evidence blocks cutover

- **WHEN** the wrapper's own run collects fewer nodes than the fixed list, runs from a tree other than
  the target commit, or its captured log verdict is not `PASS`
- **THEN** preparation MUST abort `test_receipt_mismatch` or `test_receipt_not_pass` before
  quiescing or signing

#### Scenario: Test evidence is wrapper-produced

- **WHEN** a receipt is signed
- **THEN** its `test_receipt_digest` MUST be the digest of the test receipt produced by that same
  wrapper invocation from its own run
- **AND** an operator-run `pytest_gate.py` log or hosted CI result MUST NOT be substituted

### Requirement: Enforced managed writer lifecycle fence
The managed cutover path SHALL hold one root-owned lifecycle fence for the target Compose project from
before instance inventory until the exact target release is verified. While the fence is held, no
supported lifecycle verb may start, restart, create, or run any credentialed container except the
wrapper's own cutover migration run and exact-target release, and the rel036 DDL SHALL run under a
PostgreSQL write exclusion that proves no other client session exists.

ID: REQ-relationship-effective-time-cutover-005

The fence SHALL be the file
`/var/lib/butlers/relationship-temporal-cutover/<compose_project>.fence.json` (`root:root`, mode
`0644`, root-owned non-writable directory, written by atomic rename) carrying fence id,
authorization id, project, target SHA, target image id, generation, phase (`quiesced`, `migrated`,
or `releasing`), and set time. Absence means clear. Preparation SHALL, in order: take a host
lifecycle lock; set the fence; inventory and prove the instance set; set every credentialed
container's restart policy to `no`; stop it; prove it stopped; remove it; prove zero credentialed
containers of the project remain in any state; and only then sign the receipt.

Every supported start path SHALL check the fence before acting and refuse while it is held:
`scripts/compose.sh` (every invocation, before its `down`), and `butlers deploy` (before build,
migration, and recreate). The wrapper's release verb SHALL start the named invocation row's services through that
row's own launcher in a fence-release mode that performs the launcher's protected restore-drill
preparation, skips any build, and pins `butlers-app` to the target image id; it SHALL verify each
started container against the receipt's instance rules with `StartedAt` after the rel036 commit,
prove no extra container, and only then remove the fence. The launchers SHALL accept fence-release
mode only while the fence phase is `releasing` and the fence binds the same row and configuration
digest.

The rel036 upgrade SHALL, in one transaction and before any DDL: acquire
`LOCK TABLE relationship.entity_facts IN ACCESS EXCLUSIVE MODE NOWAIT`; then prove zero client
backends on the target database other than its own session; then verify the fence file is present,
matches the receipt, and is in phase `quiesced`. It SHALL hold the lock through the DDL and commit.

A signed snapshot, an advisory lock, or both SHALL NOT satisfy this requirement: old code does not
take the advisory lock, and Docker's `unless-stopped` policy or any `compose up` can start an old
container after a snapshot is taken.

Direct Docker or Compose lifecycle outside the managed path (`docker start`, `docker restart`,
`docker run`, raw `docker compose up`, Portainer or other GUIs, `compose.sh` from a different
checkout, or host `uv run` against the database) is unsupported during the window. The managed path
reduces its reach by removing old containers and by failing the migration on any live session, but
it cannot prevent a root-capable operator from bypassing it; the packet MUST state this residual
risk.

#### Scenario: Old container restart attempt fails

- **WHEN** after preparation an operator runs `scripts/compose.sh`, `butlers deploy`, or relies on
  Docker restart policy to revive a stopped writer
- **THEN** `compose.sh` and `butlers deploy` MUST refuse `cutover_fence_held` before any lifecycle
  act
- **AND** no container exists to restart, because preparation set its policy to `no`, stopped it,
  and removed it

#### Scenario: Writer connection during DDL is excluded

- **WHEN** any other client session is connected to the target database at the rel036 check
- **THEN** the upgrade MUST abort `active_writer` before DDL
- **AND** a session that connects after the check cannot write `relationship.entity_facts` until the
  DDL transaction commits, because the access-exclusive lock is held

#### Scenario: Lock contention aborts instead of waiting

- **WHEN** another session holds any lock on `relationship.entity_facts`
- **THEN** the `NOWAIT` lock MUST fail and the upgrade MUST abort `lock_unavailable` with no change

#### Scenario: Snapshot or advisory lock alone is insufficient

- **WHEN** an implementation offers only a signed inventory snapshot, only
  `pg_advisory_lock`, or both, without the fence, restart disablement, container removal, and table
  exclusion
- **THEN** it MUST NOT be accepted as satisfying the cutover proof

#### Scenario: Only the exact target is released

- **WHEN** the release verb would start any container whose image id, `GIT_SHA`, or roster mount
  differs from the receipt, or finds any extra container
- **THEN** release MUST stop, keep the fence held, and report `release_instance_mismatch` or
  `release_instance_extra`

### Requirement: Rel036 outside automatic advancement
The rel036 migration, which drops only `relationship.uq_ef_spo_active`, SHALL be present in the
ordinary Relationship version directory but SHALL NOT be applied by any automatic migration path.
It SHALL run only through an explicit upgrade to exactly `rel_036` with
`-x relationship_temporal_cutover_receipt=<path>`, and every precondition failure SHALL abort before
DDL with no schema change.

ID: REQ-relationship-effective-time-cutover-006

`src/butlers/migrations.py` SHALL register rel036 as a gated revision of the `relationship` chain.
Automatic paths (daemon startup, `butlers db migrate`, the Compose `migrations` service, and
`butlers deploy`) SHALL upgrade that chain to the gate's `down_revision` while the gate is
unapplied, and to `head` only when the gate is already applied. Later revisions wait behind an
unapplied gate and are reported as `temporal_cutover_pending`. Because the chain is linear, every
later Relationship revision descends from rel036, so any database that has not cut over stays at
`rel_035` and receives no later Relationship migration. The revision file stays in the
ordinary directory so every image that knows rel036 can resolve `alembic_version` after cutover; a
separate directory would leave post-cutover daemons unable to locate the applied revision.

The rel036 `upgrade()` SHALL independently refuse without the x-argument, so a direct
`alembic upgrade relationship@head` still fails closed. With the argument, before DDL it SHALL
verify custody, signature, schema, purpose `cutover`, expiry against database `now()`, target SHA
against its image `GIT_SHA`, mutator-inventory digest against its own mounted files, database target
against `current_database()`, the database OID, and `pg_control_system().system_identifier`, the
fence, the exclusion, the presence and `indisvalid` state of `uq_ef_spo_occurrence_active`, and the
presence of `uq_ef_spo_active`. It SHALL then drop only `uq_ef_spo_active`, leaving columns, checks,
and the occurrence index unchanged.

#### Scenario: Automatic paths stop at the gate

- **WHEN** a daemon boots, `butlers db migrate` runs, or `butlers deploy` migrates a database whose
  Relationship chain is below rel036
- **THEN** the chain MUST stop at `rel_035` (or the gate's `down_revision`)
- **AND** `uq_ef_spo_active` MUST still exist afterwards

#### Scenario: Every precondition failure aborts before DDL

- **WHEN** the receipt is missing, unreadable, mis-owned, forged, from an unknown signer, expired, not
  yet valid, of the wrong purpose, or bound to another SHA, image, tree, inventory, test receipt,
  database, or fence; or the fence is absent, mismatched, or not `quiesced`; or any other session is
  live; or the lock is unavailable; or the occurrence index is absent or invalid
- **THEN** the upgrade MUST abort with the matching stable code
- **AND** `uq_ef_spo_active` MUST still exist and `alembic_version` MUST be unchanged

#### Scenario: Fresh database has no bypass

- **WHEN** the chain is applied to an empty or freshly created database
- **THEN** automatic advancement MUST still stop below rel036
- **AND** tests MUST reach rel036 only through the explicit path with test-only keys, fence, and
  receipt fixtures

#### Scenario: Gate lifecycle is an open owner decision

- **WHEN** this contract is read before the owner decides `bu-ftd491`
- **THEN** the gate MUST apply to every database: production, dev, fresh installs, CI and
  testcontainers databases, and restore-drill scratch databases
- **AND** no retirement of the gate, no non-production or fresh-install exemption, and no automatic
  advancement past rel036 MAY be implemented until the owner records that decision

### Requirement: Rollback and first temporal write boundary
The cutover path SHALL preserve the adopted rollback boundary: code and schema rollback are allowed
only before the first temporal write, only inside a fenced quiesced window, and only after proving no
temporal value exists and at most one active row exists per SPO. After the first temporal write, the
managed path MUST refuse downgrade and old-writer release and MUST NOT delete, choose, supersede,
flatten, or discard rows to recreate `uq_ef_spo_active`.

ID: REQ-relationship-effective-time-cutover-007

The first temporal write SHALL be the first committed state in which any `relationship.entity_facts`
row, in any validity, is temporal-bearing (any effective bound, precision, or period id non-null),
or in which two active rows share one SPO. Before rel036 this state is unreachable, because the
transition writer rejects temporal intent while the legacy index exists.

Rollback before rel036 SHALL be ordinary transition-image rollback plus the existing rel035 downgrade
refusals. Rollback after rel036 but before the first temporal write SHALL require a fresh
`rollback_before_first_temporal_write` receipt with the same fence procedure; the rel036
`downgrade()` SHALL, under the same `NOWAIT` access-exclusive lock and live-session proof, verify
zero temporal-bearing rows and zero duplicate active SPOs, then create `uq_ef_spo_active` with its
original definition inside that transaction. Only after that commit may the release verb start the
named rollback image. Any failure SHALL abort `temporal_rollback_prohibited` or the matching
precondition code with no change.

#### Scenario: Rollback before the first temporal write

- **WHEN** rel036 is applied, no temporal-bearing row exists, and no SPO has two active rows
- **THEN** a fenced downgrade with a valid rollback receipt MAY recreate `uq_ef_spo_active`
- **AND** only then MAY the named rollback image be released

#### Scenario: Rollback after the first temporal write is refused

- **WHEN** any temporal-bearing row or duplicate active SPO exists
- **THEN** the downgrade MUST abort `temporal_rollback_prohibited` with no change
- **AND** recovery MUST roll forward or follow a separately reviewed data-preserving plan

#### Scenario: No flattening escape exists

- **WHEN** any path would make the legacy index buildable by deleting, superseding, collapsing, or
  clearing temporal rows
- **THEN** that path MUST NOT exist in the migration, wrapper, verifier, or packet

### Requirement: Docker Compose ownership and authorization boundary
The cutover proof SHALL target only the current Docker Compose deployment, SHALL name exact future
source and test owners, and SHALL NOT itself authorize key provisioning, wrapper installation,
deployment, container lifecycle, migration execution, or temporal activation.

ID: REQ-relationship-effective-time-cutover-008

Future implementation SHALL own exactly these seams: `scripts/verify_relationship_temporal_cutover.py`
with `tests/scripts/test_verify_relationship_temporal_cutover.py`; the root wrapper
`scripts/relationship-temporal-cutover.sh`, its sudoers fragment
`scripts/relationship-temporal-cutover.sudoers`, and installer
`scripts/install_relationship_temporal_cutover_wrapper.sh` with
`tests/scripts/test_relationship_temporal_cutover_wrapper.py`; the fence check in
`scripts/compose.sh` with `tests/scripts/test_compose_relationship_cutover_fence.py`; the fence check
in `src/butlers/core/deploy.py` with `tests/core/test_deploy.py`; the receipt, fence, and inventory
parser `src/butlers/relationship_temporal_cutover.py` with
`tests/core/test_relationship_temporal_cutover_receipt.py`; the gated ceiling and x-argument
plumbing in `src/butlers/migrations.py` and `src/butlers/cli.py` with
`tests/core/test_migration_gated_revision.py` and `tests/config/test_migrations.py`; the migration
`roster/relationship/migrations/036_entity_fact_effective_time_cutover.py` with
`roster/relationship/tests/test_rel_036_cutover_migration.py`; and the operator packet
`docs/operations/relationship-effective-time-cutover.md`. There SHALL be no k3s, Kubernetes, or
generic orchestrator path, and no generic architecture follow-up.

A live cutover SHALL additionally require: owner adoption of the dedicated signer; the
`relationship-fact-effective-time` real-PostgreSQL scenarios (task 3.5) merged and in the test
receipt; the owner-gated resolution of the entity-merge collision wording (`bu-ldcp5f`), because the
inventory digest attests behavior that must match the adopted contract; the owner decision on the
gate lifecycle and non-production and fresh-install policy (`bu-ftd491`), which is open and whose
current default is that the gate applies to every database; and a separate
exact-environment authorization naming the authorization id, environment, supported invocation
row and flags, optional-absent services, target SHA, target image, rollback image, and maximum
window.

#### Scenario: Source delivery does not authorize a live act

- **WHEN** the verifier, wrapper, fence checks, and rel036 migration are merged
- **THEN** no key is provisioned, no wrapper is installed, no container is stopped, and no database
  is migrated past rel035 by that merge
- **AND** temporal writes remain refused `temporal_cutover_pending`

#### Scenario: Contract drift blocks authorization

- **WHEN** an inventoried mutator's merged behavior contradicts the adopted relationship-facts text
- **THEN** a live cutover MUST NOT be authorized until the owner-gated amendment resolves it

## Source References

- Non-Negotiable Rule 1 (user-federated sovereignty over one instance and its data)
- Non-Negotiable Rule 4 (deterministic infrastructure)
- Non-Negotiable Rule 5 (Git-based config is the source of truth for butler identity)
- RFC 0006 (database schema and isolation)
- RFC 0008 (deployment and network security)
- `relationship-fact-effective-time` active change (two-stage uniqueness transition, mutator
  inventory, rollback boundary)

## ADDED Requirements

### Requirement: Signed content-blind cutover receipt
Except after the complete independently verified non-receipt proof binding and fresh/disposable admission required by REQ-relationship-effective-time-cutover-006 are delivered, the Relationship effective-time cutover SHALL be authorized only by a canonical signed receipt that
binds one authorization id, one purpose, one Compose environment and project, the resolved Compose
invocation and its redacted configuration digest, the exact target Git SHA, the immutable target
image id, the target `roster/` tree id, the complete instance set, the mutator-inventory digest, the
digest of the test receipt the signing wrapper produced by running the required tests itself, the
database target, the active fence identity, the issue time, and an expiry at most 15 minutes after
issue.
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

ID: REQ-relationship-effective-time-cutover-001
Source: authorize-relationship-effective-time-cutover receipt schema; CLOSED bu-ftd491 answer A exception; heart-and-soul/vision.md rules1,4,5
Scope: v1-mandatory

#### Scenario: Receipt binds one exact cutover

- **WHEN** a receipt is verified for the G upgrade
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
The signing document SHALL live at
`/etc/butlers/relationship-temporal-cutover/signing-key.json` (regular file, `root:root`, mode
`0400`) and the verifier keyring at
`/etc/butlers/relationship-temporal-cutover/verifiers.json` (regular file, `root:root`, not group- or
world-writable). Both SHALL reuse the runtime-probe control document shapes (`version`, `alg`
`EdDSA`, `kid`, unpadded base64url key, `sign_from`/`sign_until`, `current`/`retiring`) but MUST use
a distinct `kid` namespace `rtc-*` and a distinct key. Receipts SHALL be written atomically to
`/var/lib/butlers/relationship-temporal-cutover/receipts/<authorization_id>.json`, `root:root`, mode
`0444`, in a root-owned directory that is not group- or world-writable. The key directory
`/etc/butlers/relationship-temporal-cutover` SHALL be `root:root` mode `0700`, and no test runtime,
container, or account other than root SHALL be able to read the signing document (see the isolated
test runtime in the mutator inventory and test receipt requirement).
The wrapper MUST sign only evidence it collected itself in the same invocation. It MUST NOT sign an
operator-supplied inventory, test receipt, `pytest_gate.py` log, Compose configuration, or digest,
it MUST NOT accept any of them as input, and it MUST NOT expose a generic "sign this document" verb.
Test evidence in particular SHALL come only from the wrapper running the fixed test node list itself
(see the mutator inventory and test receipt requirement). Provisioning
the key, installing the wrapper, and adding its sudoers rule are live host acts outside repository
authority; adopting this signer is an owner decision.

ID: REQ-relationship-effective-time-cutover-002
Source: authorize-relationship-effective-time-cutover design D1/D1a and signer artifact choice; heart-and-soul/security.md custody boundary
Scope: v1-mandatory

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

ID: REQ-relationship-effective-time-cutover-003
Source: authorize-relationship-effective-time-cutover design D2/D2a; RFC0008 deployment security; exact Compose invocation source
Scope: v1-mandatory

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
The mutator inventory SHALL be shipped code, not test-only data, so the image and checkout carry it.
Its digest SHALL be the SHA-256 of the JCS encoding of the sorted list of
`{path, qualname, classification, file_sha256}` entries, where `file_sha256` is the SHA-256 of the
file bytes. `src/` entries SHALL be read from the target image by `docker create` plus `docker cp`
without starting the container; `roster/` entries SHALL be read from the verified checkout. The
static inventory guard MUST be clean at the target SHA.
The wrapper SHALL run the required tests itself during preparation, after setting the fence and
verifying the checkout and before quiescing, in an isolated test runtime. The node list SHALL be a
constant of the installed wrapper, not an argument, and SHALL include the rel035 and G
migration tests, the static inventory guard, and every real-PostgreSQL scenario named by
`relationship-fact-effective-time` task 3.5. `UNKNOWN` or `FAILED` from `pytest_gate.py verdict`
SHALL abort `test_receipt_not_pass`.
Because the real-PostgreSQL scenarios need a container runtime, and access to the host Docker daemon
is root-equivalent, the test runtime SHALL satisfy every one of these properties, each checked by
the wrapper before the run starts:
1. **Own runtime, no host daemon.** Tests run under the dedicated account `butlers-rtc-test`, which
   is a member of no `docker`, `sudo`, `wheel`, `adm`, or `systemd-journal` group, and they use only
   that account's rootless Docker daemon, whose socket lives under the account's own runtime
   directory. The account MUST be unable to open the host daemon socket.
2. **No key, fence, receipt, or log access.** The run is a transient systemd service with
   `NoNewPrivileges=yes`, `ProtectSystem=strict`, `PrivateTmp=yes`, and
   `InaccessiblePaths=/etc/butlers /var/lib/butlers /run/secrets /var/run/docker.sock`. The key and
   receipt directories are `root:root` mode `0700`. The account MUST be unable to read the signing
   key and unable to write the fence, receipt, test-receipt, or captured-log paths; the wrapper
   captures stdout and stderr through a pipe into a root-only file.
3. **No network route to the target.** The service runs with `PrivateNetwork=yes`, so the test
   process tree and its rootless daemon have only a private loopback and no route to the cutover
   database, the project networks, or any external host. Its environment is a fixed allowlist with
   no `POSTGRES_*`, `PG*`, `DATABASE_URL`, or `RESTORE_DRILL_*` key, and no env file is readable.
4. **Frozen offline inputs with stated provenance.** The source is `git archive <target_sha>` from the
   verified checkout's object store, exported into a fresh root-owned directory and mounted
   read-only; its tree id MUST equal the target commit's tree. The toolchain is the target
   `butlers-app` image, copied into the rootless daemon from the host daemon by `docker save` and
   `docker load` and verified to have the target image id. The PostgreSQL test image is the one the
   target commit pins by digest, copied the same way and digest-verified. Dependencies install only
   with `uv sync --frozen --offline` against the exported `uv.lock`, which verifies every artifact
   against the lock's hashes, from a root-prepared read-only cache. Any pull, index, or other network
   need fails.
5. **No residue.** After the run, the transient service and its rootless daemon MUST be stopped and
   the export, cache overlay, and daemon data removed before inventory continues.
The wrapper SHALL prove properties 1 to 3 by running negative probes as the test account inside the
same service definition before the tests: opening the host daemon socket, reading the signing key,
writing the log path, and opening a TCP connection to the cutover database endpoint MUST each fail.
Any probe that succeeds, and any property that cannot be established on the host, SHALL abort
`test_isolation_invalid`. An unavailable pinned image or dependency SHALL abort
`test_dependencies_unavailable`, and leftover runtime state SHALL abort `test_runtime_residue`.
There is no fallback that runs the tests with host-daemon access; a host that cannot provide the
isolated runtime cannot perform a managed cutover. A disposable VM MAY replace the rootless daemon
only if it satisfies the same five properties and the same probes.
Immediately before signing, after the test run and after quiesce, the wrapper SHALL re-verify the
checkout's `HEAD`, roster tree id, and cleanliness, the target image id and projected `GIT_SHA`, and
the resolved Compose row and configuration digest, and SHALL abort `checkout_mismatch`,
`checkout_dirty`, `instance_mixed`, or `compose_invocation_mismatch` on any change since the first
verification.
The resulting test receipt SHALL be JSON with schema tag
`butlers.relationship-temporal-cutover-tests/v1`, binding the target SHA, the SHA-256 of the sorted
node-id list, the SHA-256 of the captured log, the verdict `PASS`, and the authorization and fence
ids. It SHALL be written root-owned beside the receipt, and the receipt SHALL bind its SHA-256. No
operator-produced log, receipt, or CI result SHALL be accepted, read, or signed.

ID: REQ-relationship-effective-time-cutover-004
Source: relationship-fact-effective-time design section8/task3.5; authorize-relationship-effective-time-cutover D1a/D5
Scope: v1-mandatory

#### Scenario: Inventory drift blocks cutover

- **WHEN** any inventoried file differs from the digest bound at preparation, or the static guard
  finds an unclassified mutator
- **THEN** preparation or the migration MUST abort `mutator_inventory_mismatch`

#### Scenario: Stale or incomplete test evidence blocks cutover

- **WHEN** the wrapper's own run collects fewer nodes than the fixed list, runs from a tree other than
  the target commit, or its captured log verdict is not `PASS`
- **THEN** preparation MUST abort `test_receipt_mismatch` or `test_receipt_not_pass` before
  quiescing or signing

#### Scenario: Test runtime cannot reach the host daemon, key, or target

- **WHEN** the isolation probes run as `butlers-rtc-test` inside the test service
- **THEN** opening the host Docker socket, reading the signing key, writing the captured-log path,
  and connecting to the cutover database endpoint MUST each fail
- **AND** if any of them succeeds, or the account belongs to `docker` or `sudo`, preparation MUST
  abort `test_isolation_invalid` before any test runs

#### Scenario: Tests cannot fetch or substitute dependencies

- **WHEN** the export's `uv.lock` names an artifact absent from the prepared cache, or the pinned test
  PostgreSQL image or target image id is not available for `docker load`
- **THEN** preparation MUST abort `test_dependencies_unavailable` without any network fetch

#### Scenario: Checkout drift during the test run is caught

- **WHEN** the checkout's `HEAD`, roster tree, or cleanliness changes while tests run or during
  quiesce
- **THEN** the re-verification immediately before signing MUST abort `checkout_mismatch` or
  `checkout_dirty`, and no receipt is written

#### Scenario: Test runtime leaves nothing behind

- **WHEN** the test service, its rootless daemon, or its export remains after the run
- **THEN** preparation MUST abort `test_runtime_residue` before inventory

#### Scenario: Test evidence is wrapper-produced

- **WHEN** a receipt is signed
- **THEN** its `test_receipt_digest` MUST be the digest of the test receipt produced by that same
  wrapper invocation from its own run
- **AND** an operator-run `pytest_gate.py` log or hosted CI result MUST NOT be substituted

### Requirement: Enforced managed writer lifecycle fence
The managed cutover path SHALL hold one root-owned lifecycle fence for the target Compose project from
before instance inventory until the exact target release is verified. While the fence is held, no
supported lifecycle verb may start, restart, create, or run any credentialed container except the
wrapper's own cutover migration run and exact-target release, and the G DDL SHALL run under a
PostgreSQL write exclusion that proves no other client session exists.
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
started container against the receipt's instance rules with `StartedAt` after the G commit,
prove no extra container, and only then remove the fence. The launchers SHALL accept fence-release
mode only while the fence phase is `releasing` and the fence binds the same row and configuration
digest.
The G upgrade SHALL, in one transaction and before any DDL: acquire
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

ID: REQ-relationship-effective-time-cutover-005
Source: relationship-fact-effective-time migration plan steps3-4; authorize-relationship-effective-time-cutover D3; RFC0006/0008
Scope: v1-mandatory

#### Scenario: Old container restart attempt fails

- **WHEN** after preparation an operator runs `scripts/compose.sh`, `butlers deploy`, or relies on
  Docker restart policy to revive a stopped writer
- **THEN** `compose.sh` and `butlers deploy` MUST refuse `cutover_fence_held` before any lifecycle
  act
- **AND** no container exists to restart, because preparation set its policy to `no`, stopped it,
  and removed it

#### Scenario: Writer connection during DDL is excluded

- **WHEN** any other client session is connected to the target database at the G check
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
The future Relationship temporal-cutover revision G SHALL live in the ordinary Relationship chain and drop only `relationship.uq_ef_spo_active`, preserving temporal columns, checks and the valid occurrence index. G SHALL use a then-free revision after the actual current head; historical `rel_036` is meeting-debrief storage and MUST NOT be renamed, overwritten, gated or mistaken for a completed temporal cutover. Automatic paths SHALL stop at G's actual predecessor on existing pre-cutover databases, and advance to head only when the applied Relationship revision is G or a descendant. `src/butlers/migrations.py` SHALL register G as a gated revision of the `relationship` chain; daemon startup, `butlers db migrate`, Compose `migrations` and `butlers deploy` SHALL use that ceiling. Every later Relationship revision SHALL remain behind an unapplied G and SHALL be reported `temporal_cutover_pending`; every image that knows G SHALL resolve its applied `alembic_version` from the ordinary directory, never a separately hidden migration directory. The mandatory owner-A terminal outcome SHALL permit genuinely fresh/disposable targets to advance without a signed receipt using non-spoofable source authority, including legitimate interrupted bootstrap and committed partial/resume paths; this outcome is UNDELIVERED in the current draft. The online first-traversal witness below is only a partial database-state candidate, not complete unsigned cutover authority. Receipt signature exemption SHALL NOT waive any complete compatible-mutator, old-writer-absence, wrapper-produced test, code/image/roster/instance/configuration/database/fence or lifecycle/write-exclusion claim required by REQ-relationship-effective-time-cutover-003, REQ-relationship-effective-time-cutover-004 and REQ-relationship-effective-time-cutover-005. REQ-relationship-effective-time-cutover-005 currently requires the quiesced fence to match the receipt; neither this draft nor current source defines an independently protected non-receipt binding channel for those claims. G SHALL refuse unsigned witness-only traversal until a complete source-owned non-receipt proof-binding contract and its actual registered implementation satisfy every retained claim without caller evidence or weakening the fence. A completed equivalent unsigned binding SHALL be composed explicitly with the full required proof bodies, not inferred from a database-state witness or missing receipt. No such binding, signer/custody mechanism or provisioning authority is selected by this source clarification. Fresh admission SHALL be derived by the online migration environment from its own physical database connection before any Relationship revision or version-table/schema preparation for that traversal, with no Relationship stamp and no pre-existing Relationship-chain footprint, and SHALL be bound to that actual database identity, connection/backend, transaction and target revision invocation. Empty schemas created by trusted bootstrap and unrelated core/module stamps MAY coexist; an existing Relationship object or stamp, an unreadable/ambiguous catalog, or an unclassified footprint SHALL deny freshness. For the partial fresh-state branch, the gate SHALL inspect only the environment's non-serializable private invocation witness, independently check its binding and one-time use without treating it as complete cutover proof, take `ACCESS EXCLUSIVE NOWAIT` on `relationship.entity_facts`, prove other-client exclusion as required by the admitted proof, recheck all-validity emptiness and both expected index definitions/validity, and hold exclusion through DDL and commit. Caller x-arguments, environment variables, settings, disposable labels, Config strings, a missing legacy index, a caller-selected schema, and emptiness alone SHALL NOT grant freshness. Without an independently valid source-derived fresh witness, G `upgrade()` SHALL refuse raw `alembic upgrade relationship@head` or any other invocation that omits the receipt x-argument. A receipt-bearing explicit path SHALL upgrade exactly to G with `-x relationship_temporal_cutover_receipt=<path>` and independently verify custody, signature, schema, cutover purpose, database-time validity, image SHA, mounted-code/inventory digest, database name/OID/cluster identity, active quiesced fence, write exclusion and valid occurrence and legacy indexes before any DDL; it SHALL recompute target SHA against its own image `GIT_SHA`, inventory against its own mounted files, expiry against database `now()`, target name against `current_database()`, actual OID/`pg_control_system().system_identifier` and occurrence-index `indisvalid`. Every failure SHALL leave indexes, data and the applied revision unchanged. Raw Alembic, bounded test-helper targets and every automatic caller SHALL traverse the same online admission; offline mode SHALL refuse G. A witness SHALL become unusable on consumption, rollback, commit, reconnect, different database/schema/revision target, or a new invocation, and SHALL never be cached across chains, reused Config objects or process lifetimes. A transactionally rolled-back from-base traversal MAY derive a new partial state witness; it SHALL still refuse unsigned G without the complete non-receipt proof binding. The partial candidate SHALL NOT infer authority from a committed partial Relationship stage, stamped empty nominally disposable database, restored database, deleted fact rows, TRUNCATE, lowered stamp or ordinary downgrade. Legitimate genuinely disposable interrupted/committed bootstrap and resume SHALL remain an explicit mandatory unsigned terminal obligation, UNDELIVERED here, rather than be permanently redefined as receipt-bearing. Completion SHALL require independently source-derived target/disposal provenance and full protected proof binding; the mechanism remains unspecified by this draft and MUST NOT be fabricated from a label or an inferred new custody. Until that obligation is actually satisfied, the current fail-closed receipt path or separately authorized new-target recreation is only a temporary safe fallback and SHALL NOT close the original outcome. Trusted owner/migration-role destructive removal of all historical objects and stamps is outside the runtime-caller security guarantee and SHALL be stated as residual risk, rather than called non-spoofable historical proof. Owner decision bu-ftd491 is CLOSED with answer A; bu-ldcp5f is CLOSED and its adopted merge carve-out is applied. Those decisions MUST NOT be requested again or described as open; a genuine new actor/privilege/custody/history guarantee beyond the existing trusted-admin security doctrine before its implementation, and dedicated signer/proof-topology adoption plus exact authorization for live acts, remain distinct gates. A durable marker that safely reuses existing trusted bootstrap/migration/provisioning authority under owner A MAY be routine repository engineering rather than a new decision; its actual ordinary-role forgery, bootstrap regrant, trigger, restore, lifetime and race behavior MUST be proved. Existing bounded repository release SHALL NOT be reopened as a generic permission question. No gate retirement or lighter stamped-data path SHALL be inferred from the fresh exception.

ID: REQ-relationship-effective-time-cutover-006
Source: CLOSED bu-ftd491 answer A (2026-10-04); relationship-fact-effective-time uniqueness transition; source-derived online admission proposal
Scope: v1-mandatory

#### Scenario: Automatic paths stop at the gate

- **WHEN** daemon startup, db migrate, Compose migrations or deploy reaches an existing pre-cutover Relationship branch
- **THEN** it MUST stop at G's actual predecessor and keep the legacy index
- **AND** historical meeting-debrief rel_036 MUST remain its ordinary unrelated migration

#### Scenario: Every precondition failure aborts before DDL

- **WHEN** receipt custody, readability, schema, signer, signature, purpose, time, SHA/image/tree/inventory/test/database binding, quiesced fence, activity exclusion, lock or occurrence-index validity fails
- **THEN** the matching stable precondition code MUST abort before DDL
- **AND** the legacy index, data and applied revision MUST be unchanged

#### Scenario: Fresh-from-base run passes without a receipt

- **WHEN** an implemented protected non-receipt binding independently proves every retained REQ003/004/005 source/test/instance/target/fence/exclusion claim and the same actual online transaction has a valid pristine witness, empty all-validity facts and expected valid indexes
- **THEN** G MUST advance without a signed receipt and drop only the legacy index
- **AND** the snapshot's no-stamp state MUST be distinguished from the intermediate predecessor stamp created by its own traversal

#### Scenario: Stamped database has no bypass

- **WHEN** a database had a Relationship stamp or chain footprint at invocation start, including an empty, truncated, downgraded, restored or nominally disposable target
- **THEN** the partial candidate MUST refuse caller-derived unsigned advancement; existing real-data targets MUST use the normal receipt/fence path
- **AND** a genuinely disposable unsigned exemption requires independent actual provenance plus the complete non-receipt proof binding, which remain UNDELIVERED; no caller freshness/disposal assertion may substitute

#### Scenario: Remaining gate lifecycle questions are open owner decisions

- **WHEN** this contract's historical gate-policy text is interpreted
- **THEN** the CLOSED bu-ftd491 answer A and CLOSED bu-ldcp5f amendment MUST be treated as resolved authority, preserving the precise source admission rule
- **AND** a new durable disposal/birth authority, signer adoption, proof-topology adoption or retirement proposal MUST be identified by its actual changed boundary rather than reopened as the same owner choice

#### Scenario: Caller freshness assertions cannot authorize the gate

- **WHEN** a raw Alembic invoker supplies fresh/disposable x-arguments, Config values or environment flags against a planted stamped target with facts deleted
- **THEN** the online environment MUST derive its own negative admission and G MUST refuse without a valid receipt
- **AND** the test MUST observe preserved stamp/index state rather than merely assert a mocked resolver result

#### Scenario: Unstamped historical footprint is refused

- **WHEN** an empty entity_facts table, its evidence/context/index objects or any other Relationship-chain footprint exists despite an absent/deleted Relationship stamp
- **THEN** the admission MUST refuse freshness before traversing the branch
- **AND** a missing legacy index MUST NOT convert a corrupt or manually modified target into a fresh target

#### Scenario: Bootstrap and unrelated chains do not impersonate Relationship history

- **WHEN** ordinary init-db has created empty managed schemas and core/module heads but no Relationship-chain objects or stamp
- **THEN** the Relationship traversal MAY derive its own pristine witness from that same connection
- **AND** the implementation MUST support both public-version-table and schema-scoped production/helper arrangements without ignoring a Relationship stamp in another applicable version location

#### Scenario: Migration backfill can revoke otherwise fresh admission

- **WHEN** rel_028 or any preceding revision imports legacy public data into entity_facts during the witnessed traversal
- **THEN** G MUST detect the non-empty table under its lock and refuse the receipt exemption
- **AND** the failed transaction MUST preserve pre-invocation legacy data and NOT clear rows to pass

#### Scenario: Witness does not survive transaction or invocation changes

- **WHEN** the witness is replayed after rollback/commit, on a reconnected physical connection, against another database/schema, through a different revision target or a reused Config/next-chain invocation
- **THEN** G MUST refuse the witness as invalid admission
- **AND** no DDL or revision advancement may result

#### Scenario: Fully rolled-back first traversal retries safely

- **WHEN** a from-base traversal fails before G commits and all Relationship objects and stamps created by that transaction roll back
- **THEN** a new same-target traversal MAY derive a new partial state witness after rereading the actual catalog, but MUST NOT pass unsigned G without the complete non-receipt proof binding
- **AND** the old witness MUST stay unusable, including after process restart

#### Scenario: Committed partial bootstrap does not fabricate resumable freshness

- **WHEN** a legitimate disposable bootstrap committed a pre-G Relationship stage before crash, deliberate bounded upgrade or first launch
- **THEN** its empty state MUST remain gated under the partial candidate; legitimate disposable unsigned resume is an explicit mandatory terminal obligation that is UNDELIVERED, not permanently excluded
- **AND** the temporary operator fallback MUST name the receipt route or separately authorized new-target recreation without silently dropping data, inventing a disposable marker or claiming the whole outcome complete

#### Scenario: Concurrent client cannot race the final empty check

- **WHEN** another session holds a fact-table lock or is present at the required exclusion check, or connects and attempts a write while G holds its lock
- **THEN** G MUST abort `lock_unavailable` or `active_writer` for the pre-existing conflict, and a later write MUST not run before commit
- **AND** a losing/concurrent migration invocation MUST never reuse the first invocation's witness or bypass its independently reread stamp

#### Scenario: Schema mismatch and offline paths stay closed

- **WHEN** schema selection hides the actual Relationship history, expected indexes/checks are missing or wrong, catalog/activity visibility is inadequate, or Alembic runs offline
- **THEN** the gate MUST refuse with a stable code before cutover DDL
- **AND** neither mocked metadata nor zero visible rows may count as successful admission

#### Scenario: Fresh receipt exemption does not erase writer proof

- **WHEN** a pristine target cannot prove the complete compatible mutator source and absence/exclusion of old writers under a supported source-owned lifecycle path
- **THEN** the target MUST remain pre-cutover even if no receipt is otherwise necessary
- **AND** unsigned snapshots, process health and cooperative advisory locking MUST NOT satisfy the missing proof

#### Scenario: Administrative erasure is not historical proof

- **WHEN** a trusted owner or migration-role actor destroys every historical stamp and footprint or restores a manipulated dump
- **THEN** the packet MUST state that current-catalog admission cannot prove that target's past, and that this destructive administrative path is outside the runtime-caller non-spoofability guarantee
- **AND** it MUST NOT advertise protection against that actor without a separately adopted durable provenance boundary

#### Scenario: Database-state witness does not replace protected proof binding

- **WHEN** a pristine actual raw Alembic, migrated-helper or daemon traversal supplies only a valid private database-state witness and no complete protected non-receipt binding for the REQ003/004/005 claims
- **THEN** G MUST refuse unsigned advancement before DDL and preserve data, indexes and applied revision
- **AND** no current helper, daemon, wrapper metadata, caller assertion or test-only receipt injection may be credited as implemented unsigned authority

#### Scenario: Legitimate disposable resume remains mandatory and undelivered

- **WHEN** a genuinely disposable target resumes after a committed partial bootstrap or interruption
- **THEN** the whole owner-A delivery MUST eventually admit it without a signed receipt using independently valid source-derived provenance and the complete protected non-receipt proof binding
- **AND** until that real mechanism and its actual registered proof exist, this partial proposal MUST remain fail-closed and the original implementation outcome MUST remain UNDELIVERED

### Requirement: Rollback and first temporal write boundary
The cutover path SHALL preserve the adopted rollback boundary: code and schema rollback are allowed
only before the first temporal write, only inside a fenced quiesced window, and only after proving no
temporal value exists and at most one active row exists per SPO. After the first temporal write, the
managed path MUST refuse downgrade and old-writer release and MUST NOT delete, choose, supersede,
flatten, or discard rows to recreate `uq_ef_spo_active`.
The first temporal write SHALL be the first committed state in which any `relationship.entity_facts`
row, in any validity, is temporal-bearing (any effective bound, precision, or period id non-null),
or in which two active rows share one SPO. Before G this state is unreachable, because the
transition writer rejects temporal intent while the legacy index exists.
Rollback before G SHALL be ordinary transition-image rollback plus the existing rel035 downgrade
refusals. Rollback after G but before the first temporal write SHALL require a fresh
`rollback_before_first_temporal_write` receipt with the same fence procedure; the G
`downgrade()` SHALL, under the same `NOWAIT` access-exclusive lock and live-session proof, verify
zero temporal-bearing rows and zero duplicate active SPOs, then create `uq_ef_spo_active` with its
original definition inside that transaction. Only after that commit may the release verb start the
named rollback image. Any failure SHALL abort `temporal_rollback_prohibited` or the matching
precondition code with no change.

ID: REQ-relationship-effective-time-cutover-007
Source: adopted relationship-fact-effective-time rollback boundary; authorize-relationship-effective-time-cutover D6
Scope: v1-mandatory

#### Scenario: Rollback before the first temporal write

- **WHEN** G is applied, no temporal-bearing row exists, and no SPO has two active rows
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
The cutover proof SHALL remain a source-only Docker Compose proposal and SHALL retain exact future source/test ownership without authorizing keys, installation, host preparation, deployments, containers, migration execution or temporal activation. The complete compatible-mutator/old-image-absence and rollback contract remains governed by the already adopted relationship-fact-effective-time artifact and its September13 repository implementation release; bu-h3b7t remains the whole-feature terminal owner. Owner-A fresh-admission clarification under bu-2z6jyb SHALL consume CLOSED bu-ftd491 and CLOSED bu-ldcp5f without duplicate owner gates and SHALL amend this existing capability home rather than create a competing WHAT. Future owned seams remain verifier `scripts/verify_relationship_temporal_cutover.py` / `tests/scripts/test_verify_relationship_temporal_cutover.py`; root wrapper, sudoers and installer `scripts/relationship-temporal-cutover.sh`, `scripts/relationship-temporal-cutover.sudoers`, `scripts/install_relationship_temporal_cutover_wrapper.sh` / `tests/scripts/test_relationship_temporal_cutover_wrapper.py`; isolated runtime `scripts/relationship-temporal-cutover-test-runtime.sh` / `tests/scripts/test_relationship_temporal_cutover_test_runtime.py`; launcher fence `scripts/compose.sh` / `tests/scripts/test_compose_relationship_cutover_fence.py`; deploy fence `src/butlers/core/deploy.py` / `tests/core/test_deploy.py`; parser, fence and shipped inventory `src/butlers/relationship_temporal_cutover.py` / `tests/core/test_relationship_temporal_cutover_receipt.py`; automatic ceiling/actual online admission `src/butlers/migrations.py`, `alembic/env.py`, `src/butlers/cli.py` / nearest existing `tests/config/test_migrations.py` and `tests/daemon/test_butler_migrations.py`, with `tests/core/test_migration_gated_revision.py` only if a distinct unit selector seam remains; then-free cutover migration G / one then-named roster migration test species; static inventory `tests/contracts/test_entity_facts_mutator_inventory.py`; and `docs/operations/relationship-effective-time-cutover.md`. No planned number reserves or rewrites meeting-debrief rel_036. This source allocation SHALL NOT create a generic architecture follow-up. Fresh admission, receipt plumbing and G enforcement share one transaction/state machine and SHALL be allocated cohesively to the existing temporal owner; bu-jnnxtq's Unix-socket isolation outcome SHALL join the same isolated-runtime unit rather than create a second runtime owner. A Kubernetes extension belongs only to the source proposal bu-0kf2fd and remains unadopted; no Kubernetes or generic orchestrator path is supplied by this Compose source merge. Live legacy cutover SHALL additionally require adoption of the exact dedicated signer/proof artifact, merged named actual PostgreSQL transition/mutator tests in wrapper-produced evidence, resolution of every inventoried source/contract discrepancy and a separate exact-environment authorization specifying authorization id, invocation row/flags, optional-absent services, target SHA/image, rollback image and maximum window. Hosted CI SHALL NOT replace wrapper-produced live test evidence. The whole unsigned fresh/disposable outcome, including non-receipt proof binding and legitimate committed/interrupted bootstrap/resume, SHALL remain mandatory and UNDELIVERED rather than be narrowed to the partial first-traversal witness. A durable source-owned marker MAY reuse existing trusted authority under CLOSED A if actual privileges/lifetime/restore/regrant/race checks prove it non-spoofable; only a genuinely new actor/custody/privilege or history guarantee outside existing doctrine SHALL require its exact new decision. No marker or unsigned proof-binding implementation is supplied or selected here.

ID: REQ-relationship-effective-time-cutover-008
Source: bu-h3b7t adopted PR4065/September13 source release; CLOSED bu-ftd491 A and bu-ldcp5f/PR4317; exact future-owner source table
Scope: v1-mandatory

#### Scenario: Source delivery does not authorize a live act

- **WHEN** the owner-A clarification or future source implementation is merged
- **THEN** it MUST provision no key, install no wrapper, stop/start no container and execute no live migration or temporal activation
- **AND** current source MUST remain honestly described as pre-cutover until the actual applicable admission and operational prerequisites are satisfied

#### Scenario: Contract drift blocks authorization

- **WHEN** an inventoried mutator or supported migration/caller path contradicts the adopted relationship-facts clauses or the accepted admission proof
- **THEN** live cutover MUST remain unavailable until the exact discrepancy is repaired through its owning packet
- **AND** central-writer-only tests MUST NOT stand in for every source mutation, lifecycle fence or actual migrated role proof

#### Scenario: Closed owner choices are consumed once

- **WHEN** source packets cite gate policy or merge collision authority
- **THEN** CLOSED bu-ftd491 answer A and applied bu-ldcp5f SHALL replace stale pending-answer wording without asking those questions again
- **AND** new signer custody, durable provenance or deployment-topology adoption MUST stay distinct and concrete

#### Scenario: Kubernetes proposal does not activate Compose proof

- **WHEN** bu-0kf2fd proposes k3s writer inventory/fencing
- **THEN** the Compose contract MUST retain its full obligations and explicit lack of a currently implemented k3s equivalent
- **AND** neither proposal merge nor source metadata counts as deployment proof or adoption

**Source References (retained supporting context)**

- Non-Negotiable Rule 1 (user-federated sovereignty over one instance and its data)
- Non-Negotiable Rule 4 (deterministic infrastructure)
- Non-Negotiable Rule 5 (Git-based config is the source of truth for butler identity)
- RFC 0006 (database schema and isolation)
- RFC 0008 (deployment and network security)
- `relationship-fact-effective-time` active change (two-stage uniqueness transition, mutator
  inventory, rollback boundary)

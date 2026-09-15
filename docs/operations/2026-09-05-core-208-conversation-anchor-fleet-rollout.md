# Core 208 Conversation-Anchor Fleet Rollout and Rollback Packet

> **Prepared:** 2026-09-05 for `bu-n1p0o` and the `bu-psarp` operations gate.
> **Purpose:** Give a separately authorized operator an exhaustive, content-blind
> procedure for replacing every conversation-anchor writer with the merged
> core_208-compatible convergence implementation before PR #3960 may resume.
> **Boundary:** This tracked packet records repository facts and a future procedure.
> It is not evidence of a live rollout and authorizes no observation, deployment,
> migration, restart, rollback, credential access, Beads transition, or PR mutation.

## 1. Immutable prerequisite and verdict rule

The minimum application prerequisite is the merge commit from PR #3963:

```text
PREREQUISITE_GIT_SHA=26806d6a25c9ffe9ee8cb190e9c65eb7d4758175
PREREQUISITE_SUBJECT=fix: converge core 208 conversation anchors [bu-10t7z] (#3963)
```

The commit changes the core_208-compatible application helper. It is not an
Alembic revision named `core_208`; that already-existing revision belongs to a
different migration chain concern. The target release may be a later mainline
commit, but the authorization must name one full, immutable
`[AUTHORIZED_TARGET_GIT_SHA]`, and that commit must contain the prerequisite:

```bash
git merge-base --is-ancestor \
  26806d6a25c9ffe9ee8cb190e9c65eb7d4758175 \
  "[AUTHORIZED_TARGET_GIT_SHA]"
```

Exit zero proves ancestry only. It does not prove that an image was built from
the target, that a process was replaced, that the fleet is healthy, or that the
identity-split work from PR #3960 may proceed. Docker stamps the build commit in
`GIT_SHA` (`Dockerfile:43-49`), and the deploy path builds with that resolved
commit (`src/butlers/core/deploy.py:629-640`). Every evidence layer below is
therefore mandatory.

`bu-psarp` remains open unless the final verdict is exactly
`complete_fleet_pass`. A partial, stale, unhealthy, missing, additional,
unverifiable, rolled-back, or unknown writer is a failure. No script, deploy
success row, CI result, or health endpoint may automatically resume PR #3960 or
close the gate.

## 2. Static writer proof

### 2.1 Exhaustive runtime anchor writers

At the prerequisite artifact, an anchored row in
`public.dashboard_conversations` can be created by exactly two runtime paths.

| Writer path | Deployment unit | Code proof | Scope |
|---|---|---|---|
| Direct dashboard creation | `dashboard-api`; `dashboard-api-hotreload` is the dev substitute | `conversation_create` performs the direct INSERT (`src/butlers/api/conversations.py:99-124`); the POST route calls it (`src/butlers/api/routers/conversations.py:1438-1467`). | Creates the dashboard row before submitting its first turn. These rows have no thread key and are deliberately outside the partial unique index. |
| Routed thread-anchor upsert | `butlers-up`; `butlers-up-hotreload` is the dev substitute | The helper derives a stable Telegram identity, holds one acquired connection and transaction-scoped advisory lock, then inserts or selects the canonical row (`src/butlers/api/conversations.py:73-91`, `src/butlers/api/conversations.py:320-403`). Its only non-test call is the non-dashboard branch of `route.execute` (`src/butlers/core_tools/_routing.py:1001-1062`). | Applies to every accepted, non-dashboard route with a non-null `source_thread_identity` on every daemon except Messenger. |

The dashboard route is intentionally not a second thread upsert: its thread
identity is the existing conversation UUID, so the target daemon looks up that
row by ID (`src/butlers/core_tools/_routing.py:1015-1042`). Messenger has a
separate synchronous delivery path and never enters the async anchor writer
branch (`src/butlers/core_tools/_routing.py:723-728`).

`route.execute` is registered on every daemon regardless of core-tool group
configuration (`src/butlers/core_tools/_routing.py:499-511`). `butlers up`
discovers the roster and starts all selected daemons in one event loop
(`src/butlers/cli.py:86-114`, `src/butlers/cli.py:663-694`), while Compose places
that entire fleet in one `butlers-up` container (`docker-compose.yml:613-620`).
The checked-in anchor-capable members are therefore:

```text
chronicler, concierge, education, finance, general, health, home, lifestyle,
qa, relationship, switchboard, travel
```

Messenger must still be healthy as part of the full daemon fleet, but it is not
an anchor writer. A deployed roster may differ from this checked-in list. The
operator must derive the authorized target's roster again and reconcile it with
the running daemon inventory; a missing or additional member is `unknown_writer`
until reviewed, never an assumed non-writer.

The one-shot `migrations` unit can also mutate existing anchor rows (for example,
the core_214 ghost-collapse migration), but it is not a continuously running
writer. It remains a rollout dependency and rollback-compatibility concern. It
must not be counted as a healthy runtime writer.

The remaining runtime UPDATE paths do not introduce a third deployment unit.
Dashboard lifecycle and counter mutations are called from the conversation API
(`src/butlers/api/routers/conversations.py:618`,
`src/butlers/api/routers/conversations.py:1480`,
`src/butlers/api/routers/conversations.py:1613-1629`,
`src/butlers/api/routers/conversations.py:1849`). Provider-handle and reply
mutations execute inside the daemon fleet through Spawner or core tools
(`src/butlers/core/spawner.py:2381`, `src/butlers/core/spawner.py:2695`,
`src/butlers/core_tools/_conversation_reply.py:184`,
`src/butlers/core_tools/_switchboard.py:235`,
`src/butlers/core_tools/_switchboard.py:1255`,
`src/butlers/modules/pipeline.py:1746`). Thus the complete runtime process set
remains `dashboard-api` plus `butlers-up`, including their mutually exclusive
dev substitutes.

### 2.2 Re-run the closure proof at the authorized target

The following narrow scans are useful allowlists, not closure proof. Run them
from a clean checkout at `[AUTHORIZED_TARGET_GIT_SHA]`. Record path and line
only, not matched SQL or application data:

```bash
rg -n -o --glob '*.py' --glob '!tests/**' \
  '(INSERT INTO|UPDATE|DELETE FROM) public\.dashboard_conversations' \
  src roster alembic
rg -n -o --glob '*.py' --glob '!tests/**' \
  'conversation_(create|update|unarchive_if_needed|set_routed_butler|set_provider_session|clear_provider_session|reply_create|message_count_increment|get_or_create_by_thread)\(' \
  src roster alembic
```

Expected categories are the two runtime INSERTs above, dashboard API mutations,
daemon Spawner/core-tool mutations, the sole thread-upsert routing call, and
reviewed migration-only references.

Closure additionally requires all three reviews below. They deliberately cover
aliases, dynamic construction, non-Python code, and new top-level paths that an
exact Python pattern can miss.

1. **Broad tracked-tree identifier/import scan.** Scan the entire target tree,
   not selected roots or extensions. Include the table identifier, helper and
   module identifiers, conversation mutation names, SQL execution primitives,
   daemon/dashboard launch verbs, and database client imports. Retain only
   matched paths, line numbers, and the reviewer classification.

   ```bash
   git grep -n -I \
     -e dashboard_conversations \
     -e conversation_get_or_create_by_thread \
     -e butlers.api.conversations \
     -e asyncpg -e psycopg -e sqlalchemy \
     -e '.execute(' -e '.executemany(' \
     -e 'butlers up' -e 'butlers run' -e 'butlers dashboard' \
     "[AUTHORIZED_TARGET_GIT_SHA]" -- .
   ```

2. **Full target-diff review.** Review every added, modified, renamed, copied,
   and deleted tracked path since the pinned prerequisite, without a path
   filter. For every path, classify whether it can assemble SQL dynamically,
   call a database writer through an alias, expose a write API, start a daemon,
   add a language/runtime, generate executable code, or change packaging and
   deployment behavior.

   ```bash
   git diff --name-status --find-renames --find-copies \
     26806d6a25c9ffe9ee8cb190e9c65eb7d4758175.."[AUTHORIZED_TARGET_GIT_SHA]" -- .
   ```

3. **Deployment-entrypoint and opaque-file review.** Classify every tracked
   file at the target, including new repository roots, Compose/Kubernetes/systemd
   manifests, Dockerfiles, package entry points, shell scripts, generated
   executables, submodules, and binary files. Reconcile those entry points with
   the live supervisor inventory. An unreviewable binary, submodule, generated
   executable, external supervisor, or opaque deployment root is an unknown
   writer, not an exclusion.

   ```bash
   git ls-tree -r --full-tree "[AUTHORIZED_TARGET_GIT_SHA]"
   ```

The independent reviewer records the number of tracked paths classified, the
target diff path count, the matched-path allowlist, and a zero-unknown verdict.
Any new runtime call, SQL mutation, dynamic SQL writer, daemon launcher,
deployment entry point, or unclassifiable artifact invalidates this inventory.
Stop with `static_inventory_changed`; update and independently review the
packet before live action.

### 2.3 Thread-bearing producers and deployment units

Connectors do not write `dashboard_conversations`. They normalize an
`ingest.v1` event; Switchboard copies `event.external_thread_id` into
`request_context.source_thread_identity` (`roster/switchboard/tools/ingestion/ingest.py:355-370`),
and the target daemon performs the write. Restarting a connector alone can
never satisfy the prerequisite. Producers still belong in the operational
inventory because they delimit ingress, liveness, and the route paths that can
exercise the shared writer.

| Source channel | In-repository producer proof | Default deployment unit | Anchor exposure at the pinned code |
|---|---|---|---|
| `telegram_bot` | Chat and message form the reply target (`src/butlers/connectors/telegram_bot.py:1303-1319`, `src/butlers/connectors/telegram_bot.py:1343-1376`). | `connector-telegram-bot` (`docker-compose.yml:329-353`) | Yes; this is the legacy per-message input normalized by the prerequisite. |
| `telegram_user_client` | Chat ID is the thread ID (`src/butlers/connectors/telegram_user_client.py:1881-1897`, `src/butlers/connectors/telegram_user_client.py:1966-1977`). | `connector-telegram-user` (`docker-compose.yml:355-373`) | Yes when routed. |
| `whatsapp_user_client` | Chat JID is the thread ID (`src/butlers/connectors/whatsapp_user_client.py:2008-2021`, `src/butlers/connectors/whatsapp_user_client.py:2060-2070`). | `connector-whatsapp-user` (`docker-compose.yml:375-395`) | Yes when routed. |
| `email` / Gmail | Both metadata and full envelopes carry the Gmail thread ID (`src/butlers/connectors/gmail.py:2529-2547`, `src/butlers/connectors/gmail.py:2605-2615`). | `connector-gmail` (`docker-compose.yml:685-707`) | Yes when routed. IMAP is accepted by the contract but has no default Compose unit; see unknowns below. |
| `dashboard` | The API creates the row, then emits its UUID as the envelope thread ID (`src/butlers/api/conversation_envelope.py:115-145`). | `dashboard-api` or its hotreload substitute (`docker-compose.yml:222-290`, `docker-compose.yml:825-889`) | Direct writer plus ID lookup on the routed target; never the thread upsert. |
| `google_calendar` | Event ID is the thread ID (`src/butlers/connectors/google_calendar.py:546-556`). | `connector-google-calendar` | Conditional on routing policy. |
| `google_drive` | File ID is the thread ID (`src/butlers/connectors/google_drive.py:535-558`). | `connector-google-drive` | Conditional on routing policy. |
| `spotify_user_client` | Context URI can be the thread ID (`src/butlers/connectors/spotify.py:790-803`). | `connector-spotify` | Conditional on a non-null context and routing policy. |
| `owntracks` | The device `tid` forms a thread ID (`src/butlers/connectors/owntracks.py:695-709`). | `connector-owntracks` | Conditional; a metadata/skip rule may prevent routing, but policy is runtime state and cannot prove permanent exclusion. |
| `home_assistant` | Entity or automation identity forms a thread ID (`src/butlers/connectors/home_assistant_envelope.py:271-281`). | `connector-home-assistant` | Conditional on routing policy; Home Assistant may also use the `wellness` channel. |
| `activitywatch` | Endpoint identity is used as thread ID (`src/butlers/connectors/activitywatch.py:893-903`). | `connector-activitywatch` | Conditional; current skip policy is not a durable exclusion. |
| `voice` | Forwarded utterances can carry a conversation-session ID (`src/butlers/connectors/live_listener/envelope.py:80-113`). | `connector-live-listener` under the optional `audio` profile | Conditional and optional; presence must be resolved per environment. |
| `discord` | Channel or thread ID is emitted (`src/butlers/connectors/discord_user.py:1020-1048`, `src/butlers/connectors/discord_user.py:1071-1081`). | No default Compose unit; a console entry point exists. | Potential external/supervisor-managed producer; it is an inventory unknown until proven absent or named. |
| `gaming` / Steam | Current event builders set the thread ID to null (`src/butlers/connectors/steam.py:517-528`). | `connector-steam` | No anchor at the pinned code, but a changed builder at the authorized target reopens the inventory. |
| `wellness` / Google Health | Current Google Health builders set the thread ID to null (`src/butlers/connectors/google_health.py:533-543`). | `connector-google-health` | No Google Health anchor at the pinned code; Home Assistant wellness events remain conditional. |

The accepted source vocabulary also includes `slack`, `api`, and `mcp`, and
permits both Gmail and IMAP for `email`
(`roster/switchboard/tools/routing/contracts.py:24-85`). No default Compose
producer proves those paths absent. The authorized operator must inventory
systemd, Kubernetes, cron, manually launched `butlers run` / `butlers up`, and
external ingest clients in the target's actual deployment authority. An
ingest-only client does not need this application helper, but any unlisted
process that imports the helper, starts a daemon, exposes the dashboard write
API, or writes the table directly is an `unknown_writer` and aborts the gate.

## 3. Required future authorization

No live phase begins until one owner authorization record names all fields
below. `TBD`, blank, implied, inherited, or conversational approval is not
authorization.

| Field | Required content |
|---|---|
| Authorization ID | Immutable owner-approved change record. |
| Environment | One exact actual environment: target host/cluster, database identity, Compose project or supervisor namespace, and exposure classification. Do not record credentials. |
| Mode label | Exactly one of the repository launch labels `dev` or `prod`, recorded separately from the actual environment identity. |
| Maintenance window | Start, end, and time zone. |
| Target artifact | Full `[AUTHORIZED_TARGET_GIT_SHA]`, expected immutable image ID/digest, and proof the prerequisite is its ancestor. |
| Rollback artifact | Full `[ROLLBACK_GIT_SHA]`, immutable image ID/digest, retention location, and independent compatibility approval for the target's migrations. |
| Ingress gate | One reviewed `[INGRESS_GATE_PROCEDURE_ID]` that closes dashboard ingress and every connector/external-client submission at or before Switchboard acceptance, including already-buffered submissions, and provides content-blind closed/open evidence. Both lifecycle methods require this gate; stopped producer processes are additional defense, not a substitute. |
| Lifecycle method | Exactly one of `staged_writer_first` or `canonical_deploy_with_ingress_gate`, plus the immutable reviewed procedure ID. No hybrid or ad hoc command sequence. |
| Common lifecycle acts | Explicit permission to quiesce ingress; stop, create, recreate, and start the named writer and producer units; build or obtain the target image; run the migration phase; perform content-blind health/version observation; retry only named idempotent phases; and execute the named rollback procedure on abort. |
| Canonical deploy effects | When `canonical_deploy_with_ingress_gate` is selected: explicit permission to materialize or replace `.beads/issues.export.jsonl`; resolve the protected restore endpoint; stop and create `restore-drill-postgres-proxy` and `restore-drill-executor`; invoke both generation-bound verbs of the fixed root-owned firewall wrapper via passwordless `sudo`; create the protected networks/containers; run unscoped `docker compose up -d --remove-orphans`; poll health; and write success or failure to `public.deployments`. |
| Canonical affected units | Authorization names every unit in the exact merged Compose render. The current unprofiled base-plus-protected set is: `migrations`, `log-init`, `log-cleanup`, `backup-cron`, `dashboard-api`, `butlers-up`, `oauth-gate`, `connector-telegram-bot`, `connector-telegram-user`, `connector-whatsapp-user`, `connector-google-calendar`, `connector-spotify`, `connector-steam`, `connector-owntracks`, `connector-activitywatch`, `connector-home-assistant`, `connector-gmail`, `connector-google-drive`, `connector-google-health`, `restore-drill-postgres-proxy`, and `restore-drill-executor`. Any rendered addition is separately named or aborts. |
| Staged lifecycle effects | When `staged_writer_first` is selected: explicit permission for the reviewed procedure's build, migration, protected-overlay/firewall, supporting-service, writer-only recreate, later producer-start, health, ledger, and rollback effects. Its effect list must be at least as precise as the canonical list; merely naming “Compose” is insufficient. |
| Operator and verifier | One authorized operator and one independent operations/security verifier. |
| Evidence location | Owner-controlled sanitized record location and retention period. |
| Exclusions acknowledged | No credential read/rotation, message or provider-payload read, raw-log capture, database content inspection, volume deletion, PR #3960 mutation, schema identity-split deployment, Beads closure, or production action outside the named environment. |

Authorization for one launch label, host, database, project, window, artifact,
or lifecycle act does not authorize another. A read-only authorization does not
authorize a restart. A dev authorization does not authorize prod, and neither
authorizes whichever target happens to hold real personal data merely because
its filename sounds non-production.

The canonical effect list is code-derived, not a claim that every environment
uses it. `run_deploy` materializes the local Beads export before protected
preparation (`src/butlers/core/deploy.py:700-721`), then stops the restore-drill
proxy/executor, invokes the root-owned firewall wrapper twice, and creates the
protected units (`src/butlers/core/deploy.py:724-803`). Its recreate is
unscoped (`src/butlers/core/deploy.py:807-834`), so every unit returned by that
environment's merged Compose render is an authorized lifecycle effect. Optional
profiles such as `dev`, `audio`, `minio`, or `hotreload` add units only when the
selected lifecycle explicitly renders and authorizes them.

## 4. Content-blind evidence contract

Store only the two schemas in this section as two separate immutable records.
The operator may use existing injected authentication paths, but no credential
value may enter a command line, terminal transcript, shell history, or evidence
record.

```yaml
authorization_id: "<opaque record id>"
packet_git_sha: "<full commit containing this packet>"
environment_ref: "<owner-approved opaque label>"
mode_label: "dev|prod"
window_started_at: "<RFC3339>"
evidence_window_closed_at: "<RFC3339>"
prerequisite_git_sha: "26806d6a25c9ffe9ee8cb190e9c65eb7d4758175"
target_git_sha: "<full SHA>"
rollback_git_sha: "<full SHA>"
target_contains_prerequisite: true
static_inventory_status: "match|changed|unknown"
broad_tree_scan_status: "pass|fail|unknown"
full_target_diff_review_status: "pass|fail|unknown"
deployment_entrypoint_review_status: "pass|fail|unknown"
tracked_paths_classified: "<positive integer>"
target_diff_paths_classified: "<nonnegative integer>"
lifecycle_method: "staged_writer_first|canonical_deploy_with_ingress_gate"
lifecycle_procedure_id: "<immutable reviewed procedure ref>"
ingress_gate:
  procedure_id: "<immutable reviewed procedure ref>"
  coverage: "dashboard_and_all_switchboard_submissions"
  closed_at: "<RFC3339>"
  observed_closed_at: "<RFC3339>"
  status: "closed"
canonical_deploy_effects:
  applicable: "true|false"
  beads_export_materialized: "true|false|not-applicable"
  restore_drill_units_stopped_and_created: "true|false|not-applicable"
  root_firewall_prepare_completed: "true|false|not-applicable"
  root_firewall_attestation_completed: "true|false|not-applicable"
  merged_compose_units_authorized: "true|false|not-applicable"
  deployment_ledger_result: "success|failed|not-applicable"
writer_units:
  - unit_ref: "<compose service or approved opaque supervisor ref>"
    role: "thread_anchor_writer|dashboard_anchor_writer"
    expected_instances: 1
    observed_instances_before: 1
    observed_instances_after: 1
    container_or_process_ref_before: "<opaque id>"
    container_or_process_ref_after: "<opaque id>"
    image_id_before: "<immutable id>"
    image_id_after: "<immutable id>"
    git_sha_after: "<full SHA>"
    started_at_after: "<RFC3339>"
    replacement_proven: true
    health: "healthy"
daemon_health:
  - daemon_name: "<checked-in roster name>"
    status: "ok"
    observed_at: "<RFC3339>"
producer_classes:
  - producer_type: "<non-sensitive channel/type>"
    expected_instance_count: 1
    fresh_healthy_instance_count: 1
    target_image_instance_count: 1
    replacement_proven: true
    instances:
      - process_ref_before: "<opaque id>"
        process_ref_after: "<opaque id>"
        heartbeat_instance_ref: "<opaque UUID>"
        heartbeat_sent_at: "<RFC3339>"
        heartbeat_received_at: "<RFC3339>"
        heartbeat_observed_at: "<RFC3339>"
        heartbeat_sent_age_seconds: "<number>"
        heartbeat_received_age_seconds: "<nonnegative number>"
        freshness_cutoff_seconds: 300
        future_clock_skew_seconds: "<nonnegative number>"
        fresh: true
        state: "healthy"
unknown_writers: []
abort_reason_codes: []
rollback:
  invoked: false
  result: "not_invoked|complete|partial|failed|unknown"
pre_reopen_verdict: "ready_to_reopen|fail_closed"
evidence_digest:
  algorithm: "sha256"
  canonicalization: "RFC8785"
  value: "<64 lowercase hex characters>"
attestation_binding:
  algorithm: "sha256"
  fields:
    - authorization_id
    - packet_git_sha
    - target_git_sha
    - evidence_digest.value
  value: "<64 lowercase hex characters>"
operator_attestation:
  actor: "<approved operator identity>"
  attested_at: "<RFC3339>"
  decision: "ready_to_reopen|fail_closed"
  binding_sha256: "<exact attestation_binding.value>"
  immutable_record_ref: "<owner-controlled record ref>"
independent_verifier_attestation:
  actor: "<approved verifier identity>"
  attested_at: "<RFC3339>"
  decision: "ready_to_reopen|fail_closed"
  binding_sha256: "<exact attestation_binding.value>"
  immutable_record_ref: "<independent record ref>"
```

Reopening must not mutate that attested record. If and only if both
pre-reopen attestations say `ready_to_reopen`, create this separate append-only
closeout record for the authorized reopen attempt:

```yaml
schema_version: "core208-conversation-anchor-rollout-closeout.v1"
authorization_id: "<same authorization id>"
packet_git_sha: "<same full packet SHA>"
environment_ref: "<same owner-approved opaque label>"
target_git_sha: "<same full target SHA>"
pre_reopen_evidence_digest: "<exact evidence_digest.value>"
pre_reopen_attestation_binding: "<exact attestation_binding.value>"
operator_pre_reopen_attestation_ref: "<exact immutable record ref>"
verifier_pre_reopen_attestation_ref: "<exact immutable record ref>"
reopen:
  attempted_at: "<RFC3339>"
  completed_at: "<RFC3339|null>"
  independently_observed_at: "<RFC3339>"
  final_ingress_status: "open|closed|unknown"
window_ended_at: "<RFC3339>"
final_verdict: "complete_fleet_pass|fail_closed"
closeout_digest:
  algorithm: "sha256"
  canonicalization: "RFC8785"
  value: "<64 lowercase hex characters>"
closeout_attestation_binding:
  algorithm: "sha256"
  fields:
    - authorization_id
    - packet_git_sha
    - target_git_sha
    - pre_reopen_evidence_digest
    - pre_reopen_attestation_binding
    - closeout_digest.value
  value: "<64 lowercase hex characters>"
operator_closeout_attestation:
  actor: "<same approved operator identity>"
  attested_at: "<RFC3339>"
  decision: "complete_fleet_pass|fail_closed"
  binding_sha256: "<exact closeout_attestation_binding.value>"
  immutable_record_ref: "<owner-controlled closeout record ref>"
independent_verifier_closeout_attestation:
  actor: "<same approved verifier identity>"
  attested_at: "<RFC3339>"
  decision: "complete_fleet_pass|fail_closed"
  binding_sha256: "<exact closeout_attestation_binding.value>"
  immutable_record_ref: "<independent closeout record ref>"
```

Allowed evidence is limited to version/SHA, immutable image ID, opaque
container/process/heartbeat instance ID, unit name, daemon name and port,
timestamps, bounded counts, health/readiness/liveness state, exit status, and
the enumerated reason codes. Do not store endpoint identities, sender or
recipient identities, conversation or message IDs, prompts, titles, provider
session handles, request IDs, checkpoint cursors, error text, SQL rows, raw
HTTP bodies beyond a projected status field, full process environments, full
Docker inspection output, logs, traces, or provider payloads.

Connector heartbeats provide a fresh process UUID, uptime, state, and timestamp
(`src/butlers/connectors/heartbeat.py:107-133`,
`src/butlers/connectors/heartbeat.py:230-269`). They do **not** provide reliable
application provenance: several connectors currently initialize heartbeat
`version=None`. Use the container image ID plus its specifically projected
`GIT_SHA` for version proof; heartbeat evidence is only liveness and process
replacement proof. Never dump `.Config.Env` or an entire connector summary.

Freshness is independently recomputed for each opaque producer instance. At
this target, `freshness_cutoff_seconds` is fixed at 300, matching the canonical
`online` boundary (`src/butlers/core/liveness.py:23-52`). Record the connector's
`sent_at`, the Switchboard receipt time, the verifier observation time, and the
derived age. `fresh` is true only when the receipt age and sent age are each at
most 300 seconds, `state == "healthy"`, and the sent timestamp is not beyond
the canonical five-minute future-skew tolerance. A target that changes these
constants invalidates this packet's cutoff and requires review. Missing times,
negative age outside the allowed skew, or an age over the cutoff is stale.

The pre-reopen evidence digest is over the complete first mapping from
`authorization_id` through `pre_reopen_verdict`, excluding `evidence_digest`,
`attestation_binding`, and both attestation mappings. Serialize that mapping as
RFC 8785 canonical JSON and SHA-256 the resulting UTF-8 bytes. Then compute
`attestation_binding.value` as SHA-256 over the RFC 8785 canonical JSON object
containing exactly `authorization_id`, `packet_git_sha`, `target_git_sha`, and
`evidence_digest.value`. The operator and independent verifier each recompute
both digests and create distinct immutable attestations carrying the same
binding value and their own actor, timestamp, decision, and external record
reference. Identity fields without those digest-bound attestations do not
satisfy the gate.

After the reopen attempt, compute `closeout_digest.value` over the second
mapping from `schema_version` through `final_verdict`, excluding
`closeout_digest`, `closeout_attestation_binding`, and both closeout
attestations. Compute `closeout_attestation_binding.value` over the exact six
fields listed in its schema, using the same RFC 8785 and SHA-256 rules. The
operator and verifier independently recompute and attest that binding. This
second binding joins the final ingress state and window end to the immutable
pre-reopen evidence and attestations without editing or escaping either schema.

Timestamp order is fail-closed:

```text
window_started_at
  <= evidence_window_closed_at
  <= both pre-reopen attested_at timestamps
  < reopen.attempted_at
  <= reopen.completed_at (when non-null)
  <= reopen.independently_observed_at
  <= window_ended_at
  <= both closeout attested_at timestamps
```

Any missing, reversed, equal-at-the-strict-boundary, or unverifiable timestamp
forces `final_verdict: fail_closed`. `complete_fleet_pass` exists only in the
immutable, doubly attested closeout record after ingress is independently
observed open; `ready_to_reopen` is never a gate-complete verdict.

## 5. Preflight: no mutation yet

The operator and independent verifier both sign off this phase before any
service is stopped.

1. Confirm the authorization record is complete, current, environment-specific,
   and covers rollback. Confirm the evidence sink enforces the schema above.
2. From the canonical main checkout, fetch `origin/main`; require a clean
   worktree, no unpushed commits, and exact HEAD equal to
   `[AUTHORIZED_TARGET_GIT_SHA]`. Do not use `--allow-dirty-root`. The deploy
   preflight intentionally rejects linked worktrees and divergent commits
   (`openspec/specs/deployment-and-drift/spec.md`, requirement
   “`butlers deploy` — Preflight Guard Against a Frozen or Divergent Deploy
   Root”; `src/butlers/core/deploy.py:470-579`).
3. Run the ancestry check and all five static closure components from sections
   1 and 2.2: the two allowlist scans, full-tree scan, full target-diff review,
   and deployment-entrypoint/opaque-file review. Re-enumerate
   `roster/*/butler.toml`, Compose services, enabled profiles, and every external
   supervisor. Reconcile zero unknown writers.
4. Resolve the actual host/database/project behind the selected mode without
   copying endpoint or credential values into evidence. Require a second-person
   match to the authorization's opaque environment reference.
5. Capture the before-state for every writer unit: instance count, opaque
   process/container ID, immutable image ID, specifically projected `GIT_SHA`,
   start time, and health. For every producer instance, assign an opaque ref and
   capture its process ref, heartbeat instance ref, sent time, server receipt
   time, verifier observation time, health state, and computed freshness against
   the 300-second cutoff; omit endpoint identity and error text.
6. Require the rollback image to exist by immutable ID and remain retained
   under `[ROLLBACK_PROCEDURE_ID]`. Verify the rollback SHA and target migration
   set against the recorded compatibility approval. The prerequisite itself
   adds no migration, but a later target may; an unreviewed migration delta is
   `rollback_compatibility_unknown`.
7. Require sufficient window remaining for one rollout, the full settling
   interval, verification, and one rollback. Require zero active execution of
   any PR #3960/core identity-split operation.
8. Render the exact lifecycle's service/effect manifest. Match every unit and
   privileged/local side effect to the authorization in section 3. For the
   canonical deploy, this includes Beads export materialization, both protected
   restore-drill units, both root-firewall wrapper invocations, every unscoped
   Compose unit, health polling, and the deployment-ledger write.

Preflight does not read conversation tables, connector payloads, messages,
provider sessions, credentials, or raw logs. If the actual deployment cannot
be inventoried without crossing an unauthorized data boundary, record
`inventory_unverifiable` and stop.

## 6. Authorized rollout and restart order

Use exactly the lifecycle method named in the authorization. The preferred
method, `staged_writer_first`, keeps producer processes stopped through the
entire writer matrix in addition to keeping the common ingress gate closed. It
requires a separately reviewed procedure because the repository does not
currently expose that staging as one command. The fallback,
`canonical_deploy_with_ingress_gate`, may use `butlers deploy` only when the
separately reviewed ingress gate remains proven closed at or before Switchboard
acceptance throughout the unscoped recreate.

That distinction is binding. `butlers deploy` resolves and stamps the current
Git SHA, force-runs migrations, prepares the protected topology, then executes
an unscoped `docker compose up -d --remove-orphans`
(`src/butlers/core/deploy.py:807-834`, `src/butlers/core/deploy.py:888-945`).
Compose can therefore restart unprofiled connectors as soon as its single
`butlers-up` healthcheck passes; that check covers Switchboard port 41100, not
the per-daemon writer matrix. A prior `docker compose stop` does not keep those
connectors stopped through the canonical recreate. Without the reviewed gate,
the canonical method is `producer_started_early` and must abort.

The concrete two-phase procedure is:

1. **Close and prove the ingress gate.** Apply
   `[INGRESS_GATE_PROCEDURE_ID]` across dashboard ingress, every connector,
   already-buffered submissions, manually launched or externally supervised
   clients, and any second stack sharing the database. Record the closed and
   independently observed timestamps. Under `staged_writer_first`, also prove
   every producer count is zero. Do not infer quiescence from quiet traffic or
   treat stopped producers as a substitute for the dashboard/submission gate.
2. **Stop producers, then old writers.** Stop all named producers and direct
   dashboard ingress first. Stop `dashboard-api`, then `butlers-up`, and prove
   the old writer instance count is zero. Do not delete containers, volumes,
   checkpoints, or queues. Graceful daemon shutdown leaves durable route rows
   recoverable and drains runtime sessions (`src/butlers/lifecycle.py:572-633`).
   Forced kill, timeout, or unverifiable zero-writer state is an abort.
3. **Build, migrate, and prepare supporting infrastructure.** Execute every
   authorized effect from section 3, preserving the exact environment and
   project. Never use `down -v`, volume pruning, production hotreload, a
   dirty-root override, or an ad hoc migration shortcut.
4. **Phase A — establish writers while ingress remains closed.** Under
   `staged_writer_first`, recreate only the authorized supporting dependencies,
   `butlers-up`, and `dashboard-api`; producer instance count must remain zero.
   Under `canonical_deploy_with_ingress_gate`, run the canonical operation and
   prove the ingress gate remained closed before, during, and after its unscoped
   recreate even though producer containers may have restarted. A successful
   deploy result is necessary but is not writer evidence.
5. **Complete the writer matrix.** Verify `butlers-up` first, including every
   target-roster daemon's local `/health`, then verify `dashboard-api`. The
   backend exposes one readiness endpoint per daemon
   (`src/butlers/daemon.py:759-770`); dashboard `/health` becomes ready after
   application startup (`src/butlers/api/app.py:728-769`). Any missing,
   unhealthy, stale, or unverifiable daemon rolls back before producer ingress
   can reopen.
6. **Phase B — establish producers only after Phase A passes.** Under
   `staged_writer_first`, start one named producer class at a time only after
   the complete per-daemon and dashboard writer matrix has passed. Under
   `canonical_deploy_with_ingress_gate`, leave the gate closed and verify each
   already-restarted producer class in the same deterministic order; do not
   permit it to submit buffered or new work yet. For either method, an unknown
   or early producer is an abort.
7. **Settle and verify each producer.** Wait for its post-replacement heartbeat,
   then record the per-instance timestamps and freshness computation from
   section 4 before advancing to the next class. Heartbeat intervals are
   clamped to 30–300 seconds (`src/butlers/connectors/heartbeat.py:33-35`,
   `src/butlers/connectors/heartbeat.py:72-93`). No traffic may be injected to
   manufacture evidence.
8. **Close and attest the pre-reopen evidence window.** Complete the writer and
   producer evidence, set `evidence_window_closed_at`, set
   `pre_reopen_verdict`, and freeze that record. Compute its evidence and binding
   digests and obtain both distinct pre-reopen attestations. Recheck that the
   ingress gate is still closed. Only `ready_to_reopen` from both attestations
   permits the next step; it does not satisfy `bu-psarp`.
9. **Reopen exactly once, then close out separately.** After both pre-reopen
   attestations, perform the authorized reopen. Create the separate closeout
   record from section 4; record attempt/completion, independent final-state
   observation, and `window_ended_at` there without changing the first record.
   Compute the closeout digest and binding, then obtain both distinct closeout
   attestations. A gate that opens before the pre-reopen attestations, or a
   closeout that lacks the second digest-bound attestation phase, is
   `attestation_invalid` even if health is otherwise green.

The temporary rollout window is never a mixed-version acceptance claim. While
it exists, `bu-psarp` stays open and the core identity-split work stays frozen.

## 7. Process replacement and health evidence

### Writer unit: `butlers-up`

Require all of the following:

- exactly one authorized active daemon container/process and no base/hotreload,
  Compose/external, or old/new duplicate;
- after-ID differs from before-ID, after start time is inside the window, image
  ID equals the authorized target image, and the specifically projected
  `GIT_SHA` equals `[AUTHORIZED_TARGET_GIT_SHA]`;
- every daemon discovered from the target roster answers its local `/health`
  with the projected `status == "ok"`; every anchor-capable member from section
  2.1 is present, and Messenger is healthy as a fleet dependency;
- no daemon is silently skipped during sequential startup. The single
  Switchboard container healthcheck proves only port 41100, not every daemon
  (`docker-compose.yml:669-674`), so it cannot replace the per-daemon matrix.

### Writer unit: `dashboard-api`

Require exactly one base or authorized dev substitute, never both. The after-ID,
start time, image ID, and `GIT_SHA` must pass the same replacement rules.
Project only HTTP status and JSON `status` from `/health`; require HTTP 200 and
`status == "ok"`. Do not store the endpoint's other posture fields.

### Producer classes

For every present expected producer class, require the post-restart instance
count to equal the approved count, every container image to be the authorized
target image when it uses `butlers-app`, every process identity to be new, and
every heartbeat to be fresh and healthy. Store the per-instance opaque process
and heartbeat refs, sent/received/observed timestamps, derived age, 300-second
cutoff, clock skew, and health result; do not store endpoint identities or error
text. The verifier must be able to recompute `fresh` from the retained fields.
A disabled heartbeat or connector without an independently approved health
surface is `health_unverifiable`, not healthy.

An ingest-only external client on an older build does not execute the helper
and is not automatically a stale writer. It must still be classified explicitly.
Any process that starts a Butler daemon, serves the dashboard write API, imports
the helper as a writer, or writes the table directly must satisfy the full
writer-unit proof.

### Pre-reopen and complete-fleet acceptance

The independent verifier computes `ready_to_reopen` only when:

1. target ancestry, both allowlist scans, broad target-tree scan, full target
   diff review, and deployment-entrypoint review all pass;
2. expected writer count equals observed writer count before and after;
3. every old writer has disappeared and every after writer proves target image,
   target Git SHA, new process identity, in-window start, and healthy state;
4. every target-roster daemon is present and `ok`;
5. every expected producer is present, replaced where required, healthy, and
   fresh under the recorded per-instance timestamps and cutoff;
6. `unknown_writers` and `abort_reason_codes` are empty;
7. no rollback occurred; and
8. the pre-reopen evidence digest and attestation-binding digest recompute, and
   the operator and independent verifier provide distinct immutable
   attestations bound to the authorization ID, packet SHA, target SHA, and that
   exact evidence digest.

That verdict authorizes only the already-approved reopen step. The independent
verifier computes `complete_fleet_pass` only after the separate closeout record
also proves all of the following:

1. both immutable pre-reopen attestations resolve to `ready_to_reopen` and to
   the exact pre-reopen binding carried by the closeout;
2. the reopen attempt begins strictly after both pre-reopen attestations;
3. ingress is independently observed `open`, with the observation and
   `window_ended_at` ordered as section 4 requires;
4. the closeout digest and binding both recompute over the unchanged
   pre-reopen references and final ingress evidence; and
5. two distinct closeout attestations carry the same closeout binding, which
   includes the closeout digest, and both decide `complete_fleet_pass`.

Transport HTTP 200, Compose `running`, a successful deployment ledger row, or
a fresh heartbeat alone is insufficient. Neither `ready_to_reopen` nor an open
ingress gate without the final closeout attestations satisfies the fleet gate.

## 8. Abort and stale-state matrix

| Condition | Required result |
|---|---|
| Missing, expired, ambiguous, or wrong-environment authorization | `authorization_invalid`; do not start or stop anything. |
| Target SHA is not exact, not on main, or does not contain the prerequisite | `artifact_unverified`; abort before build. |
| Static audit or live inventory finds a new/unknown writer or supervisor | `unknown_writer`; abort and re-review the packet. |
| Rollback artifact/procedure/compatibility cannot be proven | `rollback_unavailable`; abort before quiescing ingress. |
| Ingress-gate coverage or its continuously closed state cannot be proven | `ingress_gate_unproven`; do not recreate writers or producers. |
| Ingress cannot be quiesced or old writer count cannot be proven zero | `ingress_not_quiesced` or `old_writer_unverifiable`; keep gate open. |
| A producer process starts before the complete writer matrix, or the ingress gate permits submission before both pre-reopen attestations | `producer_started_early`; close ingress, abort, and roll back. |
| Build or migration fails before replacement | `deploy_failed_pre_replace`; preserve evidence and use the authorized recovery branch in section 9. |
| Any writer fails to restart or retains its old process ID | `writer_restart_failed` or `stale_process`; rollback. |
| Any writer has wrong/unknown image, SHA, start time, or instance count | `mixed_fleet` or `version_unverifiable`; rollback. |
| Any daemon or expected producer is missing, stale under the recorded 300-second cutoff, degraded, unhealthy, or unverifiable | `fleet_unhealthy`; rollback or leave ingress closed under the approved incident path. |
| Either phase's digest, binding, distinct attestations, immutable cross-references, or timestamp order is absent or does not recompute | `attestation_invalid`; do not reopen, or re-close ingress if already reopened, then roll back. |
| Evidence contains or requires sensitive content outside section 4 | `evidence_boundary_breach`; stop capture, quarantine it under the owner's incident procedure, and do not attach it to Beads or a PR. |
| Window expires before complete verification | `window_expired`; rollback. |
| Any rollback is partial, failed, unhealthy, stale, or unverifiable | `rollback_incomplete`; keep ingress closed and escalate under the separately authorized incident procedure. |

Never continue from a partial rollout by assuming the remaining instances are
equivalent. Never repeat speculative lifecycle actions. Retrying the same
idempotent deploy is allowed only if the authorization explicitly covers a
retry, rollback remains available, and the captured failure is understood.

## 9. Rollback order

Rollback authority and an immutable rollback artifact must exist before the
rollout. This packet specifies the order; `[ROLLBACK_PROCEDURE_ID]` must supply
the deployment-specific commands, protected-overlay handling, and timeouts.
The repository has no general-purpose `butlers rollback` verb, so improvising
one from this document is prohibited.

1. Keep the reviewed ingress gate closed. Stop any target-version producers in
   reverse startup order and prove their submission path remains closed.
2. Stop the target `dashboard-api`, then the target `butlers-up`; prove zero
   active writer instances.
3. If failure occurred before any writer container was replaced, restart the
   retained old containers through the approved procedure. Otherwise recreate
   `butlers-up` from the immutable rollback image first, then recreate
   `dashboard-api`, preserving databases, volumes, queues, and checkpoints.
4. Start the rollback producer units only after the rollback writer plane is
   healthy.
5. Repeat the full replacement/version/daemon/producer evidence matrix against
   `[ROLLBACK_GIT_SHA]`. Reopen ingress only if the separately authorized
   rollback acceptance permits it.
6. If rollback begins before a reopen attempt, record
   `pre_reopen_verdict: fail_closed` and do not create a synthetic closeout.
   If reopening was attempted, create and attest the closeout with
   `final_verdict: fail_closed`, including the observed re-closed or unknown
   ingress state. A successful rollback restores service; it does not satisfy
   `bu-psarp`.

Do not downgrade a database schema as part of this prerequisite rollback. The
prerequisite is application-only. If an identity-split schema from PR #3960 (or
an equivalent successor) is present, this packet is no longer sufficient:
stop and invoke its separately reviewed rollback plan. The established ordering
is application compatibility first while the newer schema is still present,
then schema downgrade; reverting the convergence helper before a schema
downgrade reopens the race
([Ingestion Envelope Protocol](../api_and_protocols/ingestion-envelope.md),
“Core 208 conversation-anchor convergence prerequisite”).

## 10. Dev and prod are separate decisions

| Launch label | Normal units | Required distinction |
|---|---|---|
| `dev` | Compose project `butlers-dev`; default launcher may substitute `butlers-up-hotreload` and `dashboard-api-hotreload`. | Authorization must state whether hotreload is allowed. Bind-mounted source is not immutable image evidence; a hotreload writer therefore needs exact mounted-source SHA plus process replacement proof, or the rollout must use the baked-image dev deployment. |
| `prod` | Compose project `butlers`; baked `butlers-up` and `dashboard-api`; protected deployment topology. | Use the canonical protected lifecycle path named in authorization. Hotreload, source mounts, and a dirty-root override are failures. |

Repository labels are not sensitivity classifications. The deployment guide
records that `.env.dev` currently selects the database described as the live
system, while `.env.prod` selects the other target
([Docker Deployment](docker-deployment.md), “Environment Variables”). Therefore:

- authorize the actual host/database/project identity, not the filename;
- never infer that `dev` is disposable, test-only, or free of personal data;
- never reuse dev evidence for prod or prod evidence for dev; and
- if both stacks can write the same database, both writer fleets are in scope.
  Deploying one while leaving the other stale is `mixed_fleet`.

## 11. Gate handoff and future PR authority

After the closeout records `complete_fleet_pass`, attach only the sanitized
pre-reopen evidence digest and binding, both immutable pre-reopen attestation
references, the closeout digest and binding, both immutable closeout
attestation references, and the two schemas from section 4 to `bu-psarp`.
Each pair of references must resolve to its phase's same binding value and must
name its distinct actor, timestamp, and decision. The closeout must carry the
exact immutable pre-reopen digests and references, not copies with altered
fields.
The tracked packet, a deploy command's exit zero, or an unbound operator
assertion is not the evidence itself. An independent security/operations
reviewer must confirm:

- authorization identity and scope;
- writer closure and zero unknowns;
- artifact, process-replacement, and health proof for the exact fleet;
- content-blind evidence hygiene;
- rollback readiness and whether rollback was invoked; and
- all four recomputed digest/binding values, all four distinct phase
  attestations, the timestamp ordering, and the final closeout verdict.

Even a passing gate only establishes the prerequisite deployment fact. It does
not authorize any action on PR #3960. A future, separate instruction must name
the exact PR head/base and authorize its rebase, migration renumbering against
the then-current chain, removal of obsolete alias logic, tests, push, and fresh
exact-head review. Merge and any identity-split deployment remain still later,
separate authorities. Partial or failed evidence permits none of those steps.

## Repository references

- [Conversation-anchor provider resume ledger](../../openspec/changes/conversation-anchor-provider-resume-ledger/specs/dashboard-conversations/spec.md): requirements “Channel-Agnostic Conversation Anchor” and “Conversation Data Model”.
- [Canonical dashboard conversations](../../openspec/specs/dashboard-conversations/spec.md).
- [RFC 0003: Switchboard Routing and Ingestion](../../about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md).
- [Deployment and drift specification](../../openspec/specs/deployment-and-drift/spec.md).
- [Docker deployment](docker-deployment.md): topology and mode warning.
- [Security and Secrets](../../about/craft-and-care/security-and-secrets.md) and [Observability and Operations](../../about/craft-and-care/observability-and-operations.md): evidence bar.

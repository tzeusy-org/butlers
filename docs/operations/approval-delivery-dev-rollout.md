# Dev approval delivery: cohosting, activation and retained-data rollback

This runbook prepares the owner-approved temporary cohosting transition for `butlers-dev` and the separately gated RFC 0023 activation. It is source guidance, not a deployment receipt or authorization to inject a fault. Source/config renders, deployed topology, real callback behavior and recovery evidence are separate facts. bu-ys9hdx.1 remains open for the operational proof. Production defaults and the future RFC 0038 gates remain unchanged.

## Authority and stop conditions

The owner authorized temporary exclusive dev cohosting: “temporary cohosting dev is fine, butlers-dev downtime isn't immediately critical”; then constrained “Please don't blow up the database though”. This permits the documented topology replacement. It does not itself authorize the separately gated rollout-row changes or synthetic fault drill. The merged runbook and verbatim bounded drill authorization are still required before those operations. The standing authorization for exactly TWO asynchronous own-chat reaction/expiry canaries requires no fresh-go question, but their old runner cannot be used unchanged.

Preserve the live database, PVCs, historical pending actions, delivery roots/presentations/cohorts/attempts/handoffs and audit evidence. No reset/drop/downgrade/restore, stamping, backfill, historical replay, bulk retry, automatic retry of an uncertain send, new key/grant/role, or legacy sender. Prepared actions stay silent. Keep receiver-derived routing and fleet-condition handoff enabled. A failed or ambiguous step stops with safe evidence; no automatic destructive repair or binary rollback across unresolved sends.

## 1. Pin, build/push, and pass the database gate before outage

1. Pin the final **merged** full source SHA, exact chart/values (including any local overlay), app/frontend image tags and resolved image digests. Record the exact currently serving image(s), full corresponding commits, and Helm revision. Refuse dirty/unknown sources or mixed runtime versions. A short tag or deployment ledger alone does not attest applied migrations. Avoid dumping Helm values or environment wholesale.
2. Build and push first through the existing `image-dev` / `scripts/k8s/build-push.sh dev` path under the existing owner secret-injection workflow. This creates images without deploying. Do not use the no-argument `deploy-dev.sh` / `ship-dev` shortcut for the controlled outage: it builds and then immediately deploys. After the gates below, `scripts/k8s/deploy-dev.sh <already-pushed-tag>` selects the existing deployment path without rebuilding. Run the chart/scripts from the pinned released checkout, not an unrelated current checkout.
3. Near cutover, require the latest scheduled backup Job to have succeeded, no newer failed run, and matching successful artifact/last-run metadata with valid artifact integrity and freshness inside the established 36-hour bound. Query only Job status/timestamps and the safe `GET /api/system/backups` projection; never print dump contents. Missing/unknown/corrupt evidence means stop. A successful Job is not proof of restore certification. Recheck immediately before stopping runtimes.
4. Compare serving source(s) to the final release for **all** of `scripts/init-db.sql`, `alembic/` (including env/config), every tracked module/roster `migrations/`, migration runner/bootstrap-prerequisite code and dependencies, and roster/module migration selection/configuration. Compare file membership and blob hashes, not names alone; flag additions, deletions and edited applied migrations. Include bootstrap/deployment wrappers and their resolved inputs in the review. One finite reviewed runtime exception is `src/butlers/cli.py::_start_all`: normalize only that function body and require the entire remaining module AST (imports, globals, other functions, signatures and decorators) to be identical. Its `AsyncExitStack` import is local to that function. Never ignore the whole CLI file or migration entrypoint. The reviewed dev value changes only `butlersControl.enabled` from true to false. The deployment-only exception must pin independently reviewed before/after blob hashes for exactly `deploy/helm/butlers/makefile`, `scripts/k8s/record-deploy.sh`, and the two added `scripts/k8s/runtime-topology-preflight.sh` / `runtime_topology_preflight.py` files, including their exact added-file inventory and final source SHA. It never waives `scripts/k8s/` generally, any bootstrap bytes, or an arbitrary target version. Retain offline negative controls for unsupported deployment/bootstrap changes as well as migration entrypoint/global/import and added/removed/edited migrations. This activation needs no new migration or bootstrap surface: any other delta is a STOP for explicit DB-change review, not permission to deploy unrelated migrations with it.
5. Read actual live `alembic_version.version_num` sets in every schema targeted by the released migration plan, using the existing configured identity and schema boundaries. Compare every selected core, butler and enabled module chain against release `get_chain_head()` / `get_chain_revision_ids()`, including module schema overrides. Missing tables, unknown revisions, unapplied heads or unreadable state mean stop. `butlers db migrate --dry-run` prints selected chains; it does **not** establish that live revisions are current. Also run the existing **read-only** reviewed-bootstrap catalog prerequisite against the live database; no new login or grants to make a check pass.
6. Capture safe baselines: per-schema rollout booleans, counts by action/delivery state, attempt/handoff counts, UUIDs of unresolved work, and schema/version metadata. Require all approval rollout flags to retain their known disabled state for the topology-only cutover; an unexpected enabled row is a stop, not permission to silently disable it. Confirm application DB identity/host and storage references are unchanged in the render.

### The standard deployment DOES perform managed DB operations

`deploy-dev.sh <tag>` → chart `deploy-dev` first passes the read-only topology preflight, then `bootstrap-dev` reapplies that tag's reviewed `scripts/init-db.sql` as the existing distinct CloudNativePG local superuser, then `record-deploy.sh` repeats the preflight immediately before Helm. The API initContainer invokes `butlers db migrate`; startup may perform its normal managed initialization. The release wrapper writes `public.deployments` and waits for semantic readiness. Do not claim “no DB writes”, skip bootstrap/init migration containers, change their identities, or bypass the reviewed-bootstrap contract. Identical bootstrap/migration inputs plus current live revision/catalog proof distinguish the expected idempotent reapplication from an unreviewed DB evolution; bootstrap itself still executes managed DDL/ACL/configuration statements. No new authority is provisioned for approvals.

The shared dev-only guard renders the exact existing Helm arguments without publishing their
values, then checks the actual release/component selectors, runtime commands, observed
generations and status counters, and all old/current runtime pod modes. Valid same-topology and
fresh releases pass; malformed, stale, mixed or unreadable state stops. A transition requires
both roles stopped and all old/terminating pods gone, including a positive terminating-replica
counter with an empty later pod read. A stopped leftover control object is harmless to a
same-cohost release. The guard never writes, scales or deletes anything and does not replace
the operator's automation hold: its API observations are not an atomic lock. Prepare the pinned
checkout's real virtualenv with `uv sync --dev`; guard execution uses `uv run --no-sync`.
Helm and kubectl must share the normal inherited context/`KUBECONFIG`. Namespace arguments in
separated, equals or attached short forms must all select `butlers-dev`. The dev guard refuses
Helm-only `--kube-*` / `--kubeconfig` flags and any `HELM_KUBE*` environment key before
render/query, checking key names without reading or printing their credential values. It does
not introduce a separate context/credential plumbing path. Production behavior is unchanged.

## 2. Exclusive topology replacement; flags remain unchanged

1. Render and review the exact release first: dev `butlersControl.enabled=false`; one `butlers-up` including Switchboard; shared `BUTLERS_MAX_GLOBAL_SESSIONS=5`; Switchboard Service destination `butlers-up:41100`; no control workload or session listener on 41199. Explicit `enabled=true` must retain split 1+4 support. Preserve receiver routing/fleet-condition flags `1` consistently in API, runtimes and connectors. The change trades split fault isolation and reserved 1+4 capacity for one shared five-slot pool; idle slots become shared. Do not change production selection.
2. Hold competing deployment automation and verify no HPA/manual replica owner or Compose/baked/hotreload process can start another runtime against this DB. Confirm the complete competing Compose project is stopped, not merely its old domains service. Do not use the non-live DB override on this live database.
3. Scale **both** dev runtime Deployments to zero in one bounded operation: `kubectl -n butlers-dev scale deployment/butlers-up deployment/butlers-control --replicas=0`. Confirm successful API reads: desired replicas zero, controller observed current generations, all observed/current/ready/updated replicas zero, and **all old runtime pods actually terminated**, including terminating pods/old ReplicaSets. Check the release/component selectors for both roles; absence/error is not a zero-count success. Recreate is per Deployment and does not enforce this cross-Deployment ordering. Do not force-delete a pod and infer its process is dead.
4. Only after exclusive zero is proven, run the standard pinned-tag deploy path. Its Helm render creates one cohosted runtime and removes the stopped control role. No hand-written replacement pod, bootstrap bypass, DB clone/restore or rollout-flag edit belongs to this step.
5. Verify one actual serving cohost process, no old control/Compose process or 41199 listener, no `--exclude switchboard`, cap five, exact image digests, unchanged DB/storage references, all roster daemons/active modules, Switchboard Service/endpoints, fresh current-boot receiver observations and registry routing, and owner-recipient/callback **availability booleans only**. Use existing read-only internal route-preflight/health endpoints; a stale registry row or `/health` alone is insufficient. Let `record-deploy.sh` finish its full `/ready` stability/two-observer/two-QA-patrol gate (roughly 20–35 minutes); do not shorten its floor. Record source/config/deployed facts separately from Telegram proof.

If this fails, preserve rows and receipts and keep runtimes stopped as needed. An older image tag alone does not restore split topology: the wrapper renders the current chart and reapplies the selected image tag's bootstrap. Review compatibility and the complete rendered topology before any rollback.

## 3. Separately authorized per-schema activation

Discover active Approvals schemas through the released config/runtime, not the historical four-schema list. Current source has approvals entries for finance, general, health, home, lifestyle, messenger, relationship, switchboard and travel; actual enabled/active/schema mapping must be checked at cutover.

After the runbook is merged, exact source/runner independently reviewed and bounded authorization recorded, begin with **Messenger only** for the authorized own-chat canaries. Before any other source enablement, complete the staging/real-PG evidence and dashboard truth review required by REQ-011. Per-schema control is the singleton `approval_delivery_rollout` row. Use a short transaction through its existing configured authority: verify expected prior flags, set the selected schema's `admission_enabled=true, worker_enabled=true, updated_at=clock_timestamp()` with an exact singleton predicate, require exactly one updated row, and reread. No insertion/upsert if the expected row is absent.

Runtime/worker installation is startup/composition-sensitive: safely stop the sole cohost before changing flags, then recompose/restart the compatible released cohost and verify only the intended source worker/authority is active. Do not assume a DB flag starts/stops an already-running task. All other sources stay disabled. Admit only **new** actions through ordinary `park_pending_action`; never attach intents to old rows. Wait for genuine attention-policy eligibility and a naturally suitable burst window; do not move clocks, expiry timestamps, quiet hours, policy, cohorts or `next_attempt_at` to force a result.

## 4. Bounded drill packet: preferred live barrier, isolated rehearsal first

**Not yet authorized or runnable.** The real-dev fault plan may reuse the FIRST of the two already authorized canaries; it must not create a third action/send. Use its 24-hour own-chat native-reaction action, exact private target, one direct generation-one presentation and one normal durable handoff. The second 60-second expiry-negative action remains untouched by fault injection; ask the owner to tap it only after the existing 90-second wait. Never execute/retry either old failed canary.

The proposed live interruption is a single-row database barrier, not a data rewrite:

1. First demonstrate the exact released runner and barriers on a standalone disposable native PostgreSQL instance with synthetic identities, no live DB address/credentials/PVCs or real provider egress. Rehearsal proves mechanics only, not the live criterion. Do not add production fault hooks or an admin API. The old two-canary runner must first be adapted and independently reviewed (below).
2. Before live admission require no unrelated active delivery, provider operation or model session, no scheduled work due during the bounded interruption/recovery window, and a successfully checked competing-process exclusion. Enable only the selected source. After its first canary has atomically admitted, disable **new admission** again before any fault; preserve its worker flag and compatible runtime. This is an explicitly authorized drill flag transition. If the state cannot be kept limited to the single target, refuse the fault and retain the normal canary outcome.
3. Poll only the known canary UUID. After `mark_handoff_started` has committed, acquire a transaction-scoped `SELECT ... FOR UPDATE OF p NOWAIT` on **only that exact source presentation ID/generation** with expected `handoff_started`, pending/eligible canary and no successor. Read only UUID/state/generation/times; do not select claim tokens, arguments or callback data into receipts. Lock no action, intent, cohort, handoff or pre-existing row, and take no explicit table/advisory lock. There is no later action-row acquisition from this barrier, avoiding a reversed action→presentation lock order.
4. Bound the barrier connection with `lock_timeout=250ms`, `statement_timeout=2s`, `idle_in_transaction_session_timeout=5s`, plus an independent **20-second total wall-clock watchdog** that rolls back/closes on every exception, disconnect, timeout or missed stage. Polling does not reset that total bound. The idle timeout alone is insufficient. NOWAIT conflict, zero rows, wrong generation/status or a missed boundary means rollback and **no kill/retry/extra request**.
5. Read the exact matching Messenger handoff tuple until `handoff_class='confirmed'` and `completed_at` is committed. Verify via safe blocking metadata that the source worker is waiting behind this barrier. Important: the barrier also blocks the source heartbeat UPDATE that runs after handoff completion; do not falsely claim the terminal-write SELECT itself was reached. `authorize_transport` uses a plain read, and Messenger provider/confirmation transactions touch a separate table, so these can complete while the source row is held. If confirmation or the wait cannot be observed inside the bound without new privileges, release and stop; never kill on `provider_started_at` alone.
6. Only with confirmed handoff and verified blocked source, interrupt the **exact old cohost runtime PID/container once**, then confirm the old runtime has exited before its replacement runs; never touch PostgreSQL processes. A cohost has one application PID: this interrupts every daemon, hence the unrelated-work and imminent-schedule checks are material gates. Prefer a pinned single-container crash/restart mechanism proved by rehearsal; do not use forced pod deletion as proof of process death. Release the barrier immediately after death (or on failed kill) and let the existing container restart policy restore the same reviewed binary. Keep the 20-second bound even if restart is slow.
7. Wait for natural lease expiry and ordinary worker reconciliation. Do not backdate/shorten leases or manually resend. The confirmed Messenger tuple must yield no second provider call; source recovery must retain one root/presentation/generation, safely advance its fence/append attempt evidence and become delivered. Record old/new process/boot metadata and safe state/count evidence. If outcome is ambiguous, retain/report it and stop; it is not a recovery pass or permission to replay.
8. After actual owner approval of the first canary, the separately authorized duplicate-decision drill submits that **same** decision once through the real authenticated existing callback path, with private material kept egress-local. Prove one terminal decision/effect and one execution-success event, no new action/card/provider effect. A DB-only duplicate or expiry update is not real callback/tap proof. The second canary then exercises genuine expiry and late tap with null result/no execution event. Observe card resolution, real reaction, callback pickup/provenance and expired-tap behavior separately; none is inferred merely from API success.

Timing must match production: idle polling is 5 seconds, the natural claim lease is 30 seconds and heartbeat cadence 10 seconds. Private transport has a **10-second complete-call deadline**, including HTTP phases/teardown, and minted proof lasts no more than the lesser of 10 seconds and remaining lease. The row barrier blocks heartbeat renewal as well as completion, so holding a lock cannot extend source authority. A transport timeout is not proof of a failed provider send. Before confirmed Messenger evidence, timeout/expiry or an expired barrier budget means release and stop with unknown/ambiguous truth, never send again. Once confirmed and the source wait is proven, the single authorized crash tests durable confirmed-result reconciliation; neither the runner nor rollout may raise these production deadlines to make it pass.

If the bounded real barrier cannot be rehearsed reliably, keep the normal two canaries and offer an **explicit owner evidence-scope amendment**: use a separately isolated native-Postgres released-runtime kill/restart/duplicate/rollback drill for AC4(a,b,d), while retaining real canary and ten real workflow evidence. The spec permits an owner-authorized staging/canary drill, but the current bead also explicitly promises live dev fault evidence; isolation is not silently credited as that acceptance. A released-runtime isolated runner must be prepared and reviewed before use. A fake transport/test runtime is supplementary evidence, not actual owner delivery.

## 5. Safe evidence, ten genuine samples, and rollback

Use existing configured pool/role boundaries. A safe read transaction sets a short statement/lock timeout and projects only UUIDs, enum states/reasons, generation/fence numbers, counts and timestamps. Never output rendered text, recipients, arguments/digests, callback tokens/secrets, claim tokens, raw provider replies/exceptions, unfiltered logs or full envelopes. UUID-scoped queries should join `pending_actions` → `approval_delivery_intents` → direct initial presentations; attempts join on presentation ID/generation; Messenger handoffs use the matching tuple through Messenger's authority. Report `execution_result IS NOT NULL`, not its contents. Legacy evidence is `approval_push_emissions`; prove **zero new rows for the sampled action IDs**, not a null outcome or zero lifetime rows.

For the selected source's existing owning connection/search path, the read-only
evidence shape is below. Bind `action_id` privately from the approved packet;
never substitute a subject supplied by a model or a historical failed canary.

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '2s';
SET LOCAL lock_timeout = '250ms';
SELECT a.id, a.status, a.requested_at, a.expires_at,
       (a.execution_result IS NOT NULL) AS has_execution_result,
       i.id AS intent_id, p.id AS presentation_id,
       p.presentation_generation, p.presentation_mode, p.state, p.claim_fence
FROM pending_actions AS a
LEFT JOIN approval_delivery_intents AS i ON i.action_id = a.id
LEFT JOIN approval_delivery_presentations AS p ON p.intent_id = i.id
WHERE a.id = :'action_id'::uuid;
SELECT count(*) AS legacy_rows
FROM approval_push_emissions WHERE action_id = :'action_id'::uuid;
ROLLBACK;
```

Separately, Messenger's configured authority may project only `issuer`,
`owning_schema`, `presentation_generation`, `handoff_class`, `reason_code`,
`provider_started_at` and `completed_at` for the exact privately bound tuple.
Keep its subject/presentation keys and all provider/callback data inside that
process. Count matching rows explicitly; an empty query result is not success.

Observe at least ten **natural, newly parked real workflow approvals**, without manufacturing eight more requests. Keep ten qualifying normal single-mode/no-defer samples (UUID/time/schema only) so one initial presentation each can be counted directly; record naturally occurring quiet/burst/cohort/prepared behavior honestly alongside them. Do not turn legitimate collapsed cohort members into extra pushes to satisfy a sample count. Show one intent/initial presentation, matching delivery evidence and zero legacy rows for each chosen sample. Dashboard truth must show active/ambiguous/retained work accurately; provider confirmation means acceptance, not owner attention.

Admission-first rollback: set admission false in each activated schema using expected-state/singleton rowcount checks; retain all durable rows. Inspect in-flight states, allow compatible workers to settle or retain ambiguity, then set worker false and perform the necessary controlled cohost stop/restart. Do not delete rollout rows or assume worker=false cancels a running task. Show retained before/after UUID/state/count evidence; new post-disable actions receive no durable intent, without creating a throwaway action just to prove it. No legacy fallback.

To restore split topology, first stop the complete cohost and prove desired/observed zero and pods gone. Then use an explicitly reviewed `enabled=true` chart selection through the standard deployment path (preserving any existing local overlay); recheck old-image/bootstrap compatibility, split Switchboard destination, one control slot plus four domain slots, and no cohost competitor. Do not overlap. Independent-process RFC0023 authority is unavailable in that topology: leave admission/worker off and preserve outstanding work for a compatible cohost recovery binary. Restoring split does not authorize new transport credentials.

Do not change RFC 0023's status to enabled-in-dev until dated actual evidence satisfies all original criteria (or a verbatim owner amendment). Any failure, or fourteen days after drill authorization without complete evidence, returns evidence to the owner. No automatic retirement/deletion.

## Remaining concrete operator/author input

- Final merged source/image/chart pins and independent combined-head review/hosted merge-group evidence; owner-run privileges for the established deployment/bootstrap flow. An offline owner-run candidate is prepared separately from the active harness; no release manifest or executable handoff is ready until these pins and independent runner review exist.
- A reviewed durable adaptation of `/home/orca/.tmp/confirm-approval-canary-async`: remove `emit_approval_push`/legacy dispatch and its rollout-off requirement; park through normal durable admission; await the normal daemon worker; replace legacy-delivered and legacy-burst queries with intent/presentation/handoff and genuine policy/cohort evidence; retain exact target/source/runner pins, deduplication, preview, two-action bounds, old-failure exclusions and private receipts. Keep callback material private. Add no approval bypass.
- After the runbook is merged and the isolated rehearsal/runner review is concrete, record one bounded authorization covering Messenger flag transitions, the first-canary-only 20-second barrier and single cohost interruption, same-action duplicate decision, admission-first retention proof, and the fourteen-day deadline. The question must identify that the runtime is shared. If that cannot be made reliable, ask explicitly for the isolated evidence-scope amendment instead. Do not re-ask permission for the two standing canaries themselves or treat topology/downtime approval as drill approval.

## Implementation Notes

- Exclusive replacement/cap/topology: `docs/architecture/key-absent-control.md:63–103`; authority cohosting/PID contract: `docs/runtime/approval-delivery-authority.md:11–27`.
- Build/standard deploy/bootstrap/ledger: `scripts/k8s/deploy-dev.sh:30–40`, `deploy/helm/butlers/makefile:30–45`, `scripts/k8s/bootstrap-db.sh:39–54`, `scripts/k8s/record-deploy.sh:47–74`; API migration init: `deploy/helm/butlers/templates/dashboard-api.yaml:21–27`; reviewed bootstrap: `docs/data_and_storage/reviewed-bootstrap.md:3–43`.
- Full chain selection/schema overrides and dry-run limit: `src/butlers/cli.py:667–731`; chain/head reads: `src/butlers/migrations/__init__.py:280–326`; backup unknown/freshness: `docs/operations/backup-restore.md:48–81`; Kubernetes backup: `deploy/helm/butlers/templates/cronjobs.yaml:1–42`.
- Row barrier ordering: `src/butlers/core/approval_delivery_worker.py:229–250,314–328`; lock-free source proof: `src/butlers/modules/approvals/delivery_recovery.py:106–179`; committed source start/heartbeat/completion: same file `440–534`; independent Messenger provider/confirmation: `src/butlers/core/approval_delivery_transport.py:200–230,239–315`.
- Default-off/worker startup: `src/butlers/modules/approvals/rollout.py:17–45`, `src/butlers/core/approval_delivery_worker.py:372–405`; receiver routing: `docs/operations/receiver-derived-routing-cutover.md:4–16,80–88`.
- Governing activation/new-actions-only/retained-data rollback: `openspec/specs/approval-delivery-intent-recovery/spec.md:258–277`; genuine quiet/burst/prepared behavior: same file `163–183,242–251`; RFC status remains separately gated at `about/legends-and-lore/rfcs/0023-durable-approval-delivery-intent-recovery.md:3`.

## Canonical owner delivery flag (separate gate)

`public.owner_delivery_rollout.enabled` (core_317, default `false`) is a separate gate from
the RFC 0023 rollout rows and is **not** part of this runbook's activation. Enabling it, the
`bridge_existing` backfill and the `parity` check that must precede it are described in
[Owner delivery](../api_and_protocols/owner-delivery.md#rfc-0023-successor-map-and-cutover)
and need their own authorization. Include its value in the safe baselines of step 1.6.

The flag governs minting only. Turning it off stops new `od1` tokens, but tokens already
minted stay redeemable (their decisions still go through `transition_pending_action`) and the
connector's retraction drain keeps running. A rollback therefore leaves live `od1` cards
working until their actions close; see
[Flag semantics](../api_and_protocols/owner-delivery.md#flag-semantics).

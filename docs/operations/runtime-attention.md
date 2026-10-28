# Runtime Attention

> **Purpose:** Check and repair the runtime-attention paging path and its purpose-owned stored
> functions it depends on.
> **Audience:** Operators.
> **Prerequisites:** [Model Routing](../runtime/model-routing.md) (breakers, dispatch attempts).

## What it is

A model breaker opening, or the first fleet-halt denial in a UTC month, creates one durable
*episode* in `public.runtime_attention_outbox`, committed in the same transaction as the
`public.model_dispatch_attempts` row that caused it. A delivery service leases and sends pending
episodes. Producers, the outbox, and the delivery lease are database-global
objects owned by the isolated `runtime_attention_outbox_owner` purpose. The
explicit held migrator installs/converges the exact source definitions; neither
serving startup nor the ordinary migration owner can rewrite protected state.
Bootstrap prepares the declared roles/extensions only. It is not a paging API.

The independent fleet and QA-patrol controller is a third producer (`core_251`). See
[Fleet and QA condition attention](#fleet-and-qa-condition-attention).

Operators read episodes through the Models page (`GET /api/settings/models/attention`, with
deliberate reissue of an `uncertain` original) and the Spend page
(`GET /api/spend/runtime-attention`). Both are content-blind projections. See
[Operator attention](../runtime/model-routing.md#operator-attention-and-deliberate-reissue).

## Fleet and QA condition attention

REQ-butler-control-plane-liveness-007. The Dashboard's supervised receiver observer runs the
[fleet condition controller](fleet-condition-controller.md) after every cycle. The controller calls
`public.append_runtime_attention_condition(condition_id)` under `SET ROLE butler_switchboard_rw`
for each active `control_plane_fleet` or `qa_patrol_assurance`/`patrol_overdue` episode in
`public.infra_conditions`. No QA patrol, QA daemon, model session, or browser request is involved.

- **One episode per condition episode.** The producer appends a `control_plane_condition` outbox
  row once the condition has been active for five minutes, which is its attention grace. The
  controller's next pass, at most half the registry TTL later, does it. A unique index on the
  snapshot's `condition_id` and an advisory lock make concurrent scans, restarts, and retries after
  an interrupted append return the same episode. Resolution before the grace ends pages nothing.
  L2, L3, and repeat escalation stay on the same condition row, so they never add a page.
- **Content-blind.** The snapshot holds only `condition_id`, `condition_kind`
  (`fleet_control` or `qa_patrol_overdue`), and `first_detected_at`. The payload is a fixed
  classification (`fleet_control_unhealthy` or `qa_patrol_overdue`) plus the `/system` door. CHECK
  constraints enforce both. Daemon names, endpoints, summaries, and errors never enter the outbox.
  Switchboard's existing fenced worker delivers the episode with fixed copy.
- **Retention cannot re-page.** `public.runtime_attention_condition_episodes` is the condition-side
  marker. It keeps the emitted episode id and the last delivery state after the outbox row ages
  out, and the producer returns that id rather than minting a new one. Since `core_253` the
  delivery state mirrors every lifecycle transition, including one after the condition resolved:
  an owner-run AFTER UPDATE trigger (`runtime_attention_condition_marker_sync_trigger`, definer
  `public.runtime_attention_sync_condition_marker()`, pinned to `pg_catalog, pg_temp`) updates it
  in the worker's own transaction. The worker role gains no marker privilege.
  The explicit source-owned `attention-transition` marker operation removes or
  reinstalls only that exact trigger/callable, preserves every marker and
  backfills retained marker delivery state from actual matching outbox rows.
  The retired v5 administrative function is not a supported caller.
- **A restore may re-page once.** The standard backup excludes both
  `public.runtime_attention_condition_episodes` and `public.runtime_attention_outbox`
  (owner decision `bu-43lod8`, option A). After a valid restore that loses both, once the
  protected interfaces and normal producer/controller are available, a still-active eligible
  fleet or overdue-QA condition may create one new episode and page the owner once more.
  Existing mandatory append/admission and fenced delivery rules still apply; external delivery
  is not guaranteed. After the new marker exists, repeated scans, concurrent appends, retries
  and controller restarts return the same episode. This allowance is per restored state that
  loses both records, not an ordinary retention or escalation re-page, and it does not authorize
  automatic replay of a surviving `uncertain` episode. Re-enabling with retained markers also
  cannot re-page. Another loss of both records in a later restore is a separate restored state.
  The existing [restore ownership precondition](backup-restore.md#ownership-precondition)
  still applies; a restore does not confer bootstrap authority.
- **Owner status.** `GET /api/system/conditions` attaches `attention` to each fleet and overdue-QA
  row. It reads the content-blind `public.observe_runtime_attention_conditions()` projection,
  which requires the fixed active `dashboard_api` purpose. A normal migration
  login, role-none caller or runtime capability cannot substitute. `status` is
  one of:

  | `status` | Meaning |
  | --- | --- |
  | `pending` | Appended, not yet claimed, and a delivery worker holds a live lease |
  | `worker_unavailable` | Appended, but no live delivery lease exists, so nothing is sending it |
  | `sending` | Claimed and the outcome is unknown. This is never "delivered" |
  | `sent` | Transport confirmed |
  | `failed` | Definitively not delivered: retries exhausted before transport, or rejected |
  | `uncertain` | Might have arrived. It is never resent automatically |
  | `unavailable` | The projection itself could not be read, which is not an all-clear |
  | `not_raised` | No marker exists and the condition outlived the attention grace plus one controller pass, so nothing paged the owner. The producer being disabled reads the same way |
  | `within_grace` | No marker yet, but the producer is not yet due to page. Calm |

  `not_raised` and `within_grace` are derived by the API for rows with no marker, not stored. The
  bound mirrors the producer's five-minute grace (`CONDITION_ATTENTION_GRACE`, pinned to
  the current source function by a test) plus half the registry TTL for one controller pass. A row
  resolved inside that bound gets no attention line; one resolved after it keeps `not_raised`.
  The allowance assumes the default 300 s registry TTL (150 s); if an operator raises the TTL it
  must track min(live TTL) / 2, and no extra margin is added.

  `safe_reason` maps the stored `(delivery_error_class, delivery_error_detail)` pair to fixed copy
  (`src/butlers/api/runtime_attention_status.py`). The Spend page uses the same mapping. The
  System page Standing Conditions tile renders this status on each row with fixed copy, where
  only `sent` reads as delivered, and `unavailable` or an active `not_raised` row marks the tile degraded.
- **Retries.** Only a proven pre-transport failure retries, and only inside the worker's bounded
  backoff. Nothing reissues an uncertain condition episode, and the operator reissue in
  `reissue_runtime_attention_episode` covers model-breaker episodes only.

To check it, query the projection as the dashboard login:

```sql
SELECT condition_kind, episode_id, lifecycle_state, outbox_retained, delivery_worker_live
FROM public.observe_runtime_attention_conditions();
```

A `Fleet condition controller: attention append failed (category=...)` warning names only the
exception type. `UndefinedFunction` means the database has not reached `core_251`.
`InsufficientPrivilegeError` requires checking the actual selected purpose:
condition append uses Switchboard's fixed producer, while observation uses the
dashboard capability. It never authorizes a shared migration credential.
## Definer search path

Every runtime-attention `SECURITY DEFINER` function runs with exactly `search_path = pg_catalog, pg_temp`, and schema-qualifies relations and non-catalog calls. Fixed purpose ownership and exact EXECUTE ACLs are part of the same source catalog contract. The explicit controlled migrator installs and verifies current bodies; startup, diagnostics and `scripts/init-db.sql` do not run a retired administrative finalizer.

## Check

- **Recorder health.** `runtime_attention_recorder_total{outcome,edge}` reports recorder results.
  `outcome=persisted` together with `edge=model_breaker_unauthorized` or `edge=fleet_halt_unauthorized`
  means the attempt row committed but the producer refused the call (SQLSTATE `42501`). The pool
  holds no canonical `butler_*_rw` `SET ROLE`. This is an admission/configuration
  fault; there is no development continuation under a shared owner.
- **Database-authority catalog.** `GET /api/system/stored-functions` reports the current source-derived authority comparison. `conforming` requires the complete expected overload population, exact body digests/settings/owners/EXECUTE and object catalog; `missing`, `mismatch`, `unavailable` and `unknown` cannot establish serving admission. The endpoint is read-only and emits closed identities/categories/digests, not stored bodies, row contents or driver errors. There is no bootstrap-file parser, source-path environment override or accepted historical body alternative.
- **Definer search path.** The endpoint also reports application definers whose path is not exactly `pg_catalog, pg_temp`. An unavailable catalog read is not an all-clear. `controlled_migrator` identifies the source-owned repair route; it does not infer authority from the dashboard's connecting login. Run the complete explicit migration/finalization procedure under its separately provisioned maintenance profile, then independently read back the catalog before serving. Do not use ad hoc ALTERs or caller grants to mask a mismatch.
- **Retained audit history.** The legacy debounce planter/trigger is retired;
  current validated producers do not create new legacy marker rows. Existing
  `runtime_attention_legacy_debounce_marker` and
  `runtime_attention_cutover_fence` vocabularies remain unchanged. Historical
  queries must still recognize both, including the original UTC `YYYY-MM`
  ceiling note. Removing the planter does not authorize rewriting that history.

## Repair catalog mismatch

Stop dependent serving admission, identify the closed mismatch category and verify the exact code/source and operator prerequisites. Use the explicit `butlers db migrate` action with the selected maintenance credential reference; it retains one session across declared chains, final-state installation, ACL convergence and verification. Never copy a secret into a command or diagnostic. A failed apply, commit or physical release is incomplete. Independently verify exact catalog and native serving-profile behavior before restarting traffic. See [the cutover procedure](../identity_and_secrets/database-authority-cutover.md).

## Stop paging

Application shutdown stops new local producer/worker work and joins its owned transactions and physical release before dependent pools close. It does not erase durable outbox, claim, lease, attempt, marker, cancellation or unknown-once evidence. A process stop alone cannot certify every other producer is stopped.

An ordinary schema downgrade is not the operator stop/reverse interface. Protected installed boundaries refuse crossing before later autocommit effects; a core_282 reverse requires the explicit captured preserving procedure. R0/R1 separately prove the pinned predecessor initializer, exact revision vector, source-bound trigger transition, retained rows/receipts and old-profile grants before an operator restores the captured code/chart/secret reference. Do not run retired `runtime_attention_admin` deactivation functions or delete marker/history rows to make a downgrade pass. A database-only restamp is not a stop-paging guarantee. This branch's source/software checks do not establish an executed live rollback or authorize an operator action.

## Database-authority source and deployment boundary

The explicit `butlers db attention-transition` command accepts a fixed
`--component` (`producers`, `reissue`, `conditions` or `marker`) and `--state`
(`enabled` or `disabled`). It selects only `BUTLERS_BOOTSTRAP_DATABASE_URL`,
requires the existing maintenance prerequisite, held migration lock and proven
serving quiescence, then validates the complete current source/function/owner/
ACL/relation shape before effects. Each change is atomic and retains episodes,
attempts, claims, leases and history. Producer reenable starts a new activation
cutoff without replaying preactivation failures; reissue reenable retains the
one-successor and unknown-once fences. Marker disable/reinstall previews the
exact full shape in a rolled-back savepoint and does not exempt arbitrary
missing triggers or policies. A partial/poisoned catalog refuses. In disposable
proof, an enumerated fixture-owned poison may be undone only by its exact
inverse before the real finalizer and all original positive operations run.
This does not create a generic production policy-repair API. Success is reported
only after commit and physical session release. Real PostgreSQL transition,
backfill and concurrency proof remains required before use.

This branch moves the bootstrap-owned attention installer into controlled final-state migrations; genuine migrated-role and preserving-rollback proof remains required before delivery. It does not activate a new attention delivery path or change the current producer/worker protocol. See [the cutover procedure](../identity_and_secrets/database-authority-cutover.md).

The model-breaker, fleet-halt, operator and condition episode/marker interfaces retain fixed server-derived inputs, exact purpose ownership/EXECUTE/search_path, append-only evidence, idempotent outbox representation and independent database-time/currentness fences. Existing Switchboard lifecycle/claim columns and delivery lease, at-most-once attempt/unknown outcome, owner stop, cancel and ACK behavior remain exact. Removing v2-v5/interface_version/legacy-debounce branches cannot remove these outcomes or authorize raw producer DML. No historical incident backfill or second transport is introduced.

The dashboard observer uses its constrained dashboard pool and exact reserve/record functions. Removing DashboardProbeRoleView and ambient role-none acceptance must retain actual epoch/sequence/nonce/CAS and owner-policy checks. The runtime-probe signing-key plane is held unchanged, and the owner rehome question occurs only at actual completed rebuild closure. Catalog diagnostics distinguish unavailable, absent and mismatch without exposing stored bodies or row contents.

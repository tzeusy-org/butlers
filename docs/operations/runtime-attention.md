# Runtime Attention

> **Purpose:** Check and repair the runtime-attention paging path and the bootstrap-owned stored
> functions it depends on.
> **Audience:** Operators.
> **Prerequisites:** [Model Routing](../runtime/model-routing.md) (breakers, dispatch attempts).

## What it is

A model breaker opening, or the first fleet-halt denial in a UTC month, creates one durable
*episode* in `public.runtime_attention_outbox`, committed in the same transaction as the
`public.model_dispatch_attempts` row that caused it. A delivery service leases and sends pending
episodes. Producers, the outbox, and the delivery lease are database-global objects that the
privileged bootstrap (`scripts/init-db.sql`) installs and owns, not Alembic. The ordinary migration
role cannot rewrite them.

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
  marker. It keeps the emitted episode id and the last delivery state it saw after the outbox row
  ages out, and the producer returns that id rather than minting a new one.
- **Owner status.** `GET /api/system/conditions` attaches `attention` to each fleet and overdue-QA
  row. It reads the content-blind `public.observe_runtime_attention_conditions()` projection,
  which is granted to the migration and dashboard login only and refuses `SET ROLE`. `status` is
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

  `safe_reason` maps the stored `(delivery_error_class, delivery_error_detail)` pair to fixed copy
  (`src/butlers/api/runtime_attention_status.py`). The Spend page uses the same mapping.
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
`InsufficientPrivilegeError` means the Dashboard pool cannot `SET ROLE butler_switchboard_rw`.
## Definer search path

Every runtime-attention `SECURITY DEFINER` function runs with
`search_path = pg_catalog, pg_temp`, and its body schema-qualifies each relation. The migration
login can `CREATE` in `public`. With `public` on a definer's path, it could plant a better-matching
overload, such as `public.hashtextextended(text, integer)` for a bare `0` seed, and run that
overload as `runtime_attention_outbox_owner`. `runtime_attention_admin.finalize_interface()`
re-applies the pin on every init-db rerun. That rerun also pins the v3 operator functions. A
database bootstrapped before this rule keeps `public` on the path until the bootstrap runs again
(see [Repair drift](#repair-drift)). A new definer must use the same path.

## Check

- **Recorder health.** `runtime_attention_recorder_total{outcome,edge}` reports recorder results.
  `outcome=persisted` together with `edge=model_breaker_unauthorized` or `edge=fleet_halt_unauthorized`
  means the attempt row committed but the producer refused the call (SQLSTATE `42501`). The pool
  holds no canonical `butler_*_rw` `SET ROLE`. That is expected on a non-hardened stack, and a
  misconfiguration wherever role enforcement should be active.
- **Stored-function drift.** `GET /api/system/stored-functions` compares every function defined in
  the bootstrap source against `pg_proc.prosrc`. The same check logs one WARNING per drifted
  function at dashboard-api startup (`src/butlers/core/stored_function_drift.py`). `drifted` means
  the deployed body differs from the committed one, ignoring whitespace. `not_deployed` means the
  bootstrap installer has not run yet. The source defaults to `scripts/init-db.sql`; set
  `STORED_FUNCTION_DRIFT_INIT_DB_SQL_PATH` when it is mounted elsewhere. The check only reports.
  It never converges anything.
- **Audit markers.** `public.runtime_attention_plant_legacy_debounce_marker()` plants
  `public.audit_log` rows for runtimes that lack the current recorder ABI marker. Rows carry actor
  `runtime_attention_legacy_debounce_marker`, and older rows carry `runtime_attention_cutover_fence`.
  Both vocabularies persist, so any query that filters on actor must accept both. The ceiling
  branch's note is the UTC month as `YYYY-MM`. Do not reformat it.

## Repair drift

Re-run the bootstrap as a cluster superuser:

```bash
psql -h <host> -U postgres -d butlers -f scripts/init-db.sql
```

`runtime_attention_admin.finalize_interface()` runs on every rerun and re-adopts the bodies defined
in `runtime_attention_admin.install_legacy_debounce_marker()`. An Alembic deploy does not do this,
so a database keeps its old bodies until someone runs the bootstrap again.

## Stop paging

Downgrade `core_199` alone. It must run as a cluster superuser, because it calls
`runtime_attention_admin.deactivate_producers_v2()`. The downgrade clears `producers_enabled`, so
episode production stops. The outbox, the delivery lease, attempt rows,
`public.runtime_attention_producer_control`, and the marker trigger all stay in place. Repair from
there is forward remediation only.

To stop condition paging alone, downgrade `core_251` as a cluster superuser. It calls
`public.runtime_attention_deactivate_condition_v4()`, which clears
`runtime_attention_condition_control.producer_enabled`. The same downgrade as the ordinary migration
login never refuses: it only restamps and logs a warning, because no code before `core_251` calls the
producer. Clear the flag separately if new code must stay deployed. Emitted episodes, their markers, and their
delivery truth all stay. Re-enabling with an `UPDATE` as bootstrap pages only conditions that have no
marker, so an active condition that already paged is never re-armed.

Downgrading below `core_198` is not supported on any database that has reached `core_199`. The
`core_198` downgrade fails with `core_198 downgrade requires trusted bootstrap rollback interface`
because the retained objects are present, and nothing restores the pre-outbox schema.

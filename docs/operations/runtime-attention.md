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

Operators read episodes through the Models page (`GET /api/settings/models/attention`, with
deliberate reissue of an `uncertain` original) and the Spend page
(`GET /api/spend/runtime-attention`). Both are content-blind projections. See
[Operator attention](../runtime/model-routing.md#operator-attention-and-deliberate-reissue).

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

Downgrading below `core_198` is not supported on any database that has reached `core_199`. The
`core_198` downgrade fails with `core_198 downgrade requires trusted bootstrap rollback interface`
because the retained objects are present, and nothing restores the pre-outbox schema.

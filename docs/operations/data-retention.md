# Data Retention Policy

The explicit retention decision for every long-lived or high-growth table in the Butlers
database.

**Governing principle:** This is irreplaceable personal data.  No automated
deletion runs without explicit owner consent.  "Keep-forever" decisions are
positive choices, not defaults-by-omission.  Any pruning mechanism MUST be
opt-in, dry-run capable, and owner-gated before it touches live data.  Indexes
live in the migrations; this page records only the decisions and the query
budgets that depend on them.

---

## Table-by-table decisions

### `{butler_schema}.sessions`

**Decision: KEEP FOREVER**

Agent interaction history.  This is the owner's personal record of every
conversation, task, and automation run.  No automated pruner.  Its companion
`session_process_logs` has its own TTL (below).

---

### `public.ingestion_events`

**Decision: KEEP FOREVER**

Ingestion audit trail.  Every inbound event (Telegram, email, webhook, etc.)
is recorded here before routing.  This is the owner's durable inbox history.
No automated pruner.

---

### `public.audit_log`

**Decision: KEEP FOREVER**

Append-only security and compliance log.  Any deletion would create gaps in the
security record.  No automated pruner.

Query-budget note: `GET /api/audit-log` fires a `SELECT count(*) ... {where}`
before pagination.  For filtered queries (actor, target, action) the composite
indexes keep this bounded.  An unfiltered `count(*)` is O(N), so callers should
supply at least one filter when auditing large installations.

---

### `switchboard.notifications`

**Decision: KEEP FOREVER**

Outbound delivery record.  Every notification (Telegram, email, etc.) sent
by any butler is logged here.  This is the owner's delivery history.
No automated pruner.

Query-budget note: the stats endpoint (`GET /api/notifications/stats`) fires
4+ COUNT queries per visit, including a terminal-failure self-join whose EXISTS
subquery relies on the partial `session_id` index to stay an index scan.

---

### `{butler_schema}.session_process_logs`

**Decision: TTL 14 DAYS — pruner [A], disabled by default**

Process/execution logs for in-flight and completed sessions.  High-velocity
write path.  The schema sets a 14-day `expires_at` default; the pruner reaps
rows that have already expired.

---

### `{butler_schema}.approval_rules` / `approval_events`

**Decision: SPLIT RETENTION WINDOW — inactive rules after 180 days; immutable audit events after 365 days**

When an owner-approved retention job runs, `cleanup_old_rules()` may delete only
rules where `active = false` and the rule is older than 180 days. The linked
`approval_events` row is not cascaded, deleted, or rewritten: its `rule_id`
remains queryable historical provenance until the event reaches the separate
365-day audit retention window. Rerunning rule cleanup after a successful
deletion is idempotent and does not change retained audit rows.

This is not a validation bypass. A new approval event with a non-null `rule_id`
must still reference a live approval rule when it is inserted. Only previously
valid immutable audit history may outlive the rule it records. Event cleanup is
separate, requires the explicit privileged path, and applies only after its
365-day window.

---

### `connectors.filtered_events`

**Decision: MONTHLY PARTITIONED — keep 12 months; pruner [B], disabled by default**

Connector event buffer, partitioned by month.  Without the pruner, old
partitions accumulate.

---

### `public.insight_candidates`

**Decision: STATUS-GATED — terminal rows after 90 days; pruner [C], disabled by default**

Candidates move from `pending` to a terminal status (`delivered`, `filtered`,
`expired`, `withdrawn`).  Only terminal rows are eligible for cleanup; `pending` rows are
never touched.

---

### `public.insight_amendments`

**Decision: BOUNDED BY INSIGHT LIFECYCLE**

One row per (delivered candidate, premise resolution), removed by `ON DELETE
CASCADE` when the candidate is pruned. No independent retention concern.

---

### `public.insight_engagement` / `insight_cooldowns`

**Decision: BOUNDED BY INSIGHT LIFECYCLE**

These tables are bounded in practice by the number of delivered insights.
No independent retention concern at current volumes.

---

### `public.secret_probe_log`

**Decision: 90+ DAYS — pruner [D], disabled by default**

The table's contract is "retention ≥ 90 days"; no archive path is defined, so
the pruner discards rows.  If an export is needed before deletion, add a
separate export step before enabling it.

---

### Memory tables (`{butler_schema}.episodes`, `episode_chunks`, `entity_*`, `relations`, etc.)

**Decision: RETENTION-CLASS GOVERNED**

Episodes carry a `retention_class`, and the memory module applies its own
retention policies (see [Memory](../modules/memory.md)).  This page defers to
that framework.

---

## Pruners

All four live in `src/butlers/jobs/retention.py` and are wired as deterministic
scheduled jobs on the `general` butler (`src/butlers/scheduled_jobs.py`).  Each is a
no-op unless `enabled = true`, and each defaults to `dry_run = true`, which only
reports what it would delete.  Enable one by adding a scheduled task in
`roster/general/butler.toml` with the `job_name` and `job_args` below.

**[A] `session_process_logs_prune`** — `prune_session_process_logs()` deletes
rows where `expires_at < now()` in the running butler's own schema.  Unlike the
other three, it is registered on every butler that has session logs and is
enabled in that butler's own `butler.toml`.  It never reaches another butler's
schema: an optional `job_args.schema` must equal the butler's name, and any other
value is refused before a statement runs.
`job_args = {enabled = true, dry_run = false}`.

**[B] `filtered_events_partition_prune`** — `prune_filtered_events_partitions()`
drops monthly partitions older than `keep_months` (default 12); dry-run lists
the eligible partitions.
`job_args = {enabled = true, dry_run = false, keep_months = 12}`.

**[C] `insight_candidates_prune`** — `prune_insight_candidates()` deletes
terminal-status rows older than `ttl_days` (default 90).
`job_args = {enabled = true, dry_run = false, ttl_days = 90}`.

**[D] `secret_probe_log_prune`** — `prune_secret_probe_log()` deletes rows whose
`recorded_at` is older than `ttl_days`.  It enforces the 90-day floor:
`ttl_days < 90` raises `ValueError`.
`job_args = {enabled = true, dry_run = false, ttl_days = 90}`.

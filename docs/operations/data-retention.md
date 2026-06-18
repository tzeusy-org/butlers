# Data Retention Policy

This document records the explicit retention decision for every long-lived
or high-growth table in the Butlers database.  It was produced during the
bu-dl98i.7.6 retention audit (2026-06-18).

**Governing principle:** This is irreplaceable personal data.  No automated
deletion runs without explicit owner consent.  "Keep-forever" decisions are
positive choices, not defaults-by-omission.  Any future pruning mechanism
MUST be opt-in, dry-run capable, and owner-gated before it touches live data.

---

## Table-by-table decisions

### `{butler_schema}.sessions`

**Decision: KEEP FOREVER**

Agent interaction history.  This is the owner's personal record of every
conversation, task, and automation run.  No automated pruner.

Retention notes:
- `session_process_logs` (companion table, per-butler) has a 14-day TTL via
  `expires_at`.  The TTL column exists and is set correctly, but no automated
  pruner yet runs.  See Follow-up [A] below.

Indexes added by core_128:
- `ix_sessions_started_at ON sessions (started_at DESC)` — list/timeline fan-out
- `ix_sessions_completed_at ON sessions (completed_at DESC) WHERE completed_at IS NOT NULL` — activity feed

---

### `public.ingestion_events`

**Decision: KEEP FOREVER**

Ingestion audit trail.  Every inbound event (Telegram, email, webhook, etc.)
is recorded here before routing.  This is the owner's durable inbox history.
No automated pruner.

Indexes (existing, from core_001):
- `ix_ingestion_events_received_at ON ingestion_events (received_at DESC)`
- `ix_ingestion_events_source_channel ON ingestion_events (source_channel, received_at DESC)`
- `ix_ingestion_events_status ON ingestion_events (status) WHERE status != 'ingested'`

---

### `public.audit_log`

**Decision: KEEP FOREVER**

Append-only security and compliance log.  Declared append-only by policy
(core_092 migration docstring).  Any deletion would create gaps in the
security record.  No automated pruner.

Indexes (existing):
- `idx_audit_log_ts_desc ON audit_log (ts DESC)`
- `idx_audit_log_action ON audit_log (action)`
- `idx_audit_log_actor ON audit_log (actor)`
- `ix_audit_log_target_ts ON audit_log (target, ts DESC)` (core_105)

Query-budget note: `GET /api/audit-log` fires a `SELECT count(*) ... {where}`
before pagination.  For filtered queries (actor, target, action) the existing
composite indexes keep this bounded.  An unfiltered `count(*)` is O(N) — the
endpoint already documents that callers should always supply at least one
filter when auditing large installations.

---

### `switchboard.notifications`

**Decision: KEEP FOREVER**

Outbound delivery record.  Every notification (Telegram, email, etc.) sent
by any butler is logged here.  This is the owner's delivery history.
No automated pruner.

Indexes (existing, from sw_001):
- `idx_notifications_source_butler_created`
- `idx_notifications_channel_created`
- `idx_notifications_status`

Index added by sw_016:
- `ix_notifications_session_id ON notifications (session_id) WHERE session_id IS NOT NULL`

Query-budget note: The stats endpoint (`GET /api/notifications/stats`) fires
4+ COUNT queries per visit, including a terminal-failure self-join.  With
ix_notifications_session_id the self-join's EXISTS subquery uses an index
scan.  See sw_016 migration docstring for full budget analysis.

---

### `{butler_schema}.session_process_logs`

**Decision: TTL 14 DAYS — pruner implemented, disabled by default (see Follow-up [A])**

Process/execution logs for in-flight and completed sessions.  High-velocity
write path.  The schema declares a 14-day `expires_at` column with a correct
default.

Current state: `butlers.jobs.retention.prune_session_process_logs()` implements
the sweep but is **disabled by default**.  Enable per-butler by adding a scheduled
task in `butler.toml` with `job_name = "session_process_logs_prune"` and
`job_args = {enabled = true, dry_run = false, schema = "<butler_name>"}`.
Dry-run logs candidate count without deleting.

---

### `connectors.filtered_events`

**Decision: MONTHLY PARTITIONED — old partition pruner implemented, disabled by default (see Follow-up [B])**

Connector event buffer.  Partitioned by month (`core_007`).  Old partitions
accumulate.

Current state: `butlers.jobs.retention.prune_filtered_events_partitions()` implements
a partition DROP sweep but is **disabled by default**.  Enable on the `general` butler
with `job_name = "filtered_events_partition_prune"` and
`job_args = {enabled = true, dry_run = false, keep_months = 12}`.
Default policy: keep the most recent 12 months.  Dry-run lists eligible partitions
without dropping.

---

### `public.insight_candidates`

**Decision: STATUS-GATED KEEP / EXPIRES-AT for delivered rows — pruner implemented, disabled by default (see Follow-up [C])**

Insight candidates have an `expires_at` column and a `status` field
(`pending` → `delivered` / `filtered`).  The schema supports TTL-based
cleanup of delivered/filtered candidates via a partial index on
`(created_at) WHERE status <> 'pending'` (core_010).

Current state: `butlers.jobs.retention.prune_insight_candidates()` implements the
sweep but is **disabled by default**.  Enable on the `general` butler with
`job_name = "insight_candidates_prune"` and
`job_args = {enabled = true, dry_run = false, ttl_days = 90}`.
Only terminal-status rows (`delivered`, `filtered`, `expired`) older than
`ttl_days` are eligible.  `pending` rows are never touched.

---

### `public.insight_engagement` / `insight_cooldowns`

**Decision: BOUNDED BY INSIGHT LIFECYCLE**

These tables are bounded in practice by the number of delivered insights.
No independent retention concern at current volumes.

---

### `public.secret_probe_log`

**Decision: 90+ DAYS (core_105 spec) — pruner implemented, disabled by default (see Follow-up [D])**

The core_105 migration docstring declares "Retention: ≥ 90 days (archive
path not specified here)".

Current state: `butlers.jobs.retention.prune_secret_probe_log()` implements the
sweep but is **disabled by default**.  Enable on the `general` butler with
`job_name = "secret_probe_log_prune"` and
`job_args = {enabled = true, dry_run = false, ttl_days = 90}`.
The pruner enforces the spec minimum: `ttl_days < 90` raises `ValueError`.
Dry-run logs candidate count without deleting.

---

### Memory tables (`{butler_schema}.episodes`, `episode_chunks`, `entity_*`, `relations`, etc.)

**Decision: RETENTION-CLASS GOVERNED**

The memory subsystem has a `retention_class` column on episodes and a
`memory_policies` table.  These are governed by the memory butler's own
retention logic (LRU-based promotion/eviction per MEMORY_PROJECT_PLAN.md).
Not covered by this audit — defer to the memory butler's policy framework.

---

## Follow-up items

**[A] session_process_logs pruner — IMPLEMENTED (bu-2nlt4)**
`butlers.jobs.retention.prune_session_process_logs()` sweeps rows where
`expires_at < now()`.  Registered as `session_process_logs_prune` in every
butler's scheduled-job registry.  **Disabled by default** — enable per butler
via `job_args = {enabled = true, dry_run = false, schema = "<butler_name>"}`.

**[B] filtered_events old partition pruning — IMPLEMENTED (bu-2nlt4)**
`butlers.jobs.retention.prune_filtered_events_partitions()` drops monthly
partitions older than `keep_months` (default: 12).  Registered as
`filtered_events_partition_prune` on the `general` butler.  **Disabled by default.**
Enable via `job_args = {enabled = true, dry_run = false, keep_months = 12}`.

**[C] insight_candidates / engagement pruning — IMPLEMENTED (bu-2nlt4)**
`butlers.jobs.retention.prune_insight_candidates()` removes terminal-status rows
older than `ttl_days` (default: 90).  Registered as `insight_candidates_prune`
on the `general` butler.  **Disabled by default.**
Enable via `job_args = {enabled = true, dry_run = false, ttl_days = 90}`.

**[D] secret_probe_log 90-day pruning — IMPLEMENTED (bu-2nlt4)**
`butlers.jobs.retention.prune_secret_probe_log()` deletes rows older than
`ttl_days` (minimum 90, per core_105 spec).  Registered as
`secret_probe_log_prune` on the `general` butler.  **Disabled by default.**
Enable via `job_args = {enabled = true, dry_run = false, ttl_days = 90}`.
Note: no archive path was specified in the original spec; the pruner discards
rows.  If an export path is needed before deleting, implement a separate export
step before enabling deletion.

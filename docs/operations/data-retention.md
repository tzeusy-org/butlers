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

**Decision: TTL 14 DAYS — pruner needed (see Follow-up [A])**

Process/execution logs for in-flight and completed sessions.  High-velocity
write path.  The schema declares a 14-day `expires_at` column with a correct
default, but no automated pruner currently runs.

Current state: rows accumulate indefinitely until a pruner is wired up.
This is a low-risk gap (process logs, not personal interaction records) but
will cause unbounded table growth on active butlers.

---

### `connectors.filtered_events`

**Decision: MONTHLY PARTITIONED — old partition pruning needed (see Follow-up [B])**

Connector event buffer.  Partitioned by month (`core_007`).  Old partitions
accumulate — there is no `DROP PARTITION` sweep.

Current state: all historical monthly partitions are retained.  This is safe
(no data loss risk) but will grow unboundedly.  Partition pruning is a
follow-up once a policy on connector event history depth is agreed.

---

### `public.insight_candidates`

**Decision: STATUS-GATED KEEP / EXPIRES-AT for delivered rows**

Insight candidates have an `expires_at` column and a `status` field
(`pending` → `delivered` / `filtered`).  The schema supports TTL-based
cleanup of delivered/filtered candidates via a partial index on
`(created_at) WHERE status <> 'pending'` (core_010).

Current state: no automated pruner runs.  Delivered and expired candidates
accumulate.  See Follow-up [C].

---

### `public.insight_engagement` / `insight_cooldowns`

**Decision: BOUNDED BY INSIGHT LIFECYCLE**

These tables are bounded in practice by the number of delivered insights.
No independent retention concern at current volumes.

---

### `public.secret_probe_log`

**Decision: 90+ DAYS (core_105 spec)**

The core_105 migration docstring declares "Retention: ≥ 90 days (archive
path not specified here)".  No pruner implemented yet.
See Follow-up [D].

---

### Memory tables (`{butler_schema}.episodes`, `episode_chunks`, `entity_*`, `relations`, etc.)

**Decision: RETENTION-CLASS GOVERNED**

The memory subsystem has a `retention_class` column on episodes and a
`memory_policies` table.  These are governed by the memory butler's own
retention logic (LRU-based promotion/eviction per MEMORY_PROJECT_PLAN.md).
Not covered by this audit — defer to the memory butler's policy framework.

---

## Follow-up items (deferred, no auto-delete before owner confirms)

These were identified during the audit but excluded from this PR to keep the
scope additive-only.  Each needs a separate bead and explicit owner sign-off
before any pruning code ships.

**[A] session_process_logs pruner**
Wire an automated expiry sweep that deletes rows where `expires_at < now()`.
The TTL column and default are already correct — only the sweep is missing.
Suggested mechanism: a scheduled task on each butler, or a shared cron-driven
background task.  Must be dry-run-able and owner-confirmable before enabling.

**[B] filtered_events old partition pruning**
Define a policy (e.g. keep 12 months of connector events) and implement a
monthly partition DROP sweep.  Must be opt-in via config; default = keep all.

**[C] insight_candidates / engagement pruning**
Add a cron-driven cleanup that removes `delivered`/`filtered` candidates
older than N days (suggested: 90).  Must be dry-run-able.

**[D] secret_probe_log 90-day pruning**
Implement the ≥ 90-day retention described in the core_105 spec.  Requires
an archive path (export or discard decision) before deleting rows.

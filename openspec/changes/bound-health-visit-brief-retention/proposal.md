## Why

`health.visit_briefs.digest` (migration `health_005`, bu-jv1moa.15) keeps every figure of a sent
pre-appointment brief: symptom names with severity and frequency, medication names with adherence
and missed doses, measurement values against the owner's range, changed conditions, and the fact ids
behind each one. Nothing ever removes it, so each brief leaves a permanent second copy of personal
health detail beside the Health facts it was computed from. No reader uses that copy after the send.

This change does two things. It classifies the stored digest, which existing rules already
determine. It also specifies a bounded pruning mechanism that keeps the delivery receipt. The
pruning **window** is a new owner choice about deleting personal health data. Neither doctrine nor
any accepted retention policy sets it, so this change does not choose one. Pruning stays disabled,
and no stored digest changes, until the owner adopts a window (see Owner decision).

## Evidence: digest fields and consumers

Reviewed against `origin/main` 1eda7eaa5. No digest content was read or copied. The field shapes
come from `compose_brief` and `_run_locked` in `src/butlers/jobs/health_visit_brief.py`.

| Stored item | Content | Classification authority | Retention rule today |
| --- | --- | --- | --- |
| `digest.figures[].label`, `.value` (sections: symptoms, adherence, trends, conditions) | Clinical detail derived from condition, symptom, medication and dose facts, plus measurement values | Derived from facts that `butler-health` "Confidential Classification of Clinical Fact Writes" makes `confidential`. A copy is no less sensitive than its sources, so the whole figure list is `confidential`. Trend figures are measurement-derived, but they share one record with clinical figures and get the same class. | None. Kept indefinitely. |
| `digest.figures[].fact_ids` | Ids of `public.facts` rows behind each figure | Same class as the figure. Next to labels, they reveal which clinical facts fed the brief. | None. Kept indefinitely. |
| `digest.period_start`, `period_end`, `default_lookback` | Brief window and whether a prior visit existed | Visit timing. The calendar store already holds it, and it carries no clinical detail. | None. Kept indefinitely. |
| `digest.partial_delivery`, `telegram_effect_uncertain`, `retryable` | Terminal Telegram outcome flags (active change `preserve-long-telegram-replies`) | Delivery metadata. | Must persist: they keep a terminal event from being sent again. |
| `event_id` (primary key) | Calendar event id | Delivery idempotency key. | Must persist: deleting the row reopens a duplicate send. |
| `event_start`, `period_start` columns | Appointment start, brief window start | Visit timing, as above. | Must persist: `event_start` anchors reschedules, and `period_start` is `NOT NULL`. |
| `delivered_at`, `created_at`, `updated_at` | Delivery receipt and row timestamps | Delivery metadata. | Must persist: `delivered_at` is the never-resend receipt. |

Consumers. The only reader and writer of `visit_briefs` is `health_visit_brief._run_locked`. It reads
`delivered_at` and the three terminal flags. It never reads `figures` or the period keys back after
the insert. No dashboard route, API, MCP tool, export or other job reads the table (checked with
`git grep visit_briefs` over `src/`, `roster/`, `frontend/`, `scripts/`, `tests/`, `docs/`). The
brief text is not stored. The `public.attention_ledger` row that `send_owner_brief` writes holds
only outcome and reason codes.

Other copies. Database backups hold the digest until they rotate out. Local dumps rotate after 14
days (`BACKUP_RETAIN_DAYS`, `docs/operations/backup-restore.md`). The off-node restic copy is
disabled by default. When enabled it keeps up to 6 monthly snapshots
(`docs/operations/offsite-backup-copy.md`). Pruning the live row does not reach those copies.

Doctrine checked. `about/heart-and-soul/security.md` § Sensitive Data Categories makes
connector-level controls, including retention periods, the main privacy mechanism. It sets one
period: 30 days for location data. It sets none for health data. `roster/health/MANIFESTO.md` asks
Health to hold the owner's story over years. That covers the authoritative Health facts, which this
change does not touch, and not a derived duplicate. bu-2jtfw.3 set the Health privacy class on
facts, not a retention duration. **No duration is adopted by any existing rule.**

## What changes

- **Classification (in force now).** The stored figure list is `confidential` Health content. Logs,
  job results, issue or PR text, telemetry and any other surface must never carry digest figure
  labels, values or fact ids. The job already behaves this way. The requirement pins it.
- **Bounded pruning (specified, disabled until adopted).** When the job runs, before its sends and
  under its existing run-scoped advisory lock, it replaces the digest of each eligible row with a
  residual receipt. A row is eligible once its appointment start is older than the adopted window.
  The residual keeps `content_pruned_at` and any terminal outcome flags. It drops `figures` and the
  period keys. The row itself, `event_id`, `event_start`, `period_start` and `delivered_at` stay.
  Rows are never deleted.
- **Irreversible boundary.** Pruned figures cannot be restored by a rollback or code revert. Only a
  backup taken before the prune still holds them, until that backup rotates out. The underlying
  Health facts stay authoritative and unchanged. A pruned brief can no longer show which facts
  stood behind each figure it sent.

## Owner decision (hard gate: pruning stays disabled until adopted)

Choose the window after the appointment start at which the digest content is pruned. Until a
choice is recorded, the default is **no deletion and no change**.

- **A. 30 days after the appointment start (recommended).** This is the same conservative horizon
  doctrine already uses for location data. It covers follow-up questions in the weeks after a visit.
  It bounds the duplicate to about a month beyond the brief's purpose, and Health facts still hold
  the full history.
- **B. 7 days after the appointment start.** Smallest useful window. It still covers the visit
  itself and a reschedule of a few days. Less time to trace a figure if the owner questions it
  later.
- **C. At terminal settlement (delivered or terminal failure).** No retained duplicate at all. It
  loses the traceability the brief requirement records ("every figure traceable") as soon as the
  brief is sent.
- **D. Keep indefinitely, explicitly accepted.** No pruning. The duplicate stays, and the mechanism
  is not implemented. This records today's behaviour as a decision instead of an oversight.

Recommendation: **A**. Whichever option is chosen, residual receipts are kept, and the pruning job
works through every past row on its first enabled run. Backup copies age out on their own schedule.

## Implementation packet (held until the owner adopts A, B or C)

- **Seam.** `src/butlers/jobs/health_visit_brief.py`. Add a prune phase to `_run_locked` after the
  terminal-latch reconciliation and before upcoming-window selection, so it runs inside the existing
  `_RUN_LOCK_KEY` advisory lock and cannot overlap a send. Add a module constant for the adopted
  window. `None` means disabled.
- **Statement.** A bounded loop of
  `UPDATE visit_briefs SET digest = jsonb_strip_nulls(jsonb_build_object('content_pruned_at', now(), 'partial_delivery', digest->'partial_delivery', 'telegram_effect_uncertain', digest->'telegram_effect_uncertain', 'retryable', digest->'retryable')), updated_at = now() WHERE event_id IN (SELECT event_id FROM visit_briefs WHERE event_start < $now - $window AND NOT digest ? 'content_pruned_at' ORDER BY event_start LIMIT $batch FOR UPDATE SKIP LOCKED)`.
  Exclude event ids still held in `_PARTIAL_BRIEFS`. Cap the batches per run. The `content_pruned_at`
  guard makes a repeat run a no-op.
- **Reschedule.** A delivered row keeps `delivered_at`, so a later re-anchor still sends nothing. An
  undelivered row whose appointment moved back into the lead window is recomposed. The existing
  `ON CONFLICT ... DO UPDATE` writes a fresh digest, which is the correct new brief.
- **Observability.** The job result gains `pruned` (row count) and, on failure, `prune_failed` with
  the exception class name only. A WARNING log carries the same reason code. A prune failure never
  blocks that run's sends, and the next daily run retries.
- **Docs.** Update `roster/health/AGENTS.md` (Pre-Appointment Visit Brief) and the `health_005`
  docstring with the adopted window and the irreversible boundary.
- **Migration.** None needed. The residual fits the existing `digest` column.
- **Tests (real Postgres).** Extend `roster/health/tests/test_jobs.py::test_visit_brief` with cases:
  an expired delivered row is pruned to a receipt and the next run sends nothing; an expired
  terminal row keeps its flags and stays excluded; a repeat run changes nothing; two overlapping
  runs (prune plus send) still deliver once; and a disabled window leaves the digest byte-identical.
  Planned implementation delta: Tests: +0 ~1 -0 (new parametrized cases on the existing gate).

## Non-goals

- No change to Health facts, the brief's content, its delivery path or its idempotency.
- No migration, no data change, no deployment and no schedule change in this change.
- No retention rule for database backups, which keep their own documented rotation.

Tests: +0 ~0 -0 for this specification change.

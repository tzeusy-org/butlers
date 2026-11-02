## 1. Implementation
- [x] 1.1 `butlers.core.temporal.document_deadlines`: validated plan, presets, inert issuer, upsert keyed on `(document_id, kind)`.
- [x] 1.2 Scheduler deadline pass dispatches job-mode deadlines to their named job with threshold context.
- [x] 1.3 `paperwork_deadline_notify` fixed-template owner notice, registered for every butler.
- [x] 1.4 `paperwork_deadline_set` core temporal tool, D4 inventory declaration, `scheduled_tasks` record writer, dark-build hiding.

## 2. Verification
- [x] 2.1 Plan test: template pinning, issuer sanitizing, late-threshold collapse and typed refusals.
- [x] 2.2 Real-Postgres test: create, unchanged, supersede, job-mode firing through `tick()` and the name-conflict refusal.
- [x] 2.3 Notice job test through the registry and the owner-notify boundary, including suppressed (deferred, no ledger row) and failed (raised) notices.
- [x] 2.5 Real-Postgres: an undelivered notice leaves `fired_thresholds` unchanged; supersede keeps an owner's disable.
- [x] 2.4 Inventory contract test: cataloged with D4 metadata, not listed to current sessions.

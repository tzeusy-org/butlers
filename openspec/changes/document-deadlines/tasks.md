## 1. Implementation
- [x] 1.1 `butlers.core.temporal.document_deadlines`: validated plan, presets, inert issuer, upsert keyed on `(document_id, kind)`.
- [x] 1.2 Scheduler deadline pass dispatches job-mode deadlines to their named job with threshold context.
- [x] 1.3 `paperwork_deadline_notify` fixed-template owner notice, registered for every butler.
- [x] 1.4 `paperwork_deadline_set` core temporal tool, D4 inventory declaration, `scheduled_tasks` record writer, dark-build hiding.
- [x] 1.5 S2: supersession across same-issuer, same-kind documents (later date within 400 days retires, never deletes; owner disable carried) and per-issuer/per-butler caps under one advisory lock.
- [x] 1.6 Failed notices back off per threshold (5 min doubling to 1 h) from their own `failed` ledger rows under a hashed per-butler, per-threshold `dedup_key`, so an outage does not write one row per tick.

## 2. Verification
- [x] 2.1 Plan test: template pinning, issuer sanitizing, late-threshold collapse and typed refusals.
- [x] 2.2 Real-Postgres test: create, unchanged, supersede, job-mode firing through `tick()` and the name-conflict refusal.
- [x] 2.3 Notice job test through the registry and the owner-notify boundary, including suppressed (deferred, no ledger row) and failed (raised) notices.
- [x] 2.5 Real-Postgres: an undelivered notice leaves `fired_thresholds` unchanged; supersede keeps an owner's disable and re-enables an expired deadline.
- [x] 2.6 Real-Postgres: cross-document supersession kept as disabled rows that never fire, owner-disable carry-over, earlier and far-later date coexistence, issuer-change capping, issuer-less isolation and both cap refusals.
- [x] 2.7 Notice backoff: the job defers within the backoff window and keys attempts per threshold (unit); through `tick()` an outage writes one failed row across ticks and the threshold fires on recovery (real Postgres).
- [x] 2.4 Inventory contract test: cataloged with D4 metadata, not listed to current sessions.
- [x] 2.8 Issuer name set: adversarial phone, URL, email, markup, non-ASCII digit and length cases reduce to a short name at write and render (unit).

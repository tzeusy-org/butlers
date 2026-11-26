## 1. Spec

- [ ] 1.1 This change reviewed before runtime code.

## 2. Slices

- [ ] 2.1 S1 `public.calendar_rsvp_policies` and its versions (one migration), the `public` grants manifest and inventories, and the owner-only dashboard route; a test that the agent role has no write grant and no MCP tool writes a policy.
- [ ] 2.2 S2 the pure evaluator in `core/temporal`, one closed predicate set, with a test that no title or description reaches it.
- [ ] 2.3 S3 the `calendar_rsvp_docket` job: per-event cards through `prepare_and_park`, idempotent per policy version and event etag.
- [ ] 2.4 S5 the agreement ledger (one migration): approved as prepared, edited, rejected, per policy, never read by autonomy.
- [ ] 2.5 S6 the dashboard docket batch: one submit approves each card through the existing path, refusing and naming any card that changed.
- [ ] 2.6 Not built: the decline note template, and any decision-set delivery (frozen until the goal-runtime cutover).

## 1. Spec

- [ ] 1.1 This change reviewed before runtime code.

## 2. Slices

- [x] 2.1 S1 `public.calendar_rsvp_policies` and its versions (one migration), the `public` grants manifest and inventories, and the owner-only dashboard route; writes and the dashboard's reads go through SECURITY DEFINER functions (core_350 pattern, EXECUTE to the dashboard role only); no `butler_*_rw` role writes (catalog test); SELECT only for the job's butler; a contract test that no MCP tool or prompt reads policies.
- [x] 2.2 S2 the pure evaluator in `core/temporal`, one closed predicate set, with a test that no title or description reaches it.
- [x] 2.3 S3 the `calendar_rsvp_docket` job: per-event cards through `prepare_and_park`, request id digests event, occurrence, etag, policy id and version, verdict and `send_updates`; rejects superseded cards with closed reasons; refuses to run outside the one butler owning the primary calendar fence; counts unprepared invitations on an outage; prepares nothing when policies disagree.
- [x] 2.4 S5 the agreement ledger (one migration): table `public.calendar_rsvp_ledger`: `approved_as_prepared`, `owner_rejected`, `answered_same`, `answered_elsewhere`, per policy; other system rejections excluded; a contract test that only the dashboard read model and the writer reference the table.
- [x] 2.5 S6 the dashboard docket batch: one submit re-evaluates each card and approves it through the existing path, refusing and naming any that changed or no longer match.
- [ ] 2.7 S7 the dashboard docket UI on top of S6's `GET`/`POST /api/calendar/rsvp-docket`: it delivers REQ-005's dashboard surface (every exact call visible, tick-and-submit, changed cards named). S6 is backend only.
- [ ] 2.6 Not built: the decline note template, policy preview (later work), and any decision-set delivery (frozen until the goal-runtime cutover).

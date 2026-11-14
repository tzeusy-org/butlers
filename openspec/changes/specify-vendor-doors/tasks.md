## 1. Ownership verification (before code)

- [ ] 1.1 Read `src/butlers/db_authority/grants.py` and the role inventory; pick the existing
  dashboard-writer, finance and switchboard roles for the confirm/propose functions and amend design D1.
- [ ] 1.2 Verify `[Unknown]` that Messenger can read `public` (receiver resolution) under its role.
- [ ] 1.3 Confirm finance_017 `owner` is only ever written from an owner-attested routing context
  (`runtime_signal_source`), supporting design D5.

## 2. Registry migration (core)

- [ ] 2.1 Failing real-PG tests first: roles, direct writes revoked, CHECKs (kind, status, host),
  confirm/revoke CAS, propose cannot touch a confirmed row.
- [ ] 2.1b Real-PG test: `PUBLIC` and every role outside each function's grant are refused
  `EXECUTE` on confirm, revoke, reject and propose; the per-bucket proposal caps (a flood of unverified proposals leaves verified, extracted and backfill rows storable) and 30-day expiry hold;
  reject is retained and not re-proposed.
- [ ] 2.2 Core revision creating `public.vendor_doors`, `vendor_doors_current` and the four
  functions, with `CHECK ((status = 'proposed') = (expires_at IS NOT NULL))`, each with a standalone `REVOKE ALL ... FROM PUBLIC` then a `GRANT EXECUTE` to one role set;
  add rows for the table, view and four functions to `src/butlers/db_authority/grants.py`, and to
  the schema-reach and grants-manifest inventories.

## 3. API

- [ ] 3.1 `src/butlers/api/routers/vendor_doors.py` behind `require_dashboard_owner_control`: list,
  confirm, revoke; refusal matrix (forged, non-owner, connector, exposed, email-originated); confirm view shows host, template, origin and verdict; reject route.
- [ ] 3.2 Concurrent confirm/revoke test; idempotent retry test.

## 4. Typed reference and resolver

- [ ] 4.1 `DoorReferenceV1` in the Switchboard contract; sender and Messenger receiver resolvers;
  URL and slot rule matrix; exposure refusal; read-failure and cancellation tests.

## 5. Migration and renewal consumer

- [ ] 5.1 Finance revision backfill (owner to confirmed, rest to proposed), idempotent.
- [ ] 5.2 Extend `test_insight_scan_renewal_with_known_door_warns_with_days_remaining` and
  `test_insight_scan_owner_set_cancelled_suppresses_the_renewal_warning`; add extracted-cancelled keeps warning.
- [ ] 5.3 `track_subscription` extracted path writes proposals only.

## 6. Docs

- [ ] 6.1 Apply the bu-kr3x6j sentence to `about/heart-and-soul/security.md` through the reviewed PR.
- [ ] 6.2 Update core-notify, butler-messenger and finance-alerts specs and docs for the adopted behavior.
- [ ] 6.3 Archive this change into `openspec/specs/vendor-doors/`.

## 7. Numbering and dark flag

- [ ] 7.1 Take the next free core and finance revision numbers at implementation (core_334/335 in flight).
- [ ] 7.2 Add `VENDOR_DOORS_LIVE = False`; no activation.

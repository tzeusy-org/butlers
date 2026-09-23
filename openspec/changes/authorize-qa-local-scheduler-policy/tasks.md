## 0. Authority and adoption gates

- [ ] 0.1 Independently review this exact proposed RFC/OpenSpec text against
  Rule 3, RFCs 0001/0003/0006/0010, `sw_035`, QA's runtime-role and audit
  pools, and the active liveness delta.
- [ ] 0.2 Obtain the owner's explicit choice on the paired proposed Rule 3
  doctrine text, narrow QA-only RFC exception, and the typed unknown-policy clarification to
  `REQ-staffer-qa-006`. Unanswered or declined means no projection grant, Q1 scheduler
  shortcut, implementation, archive, or live activation.
- [ ] 0.3 Recheck the current Switchboard migration head and open file
  ownership before allocating a revision; keep `bu-ch4p1v` and Q1 serialized
  until this authority text is adopted.

## 1. Future Switchboard producer: bu-ch4p1v

- [ ] 1.1 Implement a no-argument, fixed-QA, Switchboard-owned read
  projection with exact effective-role verification, fixed search path,
  enum/pair validation, and typed missing/malformed/denied results.
- [ ] 1.2 Revoke PUBLIC function EXECUTE, grant only the QA runtime role, and
  retain Switchboard table RLS without QA table SELECT or broad schema
  access. Verify migration and `init-db.sql` replay on real PostgreSQL.
- [ ] 1.3 Verify concurrent owner-policy changes, direct other-role and
  migration-login denial, role reset on success/error/cancellation, and
  non-destructive rollback. Keep Switchboard's own sweep on its existing role.

## 2. Future QA consumer: Q1 bu-fvw4ap.1

- [ ] 2.1 Replace legacy route-eligibility scheduling admission with a fresh
  exact projection read through QA's own role-scoped pool; never use the
  audit pool, positive cache, `allow_stale` shortcut, or a caller name.
- [ ] 2.2 Continue QA cron/deadline patrol through proven TTL staleness;
  suppress explicit holds and unresolved/typed-unavailable results with
  distinct diagnostics. Recheck before a later admission after a mid-tick
  owner change, and preserve restart idempotence.
- [ ] 2.3 Update QA manifesto and RFC 0001 implementation notes only after
  the producer contract lands; no provider, credential, deployment, or live
  schedule operation is implied.

## 3. Future verification and completion

- [ ] 3.1 Extend owning migrated-PostgreSQL, bootstrap/RLS, scheduler, and QA
  pipeline tests named in design.md. Preserve each unique race, role, owner
  stop, rollback, and no-double-dispatch invariant; condense rather than
  silently raise the at-limit integration budget.
- [ ] 3.2 Report each implementation PR's measured `Tests: +a ~b -c`,
  run test-plan, focused real-Postgres nodes and collection, Ruff, strict
  OpenSpec, overwrite/countable/guards/budget, and terminal exact-head hosted
  CI. Archive only after the adopted contract and implementation reconcile.

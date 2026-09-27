## 1. Owner gates

- [ ] 1.1 Confirm the Gmail API send and exact-ID confirmation operations, OAuth scope, and confirmation deadline for Messenger's own account.
- [ ] 1.2 Amend RFC 0024 to the trimmed design (read-only view instead of `SECURITY DEFINER` functions; no broker, ingress epochs, or protected-job registry).

## 2. Storage and grants

- [ ] 2.1 Add `roster/messenger/migrations/005_email_correspondence.py`: `messenger.email_correspondence` with allowlisted columns only (no JSONB, free text, or content) and state/retention constraints.
- [ ] 2.2 In the same migration, create `messenger.v_confirmed_email_outbound` (normalized peer, capped confirmed count, last confirmed time, 180-day window) and grant `USAGE` on the schema plus `SELECT` on the view only to `butler_relationship_rw`.
- [ ] 2.3 Downgrade drops the view first and refuses to drop a non-empty table, mirroring `003_retire_unwired_delivery_tracking.py`.
- [ ] 2.4 Add migrated-PostgreSQL tests: Relationship can select the view but not the table; no other role has access; constraints hold; downgrade is empty-only.

## 3. Messenger send path and confirmation

- [ ] 3.1 Refactor `src/butlers/modules/email.py` so Messenger commits the intent row before egress, blocks egress if the commit fails, and returns categorical, content-free outcomes.
- [ ] 3.2 Keep SMTP as `accepted -> unknown`; add a disabled-by-default Gmail API native send that records the returned message ID and confirms it with one exact-ID metadata lookup (`SENT` label).
- [ ] 3.3 Register a deterministic, zero-LLM Messenger maintenance job (confirmation retry, deadline expiry to `unknown`, 180-day purge) in `src/butlers/scheduled_jobs.py` and `roster/messenger/butler.toml`; note the exception in `roster/messenger/MANIFESTO.md`.
- [ ] 3.4 Add tests: commit-before-send, intent failure blocks egress, duplicate key, SMTP never confirms, crash after dispatch leaves `unknown`, no blind retry, exact-ID confirmation, no Sent enumeration, deadline expiry, and purge.

## 4. Relationship consumption

- [ ] 4.1 Add a deterministic `email_correspondence_enrichment` job (daily `dispatch_mode="job"`) that reads the view for at most 100 active literal `has-email` addresses and combines it with the inbound recurrence from `src/butlers/modules/contacts/email_identity_matching.py`.
- [ ] 4.2 Keep `run_email_identity_enrichment` in `roster/relationship/jobs/relationship_jobs.py` inbound-only.
- [ ] 4.3 Add tests: both legs yield `true`; any single leg, a missing view, or stale data yields `null`; no path yields `false`; no raw ledger values are written to facts or logs.

## 5. Verification and rollout

- [ ] 5.1 Run focused tests, `make check`, and `openspec validate true-bidirectional-email-correspondence --strict`.
- [ ] 5.2 Enable native confirmation for one account behind its flag; monitor only categorical counters.
- [ ] 5.3 Roll back by disabling the flag and revoking the view grant; retained rows expire on schedule.

## 1. Freshness and failure (S1)

- [ ] 1.1 `generated_at` and `cadence_seconds` on the overlay envelope; degraded and stale reads reported
- [ ] 1.2 Router and read model return a per-butler status, not `has_domain_context=false`, for a failed read

## 2. Claim schema and reconcile (S2)

- [ ] 2.1 Claim schema v2 on the existing key; `CalendarModule.reconcile_domain_claims` in process
- [ ] 2.2 `claim:` rows upserted, updated in place on a version change, retracted when absent or settled
- [ ] 2.3 Real-Postgres tests, with a mutation check of the retraction pass

## 3. Producers (S3 to S5)

- [ ] 3.1 Finance: bills, subscriptions, cancel_by; drop the reminder clause from the finance instructions
- [ ] 3.2 Travel: legs, accommodations, reservations, check-in, document expiry; drop the travel clauses
- [ ] 3.3 Health (absorbs bu-rx0tyg), Relationship (S5); Education daily review-due aggregate with its overlay-view migration (S5b)

## 4. Workspace (S6)

- [ ] 4.1 Dedup on `claim_key`; the claim door in the event drawer
- [ ] 4.2 Owner snooze and move survive a re-emit of the same `record_version`

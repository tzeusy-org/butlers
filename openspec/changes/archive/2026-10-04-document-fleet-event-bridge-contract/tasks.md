## 1. Contract bookkeeping

- [x] 1.1 Trace RFC 0022 rules to observed implementation and existing test evidence.
- [x] 1.2 Author core-fleet-events with IDs, sources, mandatory scope, and WHEN/THEN scenarios.
- [x] 1.3 Add truthful citation comments to existing tests without changing test logic.
- [x] 1.4 Condense RFC 0022 to status, decision, and trade-offs; update its index row.

## 2. Verification and delivery

- [x] 2.1 Validate the scoped capability with strict trace checking and OpenSpec.
- [x] 2.2 Run focused citation tests and repository hygiene guards; verify links and unchanged test logic.
- [x] 2.3 Sync/archive the change and verify its requirements landed in the main spec.

Focused results: 85 Python/roster tests and 32 frontend tests passed. The PostgreSQL file ran one non-DB test successfully; four DB cases failed at setup because this host denies Docker socket access. Hosted CI must supply those results before merge. Citation presence does not erase the clause-level coverage and source discrepancies recorded in design.md.

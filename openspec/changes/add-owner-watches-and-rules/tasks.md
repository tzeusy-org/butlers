## 1. Contract adoption (owner-merged)

- [ ] 1.1 Independent conformance review that the RFC 0019 and RFC 0038 S4 text stays inside
  bu-nt2eda's recorded boundary (Q1 = A, Q2 = A without optional git promotion, Q3 limits).
- [ ] 1.2 Owner reviews the exact text and merges the PR. Do not auto-merge or queue.

## 2. Follow-up slices (blocked on 1.2, tracked on bu-4c7nvp.1.5)

- [x] 2.1 Extend the closed AST and evaluator matrix, Python and `perception_private.valid_predicate`
  together, with the per-schema migration rules.
- [x] 2.2a Owner watch lifecycle on the evaluator (`butlers.watches`): exact cards, cap, expiry,
  renewal, pause, delete, in-memory store. Dark; no table, tool or delivery.
- [ ] 2.2b Postgres watch store (table, migration, grants manifest, two-schema test) and the owner
  approval-card and `never` reply surfaces that call the service.
- [ ] 2.3 Finance `alert_config` migration.
- [ ] 2.4 Guards, wake_when and automatic tighten-only modes.
- [ ] 2.5 Correlation rule and graduation card.

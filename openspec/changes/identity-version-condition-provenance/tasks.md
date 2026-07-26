## 1. Durable provenance

- [x] 1.1 Carry producer-declared identity-payload versions through infrastructure observations and preserve them as condition evidence.
- [x] 1.2 On a complete snapshot, record explicit v1-to-v2 predecessor/successor provenance only for a declared higher-version successor, without changing fingerprint identity or lifecycle states.
- [x] 1.3 Preserve ordinary snapshot recovery, incomplete-snapshot non-resolution, and repeated successor idempotency.

## 2. Operator truth and verification

- [x] 2.1 Render identity-version supersession distinctly from recovered condition history in the existing Standing Conditions panel.
- [x] 2.2 Add real-Postgres lifecycle regressions and focused frontend coverage for supersession, ordinary recovery, incomplete snapshots, and repeated successors.
- [x] 2.3 Run strict OpenSpec validation and right-sized backend/frontend quality gates.

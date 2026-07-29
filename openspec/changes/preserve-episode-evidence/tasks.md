## 1. Persistent provenance contract

- [x] 1.1 Add migrated-PostgreSQL regressions for deleting an episode with
  sourced facts, rules, and generic links; prove content-free tombstones,
  retained source identifiers, and atomic failure behavior.
- [x] 1.2 Add the scoped memory migration for content-free tombstones and
  deletion-triggered provenance preservation without modifying historical rows
  or cleanup scheduling.

## 2. Truthful readers

- [x] 2.1 Add failing storage/API regressions that distinguish available,
  expired, and unresolved episode references for facts, rules, and both ends
  of generic memory links.
- [x] 2.2 Project typed source-state fields through memory storage, dashboard
  API models, and client types without exposing raw episode content or internal
  deletion data.

## 3. Safe dashboard provenance

- [x] 3.1 Add failing React regressions proving fact, rule, and register
  provenance render expired/unresolved sources without episode navigation.
- [x] 3.2 Implement the visible non-clickable source state while preserving a
  link only for an available source episode.

## 4. Verification and handoff

- [x] 4.1 Run strict OpenSpec validation plus focused PostgreSQL/API/frontend
  regressions and the relevant lint/type gates.
- [x] 4.2 Review the final diff for raw-content retention, dangling doors, and
  any historical-cleanup behavior; record only verified evidence in handoff.

## 1. Contract and safe source boundary

- [x] 1.1 Implement bounded shared read-only snapshot assessment and safe Bead projection.
- [x] 1.2 Add typed API models and the additive snapshot-backed detail endpoint with 404/503 envelopes.
- [x] 1.3 Register the API route without adding a live tracker, database, credential, or mutation path.

## 2. Dashboard surface

- [x] 2.1 Add typed client, query hook, shell capability, and `/beads/:id` route.
- [x] 2.2 Implement accessible read-only detail rendering with visible freshness and honest unavailable/not-found states.
- [x] 2.3 Wire Decisions and escalation blockers exclusively to same-origin detail routes; keep `external_ref` inert.

## 3. Verification and handoff

- [x] 3.1 Add red-first privacy, bounded-reader, API availability, and 404/503 sentinel coverage.
- [x] 3.2 Add red-first UI, accessibility, routing, inert-reference, and degraded-state coverage.
- [x] 3.3 Run targeted and final quality gates, reconcile OpenSpec tasks, and publish the isolated branch PR.

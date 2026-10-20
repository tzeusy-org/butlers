## 1. Implementation

- [x] 1.1 `weight` and `body_fat` bundles, normalizer, sample-time identity
- [x] 1.2 `wellness_ingest` mapping and sample-time `valid_at`
- [x] 1.3 `owner_dominance` helper wired into latest, history and `/measurements/latest`

## 2. Verification

- [x] 2.1 Connector, ingest and dominance unit tests
- [ ] 2.2 Real-Postgres owner-dominance test runs in CI (authoring host has no Docker)
- [ ] 2.3 Live API shape of the weight and body-fat data points (list vs reconcile) confirmed

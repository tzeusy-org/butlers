## 1. Implementation

- [x] 1.1 `weight` and `body_fat` bundles, normalizer, sample-time identity
- [x] 1.2 `wellness_ingest` mapping and sample-time `valid_at`
- [x] 1.3 `owner_dominance` helper wired into latest, history and `/measurements/latest`
- [x] 1.4 `dataPoints:reconcile` for both bundles, every `nextPageToken` page consumed, and the
  checkpoint held when a page fetch or required submission fails (bu-u5euwb.10)

## 2. Verification

- [x] 2.1 Connector, ingest and dominance unit tests
- [ ] 2.2 Real-Postgres owner-dominance test runs in CI (authoring host has no Docker)
- [ ] 2.3 Live API shape of the weight and body-fat data points (list vs reconcile) confirmed
  - Weight confirmed on both connected dev accounts (2026-11-02, content-free receipt): reconcile
    HTTP 200 with typed `weight.weightGrams` and `weight.sampleTime.physicalTime`, the
    `sample_time.physical_time` filter selects points, and `pageToken` continuation returns a
    distinct next page.
  - Body fat still open: reconcile and plain list return an empty object on both accounts with no
    filter, so no positive point shape exists to observe. Needs a real body-fat reading on a
    connected account; no synthetic write.

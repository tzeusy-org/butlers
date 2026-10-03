## 1. Slice 1: core scorer and Health

- [x] 1.1 `butlers.core.baselines`: metric declaration, band, score, episode decision, persistence.
- [x] 1.2 Migration `core_258`: `metric_baselines`, `metric_deviation_episodes` (per schema).
- [x] 1.3 Health `baseline_watch` daily job for resting HR, HRV and sleep duration.
- [x] 1.4 Insight broker requires `baseline_evidence` for `baseline-deviation`.
- [x] 1.5 Spec deltas and Health docs.

## 2. Deferred slices

- [ ] 2.1 Slice 2: `metric_baseline_get`, Health baselines API, measurements-tab band.
- [ ] 2.2 Slice 3: Home energy on the scorer.
- [ ] 2.3 Slice 4: Finance anomaly detection and briefing on the scorer.
- [ ] 2.4 Slice 5: chronicler balance lanes and session spend.

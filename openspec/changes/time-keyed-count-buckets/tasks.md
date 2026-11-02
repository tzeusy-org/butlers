## 1. Time-keyed detail buckets (S1)

- [x] 1.1 Stats route returns dense `generate_series` buckets over the roster's window, keyed by bucket start.
- [x] 1.2 `lib/bucket-series.ts` places rows by bucket start; unmatched slots are `null`, never zero.
- [x] 1.3 Connector detail histogram uses keyed series, draws no bar for unreported slots, and labels relatively.

## 2. Deferred

- [ ] 2.1 S2: heartbeat-derived per-bucket listening state on detail and roster; drop the default-0 liveness fields.
- [ ] 2.2 S3: ESLint rule banning positional zero-fill in chart derivations; migrate session/activity strips.

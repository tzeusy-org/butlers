## 1. Sync repair (bu-uhbhzq.1 S1)

- [x] 1.1 Drop `orderBy`; share one filter set between the full and incremental passes.
- [x] 1.2 A missing sync token is degraded health with no "saved" log and no immediate repeat.
- [x] 1.3 Regression tests for token persistence, param parity and honest absence.

## 1. Credential-safe reconciliation primitive

- [x] 1.1 Add failing focused tests for shared-vs-local authority, changed, unchanged, malformed, unavailable, bounded store delay, absent/revoked, write-failure, stale-writer, retry, health ordering, Passport value replacement, dashboard probe fencing/history visibility, and concurrent Codex auth reconciliation.
- [x] 1.2 Implement atomic mode-restricted token-file replacement, explicit shared Codex authority, and coherent per-path serialized reconciliation.
- [x] 1.3 Add conditional shared-store persistence so a runtime rotation is accepted only when its captured operation authority snapshot still matches.

## 2. Codex runtime integration

- [x] 2.1 Add adapter behavior tests proving reconciliation completes before token freshness, speculative prewarm, isolated-HOME setup, and the actual spawn linearization point.
- [x] 2.2 Invoke reconciliation before token freshness/prewarm and isolated-HOME setup, revalidating before every subprocess attempt and finalizing once after retries.
- [x] 2.3 Finalize dashboard auth after its Codex post-login prewarm using the prewarm's captured snapshot.
- [x] 2.4 Let direct dispatchers accept an explicitly supplied credential authority; wire calendar quick-add's known shared/public pool; fence runtime and dashboard Codex health writes to the credential value actually used; persist dashboard health/probe/audit evidence under one credential-row transaction; finalize status-probe rotations without attaching stale health; clear health atomically on Passport/runtime credential replacement; and keep one bounded adapter sync allowance outside the provider execution budget in both Spawner and direct-dispatch guards.

## 3. Contract and verification

- [x] 3.1 Run strict OpenSpec validation and focused CLI-auth/Codex adapter tests at the final head, then mark the completed tasks.
- [x] 3.2 Run format/lint and the right-sized regression suite; obtain independent exact-head review before PR handoff.

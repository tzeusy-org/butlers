## 1. Repair the defective baseline requirements

- [x] 1.1 Add RFC-2119 prose to `connector-gmail` / `ingest.v1 Field Mapping`, carrying its `Gmail field mapping` scenario unchanged.
- [x] 1.2 Add RFC-2119 prose to `connector-gmail` / `Aggregated Health Status`, carrying its `Health model (multi-account)` scenario unchanged.
- [x] 1.3 Add RFC-2119 prose to `connector-gmail` / `Environment Variables`, carrying its `Required variables`, `Process-level default variables (optional)`, and `Backfill variables` scenarios unchanged.
- [x] 1.4 Add an RFC-2119 keyword and a `No inbox classification tool is registered` scenario to `module-email` / `Classification Pipeline Integration (Removed)`.

## 2. Verification

- [x] 2.1 `openspec validate repair-email-spec-requirement-prose --strict` passes.
- [x] 2.2 In a scratch copy of `openspec/`, archive this change and confirm the rebuilt `connector-gmail` and `module-email` specs validate with no `✗` errors.
- [x] 2.3 In that same scratch tree, archive `true-bidirectional-email-correspondence` and confirm it now succeeds.
- [x] 2.4 Confirm the rebuilt baselines are byte-identical to the originals apart from the added prose and the one added scenario — no scenario dropped, no wording changed.

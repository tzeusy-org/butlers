# RFC 0027 Review - Rounds 2 and 3

**Reviewers:** fresh RFC and capability-spec agents
**Date:** 2026-08-30
**Verdict:** ACCEPT after final convergence pass

## Round 2 Findings

The capability-spec reviewer accepted the revised contract. The RFC reviewer
requested one final consistency pass on four points:

1. Receipt numeric units mixed distinct definitions, summary entries, operation
   occurrences, and serialized bytes.
2. The compatibility key differed across RFC, design, tasks, and spec, and did
   not consistently separate key fields from conformance evidence.
3. Cursor/plan digests did not consistently include catalog generation,
   enabled-module snapshot, exposure policy, and resolved compatibility key.
4. Incomplete effect evidence had no representation in the closed receipt
   vocabulary.

## Author Response

1. Defined units field by field: definition counts, summary-entry counts,
   operation-occurrence counts, and canonical compact sorted-key UTF-8 bytes.
2. Defined one `CompatibilityKey`: runtime type, executable artifact digest/
   identity/exact version, adapter-profile revision, normalized configuration
   dialect/digest, transport/protocol version, and exact provider/model IDs.
   Manifest, fixture, result digests, and verification time are evidence fields,
   not key fields. Configuration normalization replaces ephemeral/sensitive
   values with typed sentinels before hashing.
3. Standardized the attempt plan digest everywhere as catalog-generation
   digest, enabled-module-snapshot digest, exposure policy, and resolved
   compatibility-key digest; mismatch in any component invalidates pagination.
4. Kept the original native failure category, set
   `effect_evidence_complete=false`, and added outcome
   `failed_effect_unknown`.

## Final Review

The third-round fresh reviewer re-read the stable snapshot and returned
**ACCEPT** with no remaining contradiction across the four convergence points.
The independent capability-spec review also returned **ACCEPT**, confirming:

- replay-safe candidate/presentation attempt semantics,
- closed receipt fields, enums, retention, and non-overwriting storage,
- pagination/module-snapshot/transport parity,
- fail-closed non-MCP effect evidence,
- reproducible native admission and no-behavior-regression gates,
- honest Management-tab and error-state behavior,
- dedicated skill-projection verification,
- complete proposal-to-delta coverage,
- preservation of every carried baseline scenario heading.

## Final Mechanical Evidence

- `openspec validate add-runtime-tool-surface-discovery --strict` - pass.
- `make check-spec-overwrites` - pass, no unfrozen baseline losses from this change.
- `git diff --check` - pass.
- `make check-em-dashes` - pass.
- `make check-countable-tasks` - pass.
- `make check-duplicate-names` - pass.

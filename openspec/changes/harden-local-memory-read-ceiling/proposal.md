## Why

The server-held memory sensitivity ceiling already protects catalog discovery,
but local retrieval paths can expose a more-sensitive row after its identifier
is known. Health condition, symptom, medication, and dose writes also relied on
the memory-store default rather than explicitly classifying clinical facts.

This repair closes the same-butler read bypass, makes the Health classification
contract explicit, and safely repairs historical under-classification without
introducing a cross-schema access path.

## What Changes

- Apply the runtime-config-held sensitivity ceiling to every local memory read,
  including direct `memory_get` by UUID; an above-ceiling row is not returned
  and its reference metadata is not changed.
- Keep the Profile Facts `withheld` receipt inside both its section allocation
  and the overall context budget.
- Classify Health condition, symptom, medication, and dose facts as
  `confidential` at write time, then reclassify and de-catalog the affected
  historical rows through a guarded, idempotent core migration.
- Document the behavior and regression obligations in the matching capability
  specifications.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-memory`: Define a single server-held sensitivity ceiling for all
  local memory retrieval paths and bounded withheld receipts.
- `butler-health`: Require explicit confidential classification for clinical
  condition, symptom, medication, and dose facts, including the historical
  repair boundary.

## Impact

- `src/butlers/modules/memory/` read policy, direct retrieval, and context
  assembly.
- Health condition and medication tools plus a core migration for existing
  rows and catalog entries.
- Memory and migration regression tests, CI shard manifests, and repository
  engineering notes.

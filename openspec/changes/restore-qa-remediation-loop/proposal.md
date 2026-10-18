## Why

The accepted 2026-10-14 QA review found 100 investigation attempts and zero PRs in a month, with all 95 launched sessions owned by Travel and 38 no-op cases presenting an inherited diagnosis. QA must reliably absorb incident investigation and verification work for the owner, with evidence that distinguishes a proposed fix from an actual recovery.

## What Changes

- Restore QA-scoped runtime/model/budget ownership and launch-time session lineage for initial and follow-up work.
- **BREAKING:** replace inherited unbound notes and no-op-as-unfixable semantics with invocation-bound evidence and explicit diagnostic outcomes; preserve historical records with an unverified/contaminated annotation.
- Persist incident membership, changed-evidence admission and leased deferred work, with finite shared reasoning budgets and no loss at capacity or restart.
- Add source coverage/watermarks and bounded catch-up to deterministic discovery; reuse the separately owned log-source replacement instead of implementing a second scanner.
- Require doctrine/spec grounding and proportional verification for candidate patches; consume the existing restricted publisher for validated-artifact resume and exact feedback-driven follow-ups.
- Distinguish merged, deployed, and observed-recovered cases in the existing QA dossier and report an evidence-qualified outcome/cost funnel.

## Capabilities

### New Capabilities

None. These are corrective and extended contracts for the existing QA service. Exact proposed RFC0015 and manifesto successor clauses are in contract-amendments.md and travel under the same owner adoption gate.

### Modified Capabilities

- `qa-investigation-dispatch`: runtime ownership, prompt governance, evidence/outcome lifecycle, follow-up admission and full proposal diff evidence.
- `qa-triage`: durable incident correlation, evidence-aware retries and leased backlog survival.
- `staffer-qa`: deterministic bounded discovery with honest source coverage and recovery after gaps.
- `qa-log-scanner`: cursor-aware temporal coverage and honest bounded partial results.
- `qa-dashboard`: trustworthy investigation outcome, provenance, publication/deployment/recovery and cohort/cost projections.

## Impact

Owned surfaces: core spawn hooks and the narrow launch/session-binding seam; QA module, sources, tracking, notes/prompts/dispatch, public QA storage, API and existing dossier components. New storage uses QA runtime authority and sanctioned dashboard projections; this is not a cross-schema-read exception. Required regression evidence includes multi-daemon startup order, stale artifacts, real-PostgreSQL competing claims/crash recovery, partial source coverage, exact PR feedback admission, and a synthetic signal-to-recovery journey.

The existing `confine-qa-publication-authority` delta remains authoritative for sandbox, checkout/export, publisher operations, ambiguity and cleanup. This change does not restate or weaken its requirements. `bu-mc01y5.3/.4/.2`, `bu-dss7he.1/.13`, `bu-2jtfw.5`, and `bu-s11n0s.6` retain ownership; concrete sequencing is in the direction dossier and Beads, not implied here. The credential correction to `tzeusy-orca` is owner-reported; the author-reported resolver/grant recheck has no retained receipt and remains unverified. Retained repository configuration names `tzeusy-org/butlers`. Neither report nor target configuration permits a canary or activation.

## Authority and exclusions

The owner's instruction approves turning the accepted audit into concrete changes and gated beads. These artifacts are a proposed contract for exact release review. Every implementation child remains blocked by an owner-held gate until the final manifest is adopted and released; writing this proposal does not authorize implementation or deployment. Backward compatibility is waived for obsolete APIs and representations, but audit evidence, privacy, idempotence and safe rollback are retained.

No new generalist-orchestrator runtime, grandchildren, broader QA credentials, automatic merging, external-service integration, global log rewrite, provider-price assumptions, or new dashboard page is included. The existing prompt/model identity, artifact correctness and durable state are repaired before increasing autonomous volume. Boundary-changing remedies discovered by investigators return to a proposal rather than silently extending scope.

# RFC 0027 Review — Round 1

**Reviewers:** independent spec-conflict, architecture-risk, RFC fresh-reader,
and capability-spec fresh-reader agents
**Date:** 2026-08-30
**Draft baseline:** `ba1c55c31056af5eaa362a254193bbf43741e86a`
**Verdict:** REVISE

## Findings

1. **The initial caller-scoped design silently created a fleet-wide MCP
   authentication program and contradicted current doctrine.** Runtime query
   markers are caller-controlled, FastMCP is currently unauthenticated, and
   every infrastructure client would need a coordinated credential migration.
2. **The initial discovery metadata combined incompatible concerns.** One enum
   could not express a definition that was deferred for native search but
   excluded from nested code execution.
3. **Code Mode lacked an enforceable isolation boundary.** Current CLI
   processes can carry shell, filesystem, configuration, or network authority
   beyond a nested catalog; prompt/metadata restrictions were insufficient.
4. **Catalog finalization could have retained pre-approval callables.** Approval
   setup replaces/wraps handlers after tool registration, so any earlier
   callable cache would bypass the final execution chain.
5. **Several MODIFIED deltas dropped baseline clauses.** OpenSpec strict passed,
   but the body-aware overwrite guard identified archive-time losses in core
   daemon and runtime-config requirements.
6. **Native-to-eager replay lacked a complete effect predicate.** MCP capture
   alone does not cover shell, file, app, browser, or parser-unknown effect
   channels.
7. **Candidate attempts, presentation retries, and durable receipt indexes were
   ambiguous.** The draft did not reconcile presentation fallback with the
   existing one-selection-per-catalog-entry rule or process-log upserts.
8. **Pagination and module-state snapshots were not part of the durable
   behavior contract.** A catalog-only cursor digest could mix pages after a
   module-state change.
9. **Compatibility and admission evidence was underspecified.** The key omitted
   executable/profile/configuration digests; byte measurement, expected
   outcomes, retries, cache conditions, and behavioral regression gates were
   not reproducible.
10. **UI and rollout language could overstate state.** The draft mixed Config
    and Management tab names, lacked GET/PATCH failure behavior, and made a live
    canary sound like an implementation completion requirement.

## Author Response

1. Narrowed v1 to presentation/discovery only. Fleet-wide caller
   authentication and direct-call denial are explicit separate non-goals;
   hidden definitions are not described as a security boundary.
2. Split metadata into orthogonal `llm_presentable` and
   `load_posture = eager | deferred` fields.
3. Reserved Code Mode for a separate post-v1 isolation design. V1 modes are
   only `none`, `eager_filtered`, and `native_deferred` direct MCP calls.
4. Required catalog finalization after approval wrapping, descriptor-only
   storage, and canonical lookup through the final FastMCP registry.
5. Rebuilt every MODIFIED block from the complete current baseline. The body-
   aware overwrite guard now reports zero unfrozen losses for this change.
6. Added closed replay triggers plus merged MCP/non-MCP effect evidence;
   shell/file/app/browser/unknown/parser-ambiguous evidence blocks replay.
7. Defined bounded candidate attempts with one or two ordered presentation
   subattempts and nested TTL-governed receipts.
8. Added filtering-before-pagination, plan digests containing catalog and
   enabled-module snapshots, invalid-cursor behavior, and transport parity.
9. Defined the exact compatibility key, closed receipt schema/enums/count units,
   checked-in conformance manifest, canonical UTF-8 byte formula, and no-
   behavioral-regression admission rule.
10. Clarified the existing Management-tab card, added honest GET/PATCH failure
    scenarios, and changed the deliverable to canary-ready. Live policy changes
    remain separately operator-authorized.

## Verification After Revision

- `openspec validate add-runtime-tool-surface-discovery --strict` — pass.
- `make check-spec-overwrites` — pass, zero unfrozen losses from this change.
- `make check-em-dashes` — pass.
- `make check-countable-tasks` — pass.
- `make check-duplicate-names` — pass.
- `git diff --check` — pass.

The repository-wide external `spec-trace-check.py --authoring` remains red on
pre-existing legacy-spec debt and on a known tooling conflict: repository
OpenSpec rules require new-capability `## Purpose` and terminal
`## Source References`, while the older trace script rejects those headings;
its immediate-scenario placement rule also conflicts with the body-aware guard
for legacy MODIFIED blocks whose unchanged inventories must remain in the
replacement body before scenarios. All other isolated authoring diagnostics
introduced by this change were cleared. OpenSpec strict and the body-aware
overwrite guard are the applicable mechanical gates pending repair of those
shared validator contracts.

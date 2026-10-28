# Evidence and policy decision

Use the full sanitized evidence and per-control rationale in docs/decisions/consolidation-throughput-2026-10-15.md. It reports one current source-bound dev snapshot plus a seven-day session cohort; it does not infer sustained backlog history, whole-image equality, whole-pipeline cost or dollar cost. The actual measurement preceded these decisions.

All four controls are deliberately declined as additional controls for this evidenced present state. Existing batching, ordinary default 100 and existing backfill 500 and catalog-backed Spawner behavior are retained. No threshold is proposed. The four declines have been reviewed and accepted for publication under bu-de1hrl. No runtime change is authorized.

The spec delta contains the complete existing REQ-module-memory-005 unchanged and appends five decision scenarios. No existing tenant, evidence, retry, no-spawn, private-pool, timeout or security contract is removed. Runtime source and docs/modules/memory.md are excluded because their public custody belongs to other changes.

Verification is docs/spec-only: inspect the complete-preservation diff; run the repository's actual OpenSpec strict validator for this change and applicable source/docs guards after allocation. No pytest or new runtime test is warranted for text-only retention of existing behavior. Actual strict validation and applicable static guard receipts are retained with the publication packet.

Rollback removes the added decision scenarios/change and evidence document before merge, or reverts the eventual documentation commit. It has no data, runtime, catalog or credential side effects. A later adoption requires a fresh reviewed threshold/action and separate implementation issue for each adopted control.

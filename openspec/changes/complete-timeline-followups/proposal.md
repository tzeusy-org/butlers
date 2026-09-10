## Why

The owner cannot see recent failures above the fleet chronology, select a busy minute beyond the loaded page, or move through its rows with j/k. bu-ddo0n records those three follow-ups, but incorrectly assumes an unresolved attention feed and treats an ingestion-only histogram as a dependency.

## What Changes

Propose three additions to `/timeline`: a bounded recent-records-marked-failed strip with real aggregate counts; a server-counted minute-density control that loads the selected historical interval; and row navigation plus existing-view palette actions. `/` continues to focus the shell's global search, as the existing shell contract requires.

**Status: proposed, not adopted.** Approval of this artifact is required before implementation. Shaping authorization does not approve the proposed behavior. In particular, the strip says **Recent records marked failed**, not **Unresolved**: failed sessions have no resolution state, and `public.attention_ledger` records terminal egress decisions rather than an unresolved work queue. The bounded current-status interpretation is the concrete recommended scope clarification; a new unresolved-work lifecycle would require a different design.

## Capabilities

### Modified Capabilities

- `dashboard-visibility`: add interval density/seek, recent-failure attention, and keyboard traversal requirements without replacing the existing Unified Timeline requirement.

## Impact

API router and versioned timeline read model; timeline response DTOs and frontend API/hooks; TimelinePage/Ledger; existing page-action registry. Read-only endpoints and additive optional list parameters. No schema migration, raw content disclosure, new source family, background job, LLM work, or external service.

Non-goals: retry/acknowledgement mutations; durable resolution state; per-minute counts from loaded rows; client-wide shortcut changes; local text-search API; ingestion histogram changes; saved-view schema changes; filtered prefetch; redesigned shell badges; full-page rebuild.

## Funnel and authority

Baseline: `66ed58f7f7e80963d449500141ed4cb069209967`.
Size: medium, with shared read-model and URL contracts requiring independent review.

- G0 [Observed]: all five pillars exist. `about/heart-and-soul/vision.md` (owner observability), `about/lay-and-land/` (API aggregation), `about/legends-and-lore/` (read boundaries), `openspec/specs/dashboard-visibility/spec.md` (Unified Timeline), and `about/craft-and-care/` (verification) govern this proposal.
- G1 [Observed]: bu-ddo0n and `docs/redesigns/2026-07-03-jarvis-audit.md` One Timeline identify failure triage, density and keyboard follow-ups.
- G2 [Inferred]: read-only, honest failure visibility serves the single owner's observability; no daemon intelligence or inter-butler write boundary changes.
- G3 [Observed]: router `src/butlers/api/routers/timeline.py` already fans out through `api/read_models/timeline_v1.py`; no new cross-schema shortcut is needed.
- G4 [Observed]: `attention_ledger.py` is a terminal egress log; timeline failure predicates are `success=false` and notification `status='failed'`. The design below reuses those facts and the Dispatch design language/accessibility bar.
- G5: three added requirements, with deterministic source, interval, degradation and navigation scenarios. No active delta with these three titles found; recheck at materialization.
- G6: real-Postgres aggregate/page evidence, DOM keyboard/navigation checks, right-sized tests and hosted merge queue evidence.

Sign-off: pending. Independent semantic review: PASS on 2026-09-10 after correcting mutable failure-status semantics, aggregate availability, palette registration and atomic chart/bucket URL persistence. Scope approval is still pending. Default if unanswered: leave existing implementation unchanged and children blocked.

## Narrow owner decision: what the attention strip means

The original structured acceptance called its items unresolved. That objective is preserved here as an explicit unresolved decision, not silently dropped.

1. **Recommended: recent records currently marked failed.** Approve the concrete read-only strip specified here. It gives useful inspection of recent records still marked failed, not historical occurrence or recovery evidence; acknowledgment and retry claim can remove a notification record before delivery recovery. It does not promise a resolution lifecycle. Choosing it explicitly replaces the original unresolved wording for this bead.
2. **Canonical unresolved conditions.** Keep the original unresolved objective. Defer this strip while a separately reviewed design defines which producers open, recur and resolve each session/delivery condition, the durable identity, late-success/retry behavior, and whether owner acknowledgement differs from resolution. The terminal attention ledger cannot supply this contract. Density and keyboard can be approved independently; this strip child stays blocked.

Default without approval: retain current product; no attention implementation. Approving density/keyboard alone does not implicitly approve option 1 or create new unresolved-condition producers.

## Proposed handoff graph

[Implementation packets](implementation-plan.md) define the three vertical outcomes and their terminal reconciliation. They remain proposed until the exact artifact is approved.

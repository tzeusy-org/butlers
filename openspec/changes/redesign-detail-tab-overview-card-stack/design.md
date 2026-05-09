## Context

The Dispatch redesign replaces the Butler detail Overview tab's mixed layout
(identity card, module health, cost card, eligibility, and recent notifications
feed) with a deliberate, ordered card stack. The goal is to make the Overview
tab the canonical identity surface for a butler: dense, glanceable, and composed
entirely from existing data surfaces.

This change depends on two prior merged OpenSpec changes:
- `redesign-butler-detail-no-hero` — establishes that no Tier 2 Hero should
  appear above the tabs; the Overview tab IS the identity surface.
- `add-butler-process-facts` — defines the process facts card that replaces the
  previously-proposed (and PID-containing) runtime info surface.

## Goals / Non-Goals

**Goals:**

- Enumerate exactly seven ordered cards/rows in the Overview tab.
- Preserve existing eligibility behaviors (restore mutation, quarantine reason,
  24h timeline, 60-second refresh) inside the new card layout.
- Preserve module health rendering and "No modules registered" empty state.
- Replace the recent notifications feed with a recent sessions card sourced from
  `useButlerSessions`.
- Keep the stack entirely backed by existing hooks/endpoints; no new API required.

**Non-Goals:**

- Do not add a Tier 2 Hero block above the tabs.
- Do not introduce a 24-hour activity stripe (no per-butler hourly API endpoint
  exists; the Dispatch prototype's stripe is not adapted here).
- Do not introduce new backend endpoints; reuse `useButler`, `useButlerHeartbeats`,
  `useButlerModules`, `useCostSummary`, `useButlerSessions`, and the switchboard
  eligibility hooks.
- Do not rename or restructure the existing hook contracts.

## Decisions

1. **Seven-unit card stack as the canonical layout.** The stack order is:
   identity card → process facts card → heartbeat row → module health card →
   cost card → recent sessions card → eligibility row. The order places stable
   identity facts first and live/operational facts toward the bottom.

2. **Eligibility row preserved inside the identity card.** The eligibility state,
   restore mutation, quarantine reason, and `EligibilityTimeline` (24h history)
   continue to live inside the identity card area. Moving them to a separate card
   would fragment information that users need together.

3. **Heartbeat row inside the identity card (not a separate card).** The heartbeat
   freshness pill (`Fresh`/`Stale`/`Dead`/`Unknown`) and last-seen timestamp are
   rendered inside the identity card's KV list, co-located with identity and port.
   This matches the Overview-as-identity-surface principle.

4. **Recent sessions replace recent notifications.** The Epic 04 source plan
   adapts the Dispatch card stack to existing session data hooks. The notifications
   feed is moved to a secondary card at the bottom rather than being dropped;
   the Recent Sessions card becomes the primary "recent activity" surface.

5. **Unified loading state.** A single loading gate (butler data loading) shows
   the full `OverviewSkeleton`. Per-card skeletons are used for secondary data
   (modules, heartbeat, cost, sessions) to avoid layout shifts when the primary
   butler load completes first.

6. **No `ButlerMark` in the identity card title row.** The implementation places
   `butler.name` and `ButlerStatusBadge` in the card title; `ButlerMark` is not
   included. The spec change text references `ButlerMark` as a `SHALL` requirement
   but the merged implementation omits it. This is tracked as GAP-1 in the
   reconciliation report.

## Risks / Trade-offs

- **Cost card simplification vs. spec.** The spec (main and change) requires the
  cost card to show the butler's USD cost, its percentage share of the global
  total, and the global total. The merged implementation shows only the
  per-butler `by_butler[name]` cost for Today and Last 7d, without the global
  total or percentage share. This is tracked as GAP-2 in the reconciliation
  report.

- **Recent notifications card preserved as secondary.** The `redesign-detail-tab-overview-card-stack` proposal replaces recent notifications with recent sessions as the primary feed. The implementation retains a "Recent Notifications" card at the bottom of the stack, sourced from `useButlerNotifications`. This is an additive deviation (extra card, not a missing card) and does not violate the spec, but it means the tab has eight units rather than seven.

- **OpenSpec sync pending.** The main spec at
  `openspec/specs/dashboard-butler-management/spec.md:244-279` still describes the
  old Overview tab layout (without the seven-unit card stack). The sync step
  (`/opsx:sync`) should be run to apply the `MODIFIED Requirements` delta from
  this change directory to the canonical spec.

## Why

The calendar module fetches provider events with `singleEvents=false`, so each recurring series collapses to one row, and the day-load arithmetic (`src/butlers/core/temporal/conflicts.py`) charges each timed span wholesale to its start date and has no category input, so a multi-day hold or a flight inflates "meeting" load. The owner's day is wrong on both axes: what is on it and how full it is. The governing intent is that the calendar must match the provider-visible calendar instance-for-instance, and its load signal must never manufacture occupied time or attendance. This change is the spec gate (bu-vpn5ij) for bu-h40h2b.6; no implementation code is in it.

Owner decisions already made (bu-2o1dn8, engineering call): **A1** an additive, settled-historical, coverage-aware, non-attendance Chronicler reconciliation run that reports only deterministic contradictions and unknown coverage; **B1** classification only from explicit structured provider or owner category evidence, with an honest uncategorized state and no title heuristic.

## What Changes

- Provider occurrences are keyed by `(source, recurringEventId, originalStartTime)` in the existing `calendar_events` + `calendar_event_instances` ledger (no parallel table); moved instances keep identity; a cancelled exception suppresses one occurrence.
- A versioned cursor reset with a bounded, commit-at-end full resync that never reuses a `singleEvents=false` token, never clears the prior projection on failure, is idempotent on retry, and has a defined rollback.
- A bounded projection window with a recorded `coverage_through`, and partial/stale coverage that never reads as an empty calendar.
- Category semantics (meeting, hold, travel, all-day, uncategorized) from structured evidence only, and what counts toward density.
- Per-local-day clipping of day load in the display timezone; DST days bound the invariant at 23 or 25 hours; the shared `calendar_provenance.is_calendar_analysis_candidate()` filter is preserved.
- Additive `day_load` on `ConflictScanResponse`, and dashboard occurrence/day-load/reconciliation presentation; the reconciliation line is absent for current and future days.
- Chronicler-owned settled-day non-attendance reconciliation run and read contract (S3 under A1). The canonical rule "calendar intent is not attendance" is kept verbatim and restated in every block that touches it.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `module-calendar` (ADDED only: REQ-module-calendar-031..034)
- `calendar-conflict-overcommitment-radar` (ADDED only: REQ-calendar-conflict-overcommitment-radar-010..012)
- `dashboard-domain-pages` (ADDED only: REQ-dashboard-domain-pages-050..052)
- `butler-chronicler` (ADDED only: REQ-butler-chronicler-008..009)

Every block is ADDED; no canonical requirement is MODIFIED, so no baseline text is rebuilt and no `scripts/spec-overwrite-baseline.json` clause is frozen. The existing "Superseded provider instance pruned on time-drifted re-sync" scenario stays valid for non-recurring events and is not edited: provider-expanded instances have distinct ids, so the prune never touches siblings, and the occurrence key makes a moved instance an in-place update.

## Impact

- Spec-only; no code, schema, grant or provider behavior ships here. The implementation bead (bu-h40h2b.6) will need a core-chain migration (instance metadata/category and cursor version, derived from the merged graph at that time) and real-PostgreSQL tests; those are named in tasks.md and are CI-only locally.
- Chronicler reads only the approved restricted calendar surface under `butler_chronicler_rw`; no new broad grant, and Calendar gets no cross-schema write authority.
- Expected tests for this spec PR: +0 ~0 -0.

## Overlaps (disclosed up front)

- **#4488** (bu-ed7nzt, `fix(calendar): fence responses across owning schemas`, open, ready) edits `openspec/specs/module-calendar/spec.md` in the "Approval-Bound Self Invitation Response" region. This change touches no existing module-calendar requirement, so there is no text conflict; the only hazard is archive ordering of the same file. Merge origin/main after #4488 lands before this PR is final.
- **#4585** (foreign draft, codex coordinator, bu-svqpzo.1) also edits module-calendar. Not touched; no shared requirement.
- **#4394** (`calendar-context-truth`, stale: open, CONFLICTING since 2026-10-07, `agent/bu-s11n0s.4`) MODIFIES module-calendar "CalendarEvent Model" and "Projection Provenance Truth and Source-Ledger Hygiene" and radar "Forward-Window Conflict Scan Endpoint", preserves provider event type and declared working-location fields (core 262) and removes status-only events from day-load hours. It overlaps B1 evidence: `REQ-module-calendar-034` retains provider `eventType`, attendee `self`/`resource` flags and conference-data presence as projected event metadata (the `CalendarEvent` model is unchanged) and maps `outOfOffice`/`focusTime` to `hold`. Second-to-land rebuilds: if #4394 lands first, REQ-034 reads its `event_type` column instead of the metadata copy; if this lands first, #4394 rebuilds on REQ-034. A `workingLocation` row counts toward no day-load bucket, matching #4394's zero-duration rule. **Conflict:** #4394 keeps `focusTime` (and unknown types) in meeting hours, while REQ-034 counts `focusTime` as `hold` (zero meeting minutes); whichever archives second contradicts the other (owner question 6). Its ID reuse (`REQ-module-calendar-001/002`, `REQ-calendar-conflict-overcommitment-radar-001`) is outside this change; the radar IDs here start at -010 to stay clear of it.
- **generalist-lead-focused-delegation** (RFC 0038, pending) MODIFIES module-calendar "CalendarEvent Model" and mints `REQ-module-calendar-030`; this change does not edit the model requirement and takes 031..034. It also MODIFIES several butler-chronicler requirements ("Butler Identity and Wire Contract", "Retrospective-Only Scope", "No Per-Event LLM Invocation", "Switchboard Routing Boundary"); this change ADDs two new requirements and shares none. Cutover neutrality: the Chronicler run is a scheduled deterministic job, not a prompt-mode session and not an LLM invocation.
- **chronicler-telemetry-distillation** (pending) ADDs "Retention-Window-Aware Projection Adapters" to butler-chronicler; no shared requirement. Its adapter retention window should be read together with REQ-butler-chronicler-008 (the run must not treat retention-expired evidence as absence).
- **memory-honesty-last-mile** and **harden-memory-retention-schedule-recovery** touch dashboard-domain-pages memory requirements only; no shared requirement.
- **bu-h40h2b.5.2.2 / PR #4187** merged on 2026-09-23, so it is no longer a sequencing hazard; implementation starts from a tree that already contains its find-time provider-failure work (tasks.md 0.2).

## Decisions taken in this draft ([decision])

- Unique occurrence key is `(recurringEventId, originalStartTime)`; `origin_instance_ref` for recurring instances no longer embeds the display start, so a move updates in place and the Chronicler `calendar:{origin_instance_ref}` key is stable.
- Forward projection horizon defaults to 180 days (at least the 90-day radar maximum); a horizon-advance full resync is needed because an incremental token cannot carry `timeMin`/`timeMax`.
- Day load is reported as a lower bound (`meeting_minutes`) plus a separate `uncategorized_minutes`. A warning-grade overload needs evidenced meetings; uncategorized overflow yields an info-grade possible overload.
- The reconciliation read contract is placed in butler-chronicler (REQ-009) rather than chronicler-api, to keep one capability delta.

## Open owner questions

1. **Is multi-party structure meeting evidence?** REQ-module-calendar-034 treats at least one non-self, non-resource attendee or provider conference data as structured `meeting` evidence. Without it nothing is `meeting` until the owner categorizes it. Alternative: owner category only (stricter, near-zero meeting load at first).
2. **Uncategorized in the overload warning.** The draft downgrades overload that depends on uncategorized time to `info`. Alternative: count uncategorized as meeting (status quo, flights still inflate) or exclude it entirely (hides overload).
3. **How does the owner set a category?** The draft only requires an explicit owner action recorded on the event and never overwritten by sync; the surface (dashboard control, MCP tool) is left to the implementation bead and needs owner confirmation.
4. **Horizon and thresholds.** Confirm the 180-day horizon and the horizon-advance fraction.
5. **S3 placement.** Confirm the read endpoint belongs in butler-chronicler rather than chronicler-api, and the settling delay for "settled".
6. **focusTime: hold or meeting hours?** REQ-034 counts `focusTime` as `hold`; #4394 keeps it in meeting hours. Recommendation: hold, because a focus block is not a meeting and #4394 itself asserts it as `focused`.

## Overwrite safety

`make check-spec-overwrites` and `openspec validate --all --strict` are run on this change. Because all blocks are ADDED, an archive conflict is limited to ID or requirement-name collision with pending changes; IDs were checked against canonical specs and active changes on 2026-10-18.

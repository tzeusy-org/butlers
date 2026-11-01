## Purpose

This capability specifies the future D19 dashboard mind overview owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: Whole-mind editorial overview

After the signed D19 design and the existing program M6 cutover, overview SHALL use the Dispatch editorial archetype to answer what the one assistant is doing, what needs the owner and what obligations are due. It SHALL show active goals, participating regions, due obligations, parked owner decisions, budgets and service exceptions with independent coverage and actionable deep links. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change. The owner SHALL reach global Hold, Hold and stop all, optional until and Release from Overview. Hold SHALL defer new non-owner autonomous admissions and proactive owner deliveries under the owning Hold contract, without creating a held goal state. Owner-origin goals and replies, approval prompts and control acknowledgements SHALL continue; already-running goals SHALL continue under their budgets unless separately stopped, and deterministic service maintenance SHALL continue. A persistent shared-shell status SHALL show confirmed held facts across Overview, goal detail, Region, read-only Operator and Approvals, including since, authenticated by/channel, reason, optional until, durable deferred-admission/delivery counts, observation time and independent coverage. A missing or failed status/count read SHALL be unavailable or last-known stale, never unheld or zero. Only Overview SHALL expose hold mutations; other surfaces SHALL offer a read-only status and a named Overview door.

ID: REQ-dashboard-mind-overview-001
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md; bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md
Scope: post-v1

#### Scenario: Morning overview

- **WHEN** the owner opens Overview
- **THEN** an editorial voice paragraph, completed-goals hero, active-goal rows, due obligations, region index and owner-decision rail use hairlines and calibrated density; the assistant is singular and service health is secondary infrastructure.

#### Scenario: Completed goals hero

- **WHEN** the dashboard computes completed goals today
- **THEN** only actual D11 completed goals in the stated day/timezone count, with delivered replies for owner-message goals; children, sessions, retries, partial/stopped/budget_exhausted goals and unknown effects are not counted as successes.

#### Scenario: Unavailable completion source

- **WHEN** completion or delivery evidence is incomplete
- **THEN** the hero is unavailable or explicitly partial with its coverage and period; it never renders a confident zero or treats a cached count as current.

#### Scenario: Owner attention

- **WHEN** a parked effect, failed delivery, overdue obligation or live service exception needs attention
- **THEN** the rail shows the exact current receipt and a named destination; recent completed work is not folded into active attention, and unavailable approval metrics never become an all-clear.

#### Scenario: Engage a global Hold

- **WHEN** the owner selects Hold, reviews its global scope and supplies the required reason with an optional valid future until in the displayed timezone
- **THEN** one deliberate submit requests engagement through the signed dashboard service client, waits for its durable receipt and then displays the confirmed held facts; no running goal, parked effect or committed action is reported stopped or undone.

#### Scenario: Hold and stop all has two receipts

- **WHEN** the owner explicitly selects Hold and stop all and reviews that Stop cancels parked effects and cannot undo dispatch
- **THEN** engagement is requested first and Stop-all is requested only after engagement is confirmed; the two receipts remain separately identifiable, and a failed or unknown engagement cannot launch Stop-all.
- **AND** a confirmed Hold with failed, partial or unconfirmed Stop-all remains Held, exposes the actual per-goal results and offers only the owning idempotent reconciliation/retry path; it never claims all goals stopped.

#### Scenario: Hold status persists across owner destinations

- **WHEN** the owner reloads or navigates among any of the five D19 surfaces while held
- **THEN** authenticated status restores the same global since/by/channel/reason/until and durable deferred counts with their coverage; goal detail and read-only Operator do not acquire Hold, Release, Stop-all or regional editing controls, and existing per-goal Stop and eligible approvals retain their own authority.

#### Scenario: Release and optional until retain deferral truth

- **WHEN** the owner explicitly selects Release or the stored until is reached through the owning control-plane tick, including after restart
- **THEN** both use the same durable release path; manual UI success waits for the actual receipt, and deferred-work progress remains distinct from the unheld state.
- **AND** recurring occurrences coalesce to the latest with earlier skipped_hold receipts; one-shot, caused and third-party items release once in arrival order, deferred deliveries use one digest per channel with original reminder due times, and re-engage interrupts release under the owner contract without duplicates or lost obligations.

#### Scenario: Unknown or unavailable Hold is never an all-clear

- **WHEN** engagement or release disconnects, status cannot be read, or only some deferred counts are available
- **THEN** the banner names unconfirmed/unavailable and any last-confirmed state with observation time, unknown counts are not zero, and a retry first reconciles actual status/idempotent receipt identity.
- **AND** the UI does not infer eligibility or relax the owning fail-closed non-owner/proactive gate from its cached banner; owner replies and allowed prompts retain their stated exceptions.

#### Scenario: Repeated Hold commands preserve owner audit

- **WHEN** concurrent or repeated engage/release operations return the existing state
- **THEN** the UI uses the owning durable state and audit receipts rather than adding duplicate work; actor/channel are server-derived, reason is displayed as data, invalid until is refused before submission and server refusal remains visible.


### Requirement: Dispatch interaction and accessibility

After the signed D19 design and the existing program M6 cutover, overview, goal detail, region, Operator and Approvals SHALL reuse existing Page archetypes and canonical Dispatch tokens, fonts and primitives. The operator/resident projection is discoverable through URL state and command palette. State changes, approval and Stop are receipt-authoritative and motion is restrained. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change. Global hold actions SHALL be deliberate, named, keyboard-accessible owner choices with one primary commit at a time; Hold and stop all SHALL disclose both effects before submission. The held banner SHALL remain readable without color alone and SHALL link to Overview rather than place a mutation inside the read-only Operator projection.

ID: REQ-dashboard-mind-overview-002
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md; bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md
Scope: post-v1

#### Scenario: Keyboard and coarse pointer

- **WHEN** the owner uses keyboard or a 360 CSS-pixel coarse-pointer viewport
- **THEN** all doors and disclosures have visible focus and names, at least 44-pixel coarse targets, no hover-only facts, and no page overflow; wide receipts scroll within a named focusable region.

#### Scenario: One commit action

- **WHEN** the owner reviews a goal Stop or exact-call approval
- **THEN** the surface has at most one visually primary commit action, its label names the exact effect, and disabled/cancelled/unknown states explain why; no optimistic success is shown.

#### Scenario: Command palette and quick switching

- **WHEN** the owner uses Cmd-K or region/goal recall
- **THEN** the same named destinations and permitted verbs are discoverable without duplicating authority; destructive/approval actions require the same eligibility and exact-call display as pointer paths.

#### Scenario: Theme and reduced motion

- **WHEN** the owner switches light/dark theme or requests reduced motion
- **THEN** canonical fg/bg/mfg/state/focus tokens and Inter Tight, Source Serif 4 and JetBrains Mono remain; no decorative animation, cards or shadows are added and meaning never depends on color alone.

#### Scenario: Hold controls and banner are accessible

- **WHEN** the owner uses native pointer or keyboard activation in either theme on desktop, tablet or phone
- **THEN** Hold, Hold and stop all, optional-until input, Release, receipt disclosures and the Overview banner door have names, visible focus, at least 44 by 44 CSS-pixel coarse targets and contained wrapping; pending feedback never replaces confirmed facts with success.
- **AND** only Theme changes theme, every status uses the retained single Dispatch state affordance without an empty dot, and commit pills and rule-separated rows retain their established forms.


### Requirement: Dark design and complete migration authority

After the signed D19 design and the existing program M6 cutover, this change SHALL remain a future design specification until owner signoff and the existing program M6 cutover. It MUST NOT activate pages, controls, endpoints or enforcement. Every baseline and active contract disposition in contract-migration.md SHALL be reviewed against its full old scenario body before archive or implementation; naming/count/hash evidence is not semantic proof. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-mind-overview-003
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Main before cutover

- **WHEN** this specification or its mockups are present on main before M6
- **THEN** existing runtime and dashboard behavior continues; any prospective one-mind control is default-off and unreachable, and implementation lies in the existing gated program rather than this change.

#### Scenario: Active owner collision

- **WHEN** a requirement is also owned by an unarchived change or live PR
- **THEN** the exact owner-controlled contract must be adopted/archived first or transferred atomically with its complete scenarios and IDs; a quiet branch or choosing another file does not resolve the semantic collision.

#### Scenario: Final experience signoff

- **WHEN** the complete spec PR, all five mockups and fresh independent whole-boundary review are ready
- **THEN** ROOT links them on bu-xmd7yu with the original HOLD decision request; no page, switch release, custody expansion or kernel amendment is authorized by a partial mockup.

#### Scenario: Required validation

- **WHEN** the allocated documentation/spec patch is ready for review
- **THEN** strict OpenSpec validation, authoring/ID/source/scope/task trace qualification and every check-guards step must produce actual receipts; Tests remains +0 ~0 -0 and an unexecuted gate remains unearned.

## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
- Amendment source: bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md
- Amendment source: bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md

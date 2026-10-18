## Why

`public.entity_graph_edges` is a derived projection of Relationship facts, but it is not kept in step with them. An identical reassert of an entity-to-entity fact returns `unchanged` and skips projection, so a missing edge stays missing. The only repair, `backfill_relationship_entity_facts_edges()`, has no caller outside tests, adds rows only, and leaves no durable record of what it did. Separately, the Rule detail page renders `effectiveness 0.00` over a zero denominator, and fleet-case evidence is shown as opaque ids.

bu-h40h2b.7 (run-13 move 7) is blocked until the owner adopts one bounded amendment that fixes the scope, receipts, schedule, ratio rule and ownership of name rendering. This change is that amendment, drafted for owner adoption. It contains no code.

## What Changes

### New requirements in `entity-graph` (REQ-entity-graph-013 through 018)

- A relationship-only reconciler over `relationship.entity_facts` to `public.entity_graph_edges` (add, correct, remove), never touching Memory or commitment rows.
- An identical reassert repairs its own missing projection in the same transaction.
- A content-blind receipt per attempt, with statuses `success`, `partial`, `failed`, `skipped_lock`, and fixed retention.
- A deterministic job-mode daily schedule, a non-blocking advisory lock, and fixed batch limits.
- Receipt presentation that never claims completeness from a zero-count or partial run.
- Named fleet-evidence rendering handed to bu-h40h2b.8.

### New requirement in `dashboard-domain-pages` (REQ-dashboard-domain-pages-050)

- Rule effectiveness ratio renders only when `applied_count >= 1`; the exact threshold is a zero denominator and the empty copy is `No applications yet`.

### RFC 0031 Amendment 1

An added section in `about/legends-and-lore/rfcs/0031-public-entity-graph-projection.md` records the decision and trade-offs and points at these requirements. The status table rows are not edited here (see Overlaps).

## Out of Scope

- Any reconciler code, TOML schedule entry, receipt table, migration, API, UI, live run or backfill.
- A sweeper over Memory, commitment or any other source, or any cross-schema access.
- Any change to rule scoring or to the rule response shape.
- Fleet-case name and link rendering (bu-h40h2b.8).
- A consumer of `entity_graph_edges` in entity detail beyond what RFC 0031 S5/S6 and #4646 already cover.

## Overlaps and ordering (§0 steps; residual conflicts become owner questions)

1. **#4646 (bu-8cdl1.8, dossier API and panel).** It edits the RFC 0031 status rows S5/S6 and the entity-graph area. This amendment touches no status-table row and adds an independent section, so it merges cleanly in either order. Step 0.1: after #4646 merges, merge `origin/main` and re-read RFC 0031 and `entity-graph/spec.md` before adoption.
2. **entity-chronicles-and-commitment-doors.** Its proposal lists "a consumer for `entity_graph_edges`" as out of scope and edits `dashboard-relationship` only (its REQ-dashboard-relationship-005 renumber is in #4648). This change adds no dashboard-relationship delta and no consumer of the table. Step 0.2: confirm no `entity-graph` or `dashboard-domain-pages` delta appears in that change before adoption.
3. **generalist-lead-focused-delegation (RFC 0038).** It moves prompt-mode schedules to obligation admissions and states that a job function does not start a reasoning session. This reconciler is job-mode only and depends on no prompt-mode scheduling, goal admission or capability grant, so the cutover leaves it unchanged (the lesson from #4649). Step 0.3: if that change alters `dispatch_mode='job'` semantics before adoption, re-read the core-scheduler delta and refresh REQ-entity-graph-016.
4. **relational-edges-single-home** is archived (2026-09-27), so the serialization its carrier once demanded no longer applies. Step 0.4: confirm no active change owns `relationship_assert_fact` before implementation starts.
5. **bu-h40h2b.8 (situations).** Receives name and link rendering through REQ-entity-graph-018; this change does not edit its work.

## Owner questions

- Q1: Approve receipt retention of 90 days plus the latest receipt of each status (REQ-entity-graph-015), or name different figures.
- Q2: Approve a daily cadence and the 500 rows by 20 batches bound (REQ-entity-graph-016), or name different figures.
- Q3: Approve the exact ratio threshold (`applied_count = 0`) and the copy `No applications yet`, or name a different threshold or copy.

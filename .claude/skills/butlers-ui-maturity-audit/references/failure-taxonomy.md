# Failure Taxonomy — UI-immaturity shapes, ranked by user-deception

Load in **Phase 1/2**. These are the shapes a flow audit hunts, ordered by how much each one
deceives the user (worst first). A literal no-op `onClick` is the *least* common and least
deceptive failure here — do not stop at dead buttons; the persistent-but-inert controls (1–3) are
where real immaturity hides.

## Maintenance contract (read this first)

This is a **living catalog**. The numbered *shapes* (1–8) are durable. The `e.g.` instances are
**dated, illustrative observations** from specific audits — they rot (a cited bug gets fixed; a
path moves). Rules for consuming and maintaining it:

- **Verify before citing.** Any `e.g.` is point-in-time. Re-confirm against current `main` before
  presenting it as live; if it's been fixed, that's a *win to report*, not a finding.
- **Append** a new numbered shape when an audit surfaces a failure that fits none below — number
  it, write its one-line definition + the *Tell* (how to detect it) + one dated `e.g.` with
  `file:line` and the audit month. Do this in the same run, not as follow-up.
- **Retire** an `e.g.` once fixed upstream: note the fixing evidence/date, drop it one revision
  later. Retire a whole shape only if a later audit shows it's structurally impossible now.

## The shapes

1. **Decorative persistence** — a control writes a table/row that no runtime code reads. *Tell:*
   `grep <table_or_column>` across `src/` finds only the writer (and the data-wipe list), no
   runtime reader. *e.g.* (2026-06) settings surfaces where a matrix/rule/ceiling persists but no
   runtime path consumes it — the user thinks the setting took effect; it changed nothing. **Do
   not pin the "permissions" instance to a specific guard module without re-reading it** — at
   least one runtime write-guard in this repo is keyed by activity-state and *is* enforced; verify
   which table the UI writes and whether *that* table has a reader before calling it decorative.

2. **The lie / overpromise** — success feedback for work that didn't happen. *Tell:* the
   `onSuccess` toast/redirect fires unconditionally; trace whether the asserted effect actually
   ran. *e.g.* (2026-06) a "retry" that toasts "re-dispatched" but only inserts a row — the
   dispatch function was never wired, so nothing runs until an out-of-band watchdog maybe picks
   it up.

3. **Data-contract break** — the FE reads a field the backend never writes (it writes the value
   somewhere else). *Tell:* `grep` the literal value; the read sites and the write site disagree.
   *e.g.* (2026-06) a lifecycle status read from one column but written to a `metadata.status`
   sub-key → the feature is permanently empty even though both ends "exist." (This was a live bug
   at audit time — confirm current state before reusing.)

4. **Fake / placeholder data dressed as live** — hardcoded values, timers posing as liveness,
   KPIs keyed off a field that can never match. *Tell:* the value is a literal in the route
   handler or component, not a query result. *e.g.* (2026-06) a "Live" badge driven by
   `setTimeout`; a PR panel rendering `ci unknown · +0 / -0`; an "Errors" KPI keyed off a field
   whose domain never includes "error" → always 0.

5. **Backend-ready, frontend-not-wired** — route + hook exist; no component invokes them; or the
   spec mandates an affordance (e.g. an "Add X" dialog) that simply isn't rendered. *Tell:* an
   orphaned `use-*` hook with no caller, or a `POST` route with zero callers under `frontend/`.

6. **Frontend-wired, backend stub/404** — a handler calls an endpoint that doesn't exist, 503s,
   or returns `accepted:false`. *Tell:* the client fn has no matching `@router` decorator, or the
   route body is a no-op/guard that never executed in this process. *e.g.* (2026-06) a drawer tab
   calling a `/payload` endpoint that was never implemented.

7. **Orphaned routes / navigation gaps** — a fully built page with no inbound link, or
   search/filter that silently drops results outside the loaded page. *Tell:* `grep` the route
   string finds the `path:` registration but no `<Link to=…>`/`navigate(…)` to it; or a search
   intersects results with the current page client-side. *e.g.* (2026-06) a detail page reachable
   only by typing the URL.

8. **Missing states** — no loading/empty/error/degraded rendering, or a mutation with no
   success/failure feedback. Includes treating a degraded envelope (`aggregates_available:false`)
   as an error instead of "metrics unavailable." *Tell:* the component renders `data?.x` with no
   branch for pending/error/empty.

9. **Invariant-data control** — a control/affordance is honestly wired to a real column, but that
   column is structurally constant at every write site, so the control's signalling range is dead
   and its derived states are unreachable. Distinct from shape 4 (fake/placeholder *literal* in
   JSX): here the binding is truthful — the deadness is in the data layer, not the render. The UI
   implies an axis of information the system never populates. *Tell:* `grep` the column's write
   sites — every writer hardcodes/defaults the same value (no path computes a varying one); then a
   conditional branch keyed on a threshold of that value is never taken. *e.g.* (2026-06, entities)
   the per-fact confidence bar reads a real `conf` column and has an `amber < 0.85` low-confidence
   branch, but `store_fact`/`relationship_assert_fact` write `conf=1.0` at every call site
   (`src/butlers/modules/memory/storage.py:767`; no ingest path calibrates), so the bar is always
   100% and the amber branch can never fire — the "confidence axis" carries zero information.
   Likewise `verified=true` is reachable only for the prefers-channel predicate, so the green
   verified `✓` is near-constant on ordinary facts. *Fix posture:* either populate the column with
   real variance, or descope the control so it stops implying calibration that doesn't exist.

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

10. **Mislabeled real data — semantic-axis mismatch** — a control surfaces a *real, correctly-read*
    column, but under a label that asserts a different aggregation or dimension than the writer
    actually computes. Distinct from shape 4 (the value is genuine DB data, not a literal/timer) and
    shape 3 (the read/write *agree* on the field — it's the human-facing *label* that lies) and shape
    9 (the data varies; only its meaning is misrepresented). The user reads a true number under a
    false caption and mistrusts the whole surface. *Tell:* `grep` the source column's writer and read
    its semantics (is it cumulative-since-process-start? a state enum? a 24h window?), then compare to
    the FE label/field name; they describe different axes. *e.g.* (2026-06, ingestion connectors) the
    roster's "events · today" KPI maps `counter_messages_ingested` — a lifetime cumulative counter
    ("monotonic … since process start", `heartbeat.py:326`) — onto `today.messages_ingested`
    (`ingestion_connectors.py:224-227`), so a connector shows "events today: 1,781,451,647" (live);
    the number is real, the word "today" is the lie. Same shape: `/cross-summary`'s
    `connectors_online/stale/offline` are populated from the `state` enum (healthy/degraded/error),
    not liveness, so they contradict the per-row liveness (16 "online" while ≥4 rows read
    `liveness:offline`). *Fix posture:* either compute the axis the label promises (a real 24h
    window / a real liveness count), or rename the field to the axis the data actually carries.

11. **Failure-degrades-to-benign across a multi-source join** — a page composes N data sources but
    wires its loading/error/empty/stale plumbing to only ONE (the primary). Every *secondary*
    source's failure is caught and replaced with a fallback that happens to render as a calm,
    healthy state, so a partial outage is indistinguishable from a genuinely quiet system. Distinct
    from shape 8 (a single component missing its own states): here each component renders fine — the
    deception is architectural, living in the join's per-source fallbacks plus an error surface that
    can only see the primary query. The page fails safe *visually* and dangerous *semantically*; the
    one moment it matters (an incident) is the one it hides. *Tell:* find the composite hook's
    aggregate error/loading fields and confirm they derive from a single `xQuery` while the other
    sources are read under `!yQuery.isError && yQuery.data ? … : <benign-default>` guards; then check
    whether any per-source `error`/degraded field the backend sends is consumed (often it is plumbed
    into the FE type but never read). *e.g.* (2026-06, `/butlers` status board) the composite
    `useButlerStatusBoard` joins 6 sources but `aggregates.{isError,error,refetch}` come solely from
    `butlersQuery` (`use-butler-status-board.ts:329-332`); a heartbeat outage falls back to
    `active_session_count=0` (`:246`) → every cell shows LOAD `0%` + LAST `—` + activity `idle` (a
    calm idle fleet, not an outage), and the backend's per-entry `error:"schema_unreachable"`
    (`system.py:743-749`) is never read; a registry outage falls back to eligibility `"unavailable"`
    (`:241`) which isn't subtracted from the healthy/total pill, so the pill stays green. *Fix
    posture:* derive the page's degraded indicator from the union of all source error states, and
    render `—`/"unavailable" (not `0`/"idle"/"healthy") whenever the source that would justify the
    confident value has failed.

12. **Backend-narrow vs frontend-wide status vocabulary** — the frontend derives user-facing states
    by branching on a richer status enum than the backend can ever emit. The branches for the
    never-produced values are dead code, and — worse — the values the backend *does* emit may have
    no branch at all, so they fall through to a misleadingly-benign default. Distinct from shape 9
    (there the column is structurally *constant*; here it genuinely varies, but over a different,
    narrower domain than the FE assumes) and shape 3 (read and write agree on the *field*; the
    mismatch is the *value domain*, not the column). A real, varying signal (e.g. "this butler is
    down") is silently dropped on the floor. *Tell:* enumerate the distinct values the backend writer
    actually produces (`grep` its `status = "…"` assignments, or hit the live endpoint and
    `Counter()` the field) and diff against the cases the FE `switch`/`if` handles; look for FE
    branches with no producing value (dead) AND backend values with no FE branch (fall-through). Bonus
    tell: a sibling surface maps the same value correctly, exposing the inconsistency. *e.g.*
    (2026-06, `/butlers` status board) `_probe_butler` emits only `status:"ok"|"down"`
    (`butlers.py:104-123`), but `deriveActivity` branches on `"degraded"`/`"waiting"`
    (`use-butler-status-board.ts:113-121`, never produced → dead) and has **no `"down"` case**, so a
    crashed butler falls through to `idle`/neutral/no-rail — rendered identically to a healthy idle
    one, and contradicting its own detail page which maps `down→red "offline"`
    (`ButlerOverviewTab.tsx:44-46`). *Fix posture:* reconcile the FE derivation against the backend's
    actual value domain — add branches for every emitted value (especially the unhealthy ones) and
    delete the dead ones; if the spec assumes the richer vocab, fix the spec and the producer too.

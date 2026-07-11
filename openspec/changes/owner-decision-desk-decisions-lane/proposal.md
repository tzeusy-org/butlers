# Owner Decision Desk: Dashboard Decisions Lane

## Why

`grep OWNER|DECISION over frontend/src/pages` returned zero hits: the dashboard
console never surfaced the owner decision queue. 14+ owner-attention beads
(bu-v4ipc, bu-zhfd0, bu-4pq0s, bu-wyftz, bu-4qfhl, bu-i4jbj, ...) have sat open
since 2026-07-04/05 with nothing anywhere in the product naming them, and a P1
silent-message-loss bug (bu-wzbu9) has been decision-blocked since 07-05.
bu-ckkpz.4 (merged, PR #3140) already computes the digest server-side
(`butlers.jobs.decision_review.compute_decision_digest`) for a weekly Telegram
push and an age-based escalation check, but that computation had no dashboard
reader -- an owner had to already know to check Telegram/bd directly.

This is slice 2 of epic bu-ckkpz ("Owner Decision Desk"): the Decisions lane
itself, following the fleet's standard verdict-opener + keyboard-triage
pattern already established by `dashboard-approvals`.

## What Changes

- **New API surface**: `GET /api/decisions`, a thin read-only wrapper around
  the existing `compute_decision_digest()` (no new database access -- reuses
  bu-ckkpz.4's beads-export JSONL read path verbatim). Returns open
  decision-marked beads oldest-first with escalation fields, and
  `meta.decisions_available` (degraded-envelope convention) so a beads-export
  outage never renders as a fabricated "no decisions waiting."
- **New page**: `/decisions`, nav-registered under "Main" (icon + sidebar
  badge counting open decisions), following the same single-list-per-page
  shape as `/approvals`, `/issues`, `/notifications`:
  - A verdict opener composing "N decisions waiting, oldest Xd" -- the exact
    vocabulary `decision_review.py`'s own weekly digest message already uses
    -- plus an escalation clause when any decision is blocking a P1 bug or a
    deploy.
  - A rule-separated row list with j/k roving selection
    (`useListTriage`/`useRegisterShortcut`, the shared pattern from
    `dashboard-approvals`/`dashboard-domain-pages`); the selected row is a
    door -- it expands inline to show everything the digest knows about that
    decision (age, priority, escalation detail).
  - No approve/deny/close actions yet: bu-ckkpz.1 (structured
    options/default/deadline convention) and bu-ckkpz.3 (attention-ledger +
    Telegram one-tap close) have not shipped, so a decision is detected by
    title marker only and carries no machine-actionable payload. The page
    says so explicitly rather than fabricating action affordances.

### Why a dedicated page, not an Overview lane

The epic text says "Dashboard Decisions lane"; the literal Overview page (`/`)
was considered first. It was rejected: every existing page in this app
registers at most one `useListTriage` instance (`useRegisterShortcut`
installs one independent `window` keydown listener per call, with no
cross-list scoping), and Overview's "Needs attention" list already owns one.
A second independent j/k-bound list on the same page would double-fire on
every j/k keypress, moving both lists' selection simultaneously -- a real,
reproducible interaction bug, not a style preference. A dedicated `/decisions`
page (mirroring `/approvals`) gives the lane its own keyboard scope, its own
URL, and a real "door to the bead's options" target for the sidebar badge and
future `notify()` deep links (bu-ckkpz.3), at the cost of one extra nav entry.

## Impact

- Affected specs: `dashboard-decisions` (NEW capability), `dashboard-api`
  (ADDED requirement).
- Affected code: `src/butlers/api/routers/decisions.py`,
  `src/butlers/api/models/decision.py`, `frontend/src/pages/DecisionsPage.tsx`,
  `frontend/src/components/decisions/`, `frontend/src/hooks/use-decisions.ts`,
  nav/router registration.
- No database migration; no change to `decision_review.py`'s existing
  digest/escalation/notify behavior (bu-ckkpz.4 is untouched).
- Follow-ups tracked separately: bu-97qrw (switch detection off the title
  heuristic once bu-ckkpz.1 ships real fields), bu-ckkpz.3 (attention-ledger
  routing + one-tap close, which will add real actions to this page).

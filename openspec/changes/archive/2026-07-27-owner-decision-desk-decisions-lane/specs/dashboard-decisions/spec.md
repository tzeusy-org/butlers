# Dashboard Decisions Lane — Delta

New capability. Defines the owner-facing `/decisions` page: the dashboard's
surface for the open decision-bead digest bu-ckkpz.4 already computes
server-side. Before this capability existed, the owner-decision queue was
invisible anywhere in the dashboard (`grep OWNER|DECISION frontend/src/pages`
returned zero hits) -- an owner had to already know to check Telegram or `bd`
directly. This is slice 2 of epic bu-ckkpz ("Owner Decision Desk"); it is
deliberately read-only because its summary API does not expose per-decision
options/defaults or mutations, even though decision classification is already
label-based.

## ADDED Requirements

### Requirement: Decisions Page Route and Navigation

The dashboard SHALL expose a routed page at `/decisions`, registered in the
sidebar "Main" section with a dedicated icon and a badge counting currently
open decisions (`GET /api/decisions`'s `data.length`, or `0` when
`meta.decisions_available` is `false` -- the badge has no degraded
affordance of its own).

#### Scenario: Decisions page is reachable from the sidebar

- **WHEN** the owner opens the sidebar
- **THEN** a "Decisions" nav entry is visible under "Main", linking to `/decisions`
- **AND** its badge shows the count of currently open decision beads when greater than zero

### Requirement: Decisions Verdict Opener

The `/decisions` page SHALL render a verdict-opener line, composed from the
`GET /api/decisions` digest via the shared `DispatchVerdict` primitive
(the same primitive `dashboard-approvals`'s opener uses), using this exact
vocabulary (matching `decision_review.py`'s own weekly digest message):
`"N decision(s) waiting, oldest Xd"`, singular/plural on `N = 1`. When one or
more open decisions are escalated (blocking a P1 bug or a deploy for more
than 48 hours), a second clause SHALL name the escalated count: `"N blocking
a P1 bug or deploy"`.

When `meta.decisions_available` is `false`, the opener SHALL render a clause
naming the digest as unavailable and MUST NOT render the calm all-clear line,
even though the underlying `data` array is empty (degraded-envelope
convention). When the query itself errors (network/5xx), the opener SHALL
likewise suppress the all-clear and name the source as unavailable.

#### Scenario: All-clear

- **WHEN** the digest is available and contains zero open decisions
- **THEN** the opener renders "No decisions waiting." and nothing else

#### Scenario: Waiting with escalation

- **WHEN** the digest contains 3 open decisions, oldest 10 days old, one of which is escalated
- **THEN** the opener renders "3 decisions waiting, oldest 10d" followed by "1 blocking a P1 bug or deploy"

#### Scenario: Degraded digest suppresses the all-clear

- **WHEN** `meta.decisions_available` is `false`
- **THEN** the opener names the digest as unavailable
- **AND** does not render "No decisions waiting."

### Requirement: Decisions Row List with Keyboard Triage

The `/decisions` page SHALL render the open decisions as a rule-separated row
list, each row showing the bead id, title, priority (when set), age, and an
"escalated" indicator when applicable. The list SHALL support j/k roving
selection via the shared `useListTriage` hook (the same hook
`dashboard-approvals`, `dashboard-domain-pages` (Issues/Notifications) use),
publishing its bindings to the page's footer hint strip and the app-wide '?'
help sheet.

The currently-selected row SHALL be a door: it expands inline to show every
field the digest carries about that decision (created-at timestamp, and,
when escalated, the blocked bead's id/title/kind/duration). A non-escalated
row's expanded detail SHALL state plainly that this read-only summary does not
expose structured options or actions, rather than fabricating action
affordances that do not yet exist.

A degraded digest (`meta.decisions_available: false`) SHALL render a named
degraded note instead of the calm "No decisions waiting." empty state.

#### Scenario: Selecting a row reveals its detail

- **WHEN** the owner presses `j` to select the first row
- **THEN** that row's detail panel renders inline, showing its created-at timestamp
- **AND** moving selection to another row (via `j`/`k` or a click) closes the previous row's panel and opens the newly-selected one

#### Scenario: Escalated row detail names the blocker

- **WHEN** the selected row is escalated
- **THEN** its detail panel names the blocked bead's id, title, kind (P1 bug or deploy), and how long it has been blocked

#### Scenario: No fabricated action affordances

- **WHEN** the selected row is not escalated (or in general, on any row)
- **THEN** the page renders no approve/deny/close button, because this lane is a
  read-only summary with no option payload or close mutation

#### Scenario: Degraded digest names itself instead of a calm empty state

- **WHEN** `meta.decisions_available` is `false`
- **THEN** the row-list area renders a named "Decisions: <reason>" note instead of "No decisions waiting."

### Requirement: Export As-Of Plaque

The `/decisions` page SHALL render a plaque naming the beads export's age next
to the verdict opener whenever `GET /api/decisions`'s `meta.export_as_of` is
known, regardless of whether `decisions_available` is `true` or `false` --
the underlying single-file bind-mount tolerates up to 14 days of staleness
before `decisions_available` flips off, so a slowly-aging-but-still-available
export MUST NOT render as calm current data. Past a shorter warning
threshold (well before the 14-day cliff), the plaque SHALL switch from a
muted to a warning-tinted style so staleness gets a visible tell before the
digest goes fully unavailable. The plaque SHALL be omitted (not rendered)
only when `meta.export_as_of` itself is absent (the export was never
reached, e.g. `export_missing`).

#### Scenario: Recent export renders a muted plaque

- **WHEN** `meta.export_as_of` is within the warning threshold of now
- **THEN** the page renders an "export as of <age> ago" plaque in a muted style

#### Scenario: Aging export renders a warning-tinted plaque

- **WHEN** `meta.export_as_of` is older than the warning threshold but the
  digest is still `decisions_available: true`
- **THEN** the plaque renders in a warning tint, distinct from the muted style

#### Scenario: Plaque persists alongside the degraded note

- **WHEN** `decisions_available` is `false` (e.g. `export_stale`) but
  `meta.export_as_of` is known
- **THEN** the plaque still renders alongside the degraded-source note

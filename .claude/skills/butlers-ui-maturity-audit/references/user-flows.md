# User Flows — derivation, rubric, and living catalog

Load in **Phase 0** to turn page surfaces into the user flows the audit actually walks.

## Why flows, not pages

A page-by-page control sweep finds dead `onClick`s — the rarest and least-deceptive failure — and
misses everything that spans pages (connect-an-account crosses Secrets + an OAuth callback + a
toast on return; triage-an-event crosses a list, a drawer, and a dispatch action). A flow is a
*goal a user holds*, and the audit's job is to confirm that goal is reachable, pleasant, honest,
and backed end to end.

## Deriving flows from project shape (do not invent goals)

A flow must trace to what the product is *for*, not to what would be neat to test.

1. **What is the surface for?** Read the relevant manifesto / doctrine — invoke `heart-and-soul`
   for the butler or capability, and `lay-and-land` for where it lives. The manifesto states the
   value proposition; each value proposition implies one or more user goals.
2. **What was promised?** Read the redesign brief (`docs/redesigns/*.md`) and the openspec
   change/spec for the surface. A redesign's "design intent" section is a list of intended user
   experiences — each is a candidate flow.
3. **What does the route graph allow?** From `scripts/scope_surfaces.py`, see which surfaces
   connect; a flow usually threads several routes + a backend action + a return state.

Write each flow as a one-line user goal in the user's voice, then its happy-path steps:

> **Flow:** "As the owner, I connect my Google account so my calendar butler can see my events."
> Steps: open Secrets → click Connect Google → consent screen → callback → see account listed with
> granted scopes → (later) widen scopes / see expiry / disconnect-and-recover.

## The experiential rubric (apply at every step, not just the entry point)

For the flow as a whole and for each step:

1. **Pleasant experience** — define the happy-path for this goal from project shape. What would
   "this just works" feel like? What does the user expect to see/feel after each action?
2. **What went wrong** — where does the implementation diverge? Be specific: which step, which
   control, what the user sees instead.
3. **Maturity of every UX element** — at each step: loading state, empty state, error state,
   degraded state (remember the `aggregates_available:false` envelope is *not* an error — show
   "metrics unavailable"), action feedback (does a mutation toast success/failure?), sensible
   defaults, keyboard/focus, and recovery from the trap cases.
4. **Misleading / poorly-designed elements** — a success toast for work that didn't happen; a
   hardcoded status dressed as live; a control that looks editable but is read-only; a "Live"
   badge driven by a timer; a filter/search that silently drops results. Rank these high:
   misleading beats missing for trust damage.
5. **Comprehensive backend support** — does the *whole* happy-path have real, consumed, non-stub
   backing — including the unhappy branches the user *will* hit: revoked/expired token, empty
   result set, partial failure, permission denied, concurrent edit? A flow that works only on the
   pristine path is not mature.

## Living flow catalog (maintenance contract — read this first)

This catalog is **living state**, not static guidance: the dashboard's flows change as surfaces
ship and redesigns land. When an audit run derives a flow that is not listed below, **append it in
the same run** — name it, give the user-goal sentence and the surfaces it spans. When a flow is
removed from the product, drop its entry one revision after you confirm it's gone (note the
evidence). Keep entries terse; the per-run findings live in the report, not here.

Format: `Flow name` — user goal — surfaces spanned — last audited (YYYY-MM or "not yet").

### Catalog

- **Connect & manage an account/auth** — "connect Google, see/widen scopes, handle expiry,
  disconnect and recover" — Secrets passport + OAuth start/callback + per-account status — 2026-06.
  *Exemplar flow; richest signal came from walking it end to end.*
- **Triage an ingested event** — "see what arrived, inspect it, retry/replay or route it" —
  Ingestion timeline + event drawer + dispatch/replay actions — 2026-06.
- **Tune what gets ingested** — "create/edit a filter rule, add a priority sender" — Ingestion
  filters pipeline + rule CRUD — 2026-06.
- **Scan the live ingestion ledger** — "open /ingestion, see events newest-first with live
  freshness, KPIs, and a time-range scope" — IngestionTimelinePage header band + range picker +
  hour-grouped ledger — 2026-06.
- **Search/filter/saved-views the ledger** — "narrow the ledger by freetext, channel chips, status
  pills, and saved views (all/errors/priority/spend)" — TimelineTab toolbar + saved-views CRUD —
  2026-06.
- **Bulk replay ingested events** — "select failed/filtered rows and re-dispatch them" — Timeline
  checkbox column + BulkActionBar → /events/retry|replay/bulk — 2026-06.
- **Survey connector roster health** — "see every connector's liveness, state, volume, and what
  needs attention" — IngestionConnectorsPage roster + summary + attention strip + dormant list —
  2026-06.
- **Drill into a connector** — "inspect one connector's recent events, incidents, routing rules,
  scopes, histogram" — ConnectorDetailPage — 2026-06.
- **Reconnect a degraded connector** — "see a broken connector, reauth it, return healthy" —
  roster/detail ReauthCallout + connector-auth derivation + OAuth start/callback — 2026-06.
  *Recovery path was invisible at audit time (callout gated on an unreachable `state==='error'`).*
- **Tune connector batch settings** — "adjust a batch connector's flush window and have it take
  effect" — BatchSettingsCard → /connectors/{id}/settings → connector flush scanner — 2026-06.
  *Card built+backed end-to-end but mounted on no live page at audit time.*
- **Understand what a butler remembers** — "browse memory, follow provenance fact→episode, judge
  confidence/decay, confirm/forget" — Memory ledger + fact/rule/episode detail — 2026-06.
- **Operate a butler** — "force-run, pause/resume, edit its prompt, change its model/permissions"
  — Butler detail + management tab + settings console — 2026-06.
- **Configure the system** — "pick a model, edit a permission, set a spend ceiling/rule" —
  Settings console + model/permissions/spend sub-pages — 2026-06.
- **Investigate a QA case** — "drill into an investigation/patrol, read the dossier, retry/dismiss"
  — QA overview + case index + investigation/patrol detail — 2026-06.
- **Explore people & things** — "search/re-centre on an entity, hop the graph, review curation
  (unidentified/duplicate/stale), merge/forget" — Entity index + workbench + hop/columns/
  concentration + finder — 2026-06.

Re-derive and re-cluster each run; this is a starting set, not a fixed contract.

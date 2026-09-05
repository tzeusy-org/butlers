# Fleet case file

A fleet case is the durable object for one situation, not one cycle. The
Switchboard insight broker correlates multi-butler candidate clusters, but a
cluster on its own is cycle-scoped — recomputed and discarded every delivery
cycle, so a situation spanning several days (a multi-day illness, a slowly
worsening problem) is invisible to it.

Butlers records a recognized situation in `public.fleet_cases`:

- **state**: `open | watching | closing | closed`. Only `closed` is terminal.
- **posture**: `silent | routine | active | urgent`. The Switchboard is the
  sole arbiter of a case's posture.
- **correlation_key**: a readable key identifying the situation (for example
  `health:owner:respiratory-illness`), not an opaque id.
- **outcome**: required exactly when `state = 'closed'`. A lapse sweep closes
  a case by writing `outcome = 'lapsed'` — that is a value of `outcome`, not a
  fifth state.

At most one non-closed case may exist per `correlation_key` — a partial
unique index is the DB-level backstop.

`public.fleet_case_evidence` records one contribution per contributor per
case; `UNIQUE(case_id, contributor, kind, ref)` makes re-reporting the same
evidence a no-op rather than a duplicate row. Any butler role may contribute
evidence. `public.fleet_case_links` binds a case to an entry in another
ledger (an insight candidate, an owner condition, another case) by
`(case_id, link_kind, ref)`.

Only `butler_switchboard_rw` may create or update a case or its links — a
case's existence, state, posture, and ledger bindings are Switchboard-
arbitrated, enforced by row-level security rather than by GRANT/REVOKE alone
(see the RLS note in RFC 0032).

This schema is Slice 1 of a seven-slice rollout — see RFC 0032 for the full
design and slice plan. No broker wiring, MCP tools, or dashboard surface
exist yet.

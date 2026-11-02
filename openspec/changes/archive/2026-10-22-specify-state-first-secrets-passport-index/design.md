## Context

See `proposal.md` for motivation. This document is the field-by-field contract a reviewer and a
future implementer can both check the code against, and the record of why each judgment call landed
where it did. `bu-k87os` gave the top-level shape directly (five groups, this exact order, and the
state-to-group mapping down to the state names), and the acceptance criteria (`bu-k87os` AC1-AC5)
name the remaining questions this document resolves: within-group determinism, the Recency control,
legacy `?sort=` compatibility, and a content-blind method for checking the mapping against real
inventory data.

The governing artifacts are `openspec/specs/butler-secrets/spec.md` (the Passport-Book IA and sort
controls), `frontend/src/components/secrets/passport/spine-builder.ts` /
`frontend/src/components/secrets/passport/Spine.tsx` (current implementation being amended, read in
full before drafting this delta), and `frontend/src/components/secrets/passport/constants.ts` /
`types.ts` (the current `CredentialState` union and `STATE_CATALOG`, which this delta's mapping was
checked against directly rather than re-derived from memory — all eleven current states are
accounted for exactly once).

## Goals / Non-Goals

**Goals:**

- Replace the family-first spine order with the owner-specified five state-first groups and map
  every current `CredentialState` value to exactly one of them.
- Make within-group ordering fully deterministic without any new recency/activity data.
- Resolve the Recency control and legacy `?sort=recency` URLs explicitly, per `bu-k87os` AC3.
- Define scenarios for every interaction the state-first regrouping touches: mixed families,
  transient states, search, keyboard order, identity switching, and accessibility now that group
  headings no longer name a family.
- Define a real-inventory comparison method that verifies this spec's claims against live data
  without violating the surface's content-blind, no-persistence posture.

**Non-Goals** (verbatim from `bu-k87os`):

- No new last-used/activity field, telemetry, or inventory persistence.
- No secret access, API expansion, frontend implementation, deployment, PR, or merge in this bead.

## Decisions

### Five state-first groups, `rotating` reclassified, `authorization_needed` named explicitly

The mapping is exactly the one `bu-k87os` specified:

| Group | States |
|---|---|
| `needs-hand` | `expired`, `revoked`, `scope_mismatch`, `expiring`, `authorization_needed`, `failed` |
| `in-progress` | `checking`, `rotating` |
| `stale` | `warn` |
| `ready` | `ok` |
| `not-set` | `never_set` |

This is exhaustive over `frontend/src/components/secrets/passport/types.ts`'s `CredentialState`
union (verified directly against the file, not re-derived): all eleven current values appear above
exactly once.

Two corrections against today's code fall out of this table:

- `rotating` moves from `NEEDS_HAND_STATES` (`constants.ts`) to `in-progress`. An in-flight
  rotation the owner initiated is a transient operation, not a broken credential — the same
  reasoning that already kept `checking` out of `needs-hand`. Its tone also changes from today's
  amber-no-sliver (an inconsistent middle state — alarm color without an alarm sliver) to
  `--dim`/no-sliver, matching `checking`'s existing quiet treatment (bu-976n0 precedent: a
  transient, self-clearing state is not alarm-colored).
- `authorization_needed` was already a `needs-hand` member in code (`NEEDS_HAND_STATES` includes
  it) but the old spec scenario's exact-state list never named it. This delta closes that
  spec/code gap by naming it explicitly rather than silently inheriting the omission.

Alternative considered: keep `rotating` in `needs-hand` since it was there in code already.
Rejected — "in progress" is exactly what `bu-k87os`'s group name describes, `rotating` is
definitionally in-flight and self-resolving, and leaving it in `needs-hand` would put a transient
state in the pinned act-now bucket next to genuinely broken credentials, diluting that bucket's
"the owner must act" signal.

### Within-group ordering: severity always first, `?sort=` controls only the tie-break after it

`bu-k87os` says "within groups use severity where applicable and deterministic cross-family label
order" without spelling out the tie-break chain. The old spec already stated a stronger,
easy-to-lose invariant for one group: `needs-hand` "is always pinned and severity-sorted regardless
of the `?sort=` mode." Dropping that qualifier while introducing a two-mode sort picker would have
let `?sort=alpha` interleave a `revoked` row after a `scope_mismatch` row inside `needs-hand` by
label alone — silently weakening an existing safety property. This delta instead generalizes the
invariant to every group and folds the sort picker in underneath it: severity rank (the existing
`STATE_CATALOG` rank, ascending) is always the primary key, unconditionally, in every group and
under both sort modes; `?sort=` selects only the tie-break applied to same-severity rows —
`severity` mode uses (family rank, label, focus key), `alpha` mode uses (label, focus key), skipping
family rank entirely so `alpha` has a real, visible effect (pure alphabetical browsing within a
severity band) without ever promoting a healthier row above a sicker one.

Severity differentiates rows in `needs-hand` (six member states span four distinct ranks) and
`in-progress` (`rotating` rank 4, `checking` rank 5); it is a no-op in `stale`/`ready`/`not-set`,
each a single state, where the mode-selected tie-break takes over immediately. One rule covers every
group without a per-group special case.

The family rank `cli` < `system` < `user` (used only under `severity` mode) matches
`buildSpineEntries`'s existing `[...cli, ...system, ...user]` construction order and the existing
group render order in `Spine.tsx` — reusing it is the smaller, more reversible change (tie-break
4/5 in `decision-autonomy.md`: less churn, matches an established convention) versus inventing a
new family precedence with no signal behind it either way.

Alternative considered: let `?sort=alpha` override severity entirely within a group (label as the
primary key). Rejected — this is the exact regression named above: it would let `alpha` mode
visibly reorder `needs-hand` by label, contradicting the prior spec's explicit invariant and
letting a cosmetic sort preference outrank the act-now signal the group exists to carry.

Alternative considered: alphabetical family order (`cli`, `system`, `user` — coincidentally
identical here) instead of the existing-convention order. Rejected as a *reason*, not a result: an
alphabetical rule would silently reorder if a fourth family is ever added in an order the product
doesn't intend, where the explicit existing-convention rule stays stable by design.

### Recency is removed, not canonicalized

`bu-k87os`: "Remove or canonicalize the misleading synthetic Recency control without adding
activity telemetry." This delta removes it.

`lastTouchOrder` (`spine-builder.ts`) is not a last-used signal: `user` rows always carry the fixed
constant `800` (nothing has ever tracked per-credential usage time — `bu-hd1vs`, `bu-5r9hy`);
`system` and `cli` rows carry a raw array index or another fixed constant. Renaming the sort mode
while keeping the same backing value would still present array position as if it meant something
about time — the exact "misleading" property `bu-k87os` names, just relabeled. Removing the control
is the only option that resolves the problem without adding real activity data, which the non-goals
forbid.

Alternative considered: rename `recency` to something honest like "list order" and keep the same
`lastTouchOrder` values. Rejected — a `sort · list order` control still implies the position means
something to the owner; there is nothing meaningful backing it, so the honest fix is not to offer
it. This also directly resolves the question `bu-5r9hy` left open (should user rows rank by index
or a fixed constant?) by removing the dimension the question was about.

### Legacy `?sort=recency` falls back to `severity`, without a forced URL rewrite

An old bookmark or shared link carrying `?sort=recency` must not error or blank the page.
`DirectionPassport.tsx`'s existing pattern for a stale legacy focus-key prefix (`s:cli-auth/...` →
canonicalized to `c:cli-auth/...` in memory, without rewriting the URL) is the model: this delta
applies the same shape — an unrecognized `?sort=` value (not just `recency` specifically) renders as
`severity`, and the URL is left as the owner navigated to it.

Alternative considered: rewrite the URL to strip or replace `sort=recency` on load. Rejected —
forcibly mutating history on every legacy link visit is a bigger, less reversible behavior change
than resolving the value in memory, and the existing focus-key precedent already established
in-memory canonicalization as this page's pattern for a retired URL shape.

### Real-inventory comparison method: family / state / published label / ephemeral token only

`bu-k87os` AC5 requires a "non-degraded real-inventory comparison method [that] retains only
family/state/published label/ephemeral row token and persists no values." This delta defines that
method's record shape directly: `family` (`user`/`system`/`cli`), `state` (the `CredentialState`
value), `publishedLabel` (the row's already-public display label — the provider's label for `user`
rows, the raw `key` for `system`/`cli` rows, both already published per the `Evidence-Over-Value`
and `dashboard-api` inventory contracts), and `ephemeralRowToken` (generated fresh per comparison
run, used only to correlate an expected row against an actual row within that one run, then
discarded).

Excluding fingerprint, probe messages, audit notes, and scope/capability data is deliberate even
though some of those (e.g. the fingerprint) are already non-reversible on the wire: the method's own
record is scoped to exactly what proves the group-mapping and ordering claims, nothing broader, so
its footprint cannot grow by accretion as new inventory fields are added later. "Non-degraded" scopes
the method to a healthy `meta.sources_degraded`-empty response so an absent family is read as a
comparison precondition failure, never as evidence this spec's mapping is wrong.

Alternative considered: let the comparison method read the full inventory row and simply not log
it. Rejected — a method whose *contract* only names what it doesn't log still leaves it possible to
widen what's read informally over time; naming the exact four retained fields makes the boundary
checkable rather than a matter of discipline.

## Risks / Trade-offs

- [`check_spec_overwrites.py` flags two clause losses in the `Spine grouping order` scenario] →
  Both are moves, not deletions: the old five-state `needs-hand` list and the "always... severity-
  sorted regardless of the `?sort=` mode" clause are superseded by the six-state list and the
  generalized severity-first invariant now stated in the same requirement's "Every CredentialState
  maps to exactly one group" scenario and the rewritten "Spine grouping order" scenario itself; the
  old `stale`/`warn` clause's "an unknown, not a failure... MUST NOT appear in `needs-hand`"
  rationale is carried forward verbatim into "Every CredentialState maps to exactly one group." The
  checker matches same-named scenarios only (`check_spec_overwrites.py:379-388`), so content moved
  to a differently-named scenario within the same requirement block reads as a loss even though
  `openspec archive` would still write the whole requirement, substance included. Frozen via
  `--update-baseline` after manually confirming both losses are relocations, not deletions — the
  same documented pattern `amend-secrets-inventory-label-minimization` used for its own frozen loss.

- [A future reader could assume "in progress" is a low-urgency bucket that never needs attention,
  and miss a `rotating` credential that stalls] → `rotating`/`checking` are named explicitly as
  transient and self-resolving in the new "Transient states render quietly in `in-progress`"
  scenario; a stalled rotation that never resolves is a different failure mode (a timeout or stuck
  operation) outside this spec's scope, matching how `checking` already worked before this delta.
- [Merging family-labeled group headings into state-labeled ones could regress screen-reader
  usability, since family used to be conveyed by the group heading alone] → Named explicitly and
  resolved by the new "Row family is accessible without a family-labeled group heading" scenario,
  requiring a per-row accessible family qualifier.
- [A reviewer could read the within-group tie-break chain as over-specified for a UI detail] → The
  chain exists because `bu-k87os` AC2 requires determinism explicitly; an underspecified tie-break
  is exactly the kind of ambiguity that produces flaky visual-regression snapshots and inconsistent
  keyboard-navigation order across renders.

## Migration Plan

1. Obtain exact owner adoption of this draft (`bu-k87os` acceptance criterion 7) before any
   frontend change.
2. Implement `frontend/src/components/secrets/passport/constants.ts`: reclassify `rotating` out of
   `NEEDS_HAND_STATES` into a new `IN_PROGRESS_STATES` set (`checking`, `rotating`), add matching
   `STALE_STATES` / `READY_STATES` / `NOT_SET_STATES` (or an equivalent single state→group map) and
   an `isInProgress()` helper alongside the existing `needsHand()` / `isUnverified()`; adjust
   `STATE_CATALOG.rotating.tone` to `"dim"` / `sliver: false`.
3. Implement `frontend/src/components/secrets/passport/spine-builder.ts`: remove `lastTouchOrder`
   from `SpineEntry` construction; implement the four-key tie-break comparator (severity rank,
   family rank, label, focus key) as the shared within-group sort.
4. Implement `frontend/src/components/secrets/passport/types.ts`: narrow `SpineSortMode` to
   `"severity" | "alpha"`; remove `lastTouchOrder` from `SpineEntry`.
5. Implement `frontend/src/components/secrets/passport/Spine.tsx`: replace the three-group render
   (`needsHandGroup`, `staleGroup`, `restCli`/`restSys`/`restUsr`) with five groups built from the
   new state→group map; remove the `recency` entry from `SORTERS` and `SortPicker`'s options; add
   the per-row accessible family qualifier.
6. Implement `frontend/src/components/secrets/passport/DirectionPassport.tsx`: fall back an
   unrecognized `?sort=` value (including legacy `recency`) to `"severity"`.
7. Update the passport test suite (`Spine.roving.test.tsx`, `secrets-fe5.test.tsx` and its snapshot,
   `spine-duplicate-key.test.tsx`, and any test asserting the old three-family group render) to the
   new five-group shape; add tests for the tie-break chain, the legacy `?sort=recency` fallback, and
   the accessible family qualifier.
8. Deploy through the normal merge queue after exact-head review. No data migration, backfill, or
   runtime config change is needed — this is a frontend rendering/sort-logic change only.
9. Rollback is a code revert; no persisted data is touched (no field in this delta is ever
   persisted, per the real-inventory comparison method's own contract).
10. File a new bead for this implementation step; do not fold it into `bu-k87os`, which is
    spec-only per its acceptance criterion 7.

## Open Questions

None. Widening this to a sixth group, changing the five-group order the owner specified, or adding
a real activity/last-used signal would each be a separate owner decision and a separate proposal.

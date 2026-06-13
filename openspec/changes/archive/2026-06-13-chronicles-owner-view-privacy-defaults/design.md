## Context

The dashboard surface is single-tenant: there is exactly one viewer, the
owner of the Butlers instance. The Chronicles page reads from
Chronicler-owned API endpoints that are mounted behind the dashboard's
existing session/cookie auth boundary. There is no shared-link, no
screenshot-publish flow, and no third-party viewer plumbing today.

The existing `Map Render Privacy Contract` in
`openspec/specs/dashboard-chronicles/spec.md` L144–169 was specced before
the doctrine cross-check happened; it baked in the assumption that
some sources would arrive with `privacy=sensitive` and the frontend
would mask the envelope and exclude coordinates from the map. The only
source that ever did so was `owntracks.points`, and the resulting masking
hit only the owner's own location data.

[Observed] `about/heart-and-soul/security.md` L168–185 is unambiguous:
location data "is governed by the same trust model as all other data" and
"the system does not apply differential privacy, anonymization, or
special-purpose encryption to any data category." Connector-level controls
(retention, opt-in) are the privacy mechanism; render-time masking is not.

The shipped fix flipped `OwnTracksPointAdapter` defaults from `SENSITIVE`
to `NORMAL` and added `core_086` to backfill existing rows. The remaining
spec work is to (a) refine the existing requirement so it stops implying
owntracks-specific behaviour, and (b) codify a forward-looking requirement
for the per-recipient masking toggle so the render-time machinery has a
spec-grounded trigger when (if) shared-viewer flows ever land.

## Goals / Non-Goals

**Goals:**

- Document the owner-view classification doctrine in
  `dashboard-chronicles/spec.md` so the privacy contract matches what's
  actually in `OwnTracksPointAdapter` and `core_086`.
- Specify a `Per-Recipient Masking Toggle` requirement that re-engages the
  render-time masking machinery for non-owner viewers, with clear
  fail-safe defaults and explicit out-of-scope items.
- Keep this change spec-only — no new code, no new tests, no migration.

**Non-Goals:**

- Implementing the per-recipient masking toggle. That is a downstream
  change with its own beads work; only the requirement is specced here.
- Designing the viewer-context plumbing (session role, share-link tokens,
  screenshot-mode flag). The toggle requirement names that plumbing
  exists, but its shape is left to the implementation change.
- Re-litigating the owner-view doctrine. It is established in
  `about/heart-and-soul/security.md`; this change cites it.
- Touching Spotify, Google Calendar, Home Assistant, or any other adapter.
  Their privacy defaults are already `normal` post-`core_085`.

## Decisions

### D1: Refine the existing requirement instead of replacing it.

The `Map Render Privacy Contract` requirement still has load-bearing
scenarios: `Restricted episodes excluded entirely`, `Tombstoned data
excluded by default`, and `Retention enforcement is upstream`. Only the
`Sensitive episodes masked` scenario needs reframing. The cleanest path
is a delta spec that uses `## MODIFIED Requirements` for the Map Render
Privacy Contract, reproduces the surrounding scenarios verbatim, and
reframes only the `Sensitive episodes masked` scenario to drop the
implied owntracks-default assumption.

**Alternative considered:** removing the `sensitive` tier from the spec
entirely, since no in-tree adapter emits it. **Rejected**: the tier is
load-bearing for the future per-recipient toggle (the masking machinery
needs *something* to render against), and other future sources may
legitimately classify rows as `sensitive` (e.g. a future adapter that
ingests another person's calendar shared with the owner).

### D2: New requirement is purely declarative.

`Per-Recipient Masking Toggle` describes WHAT a future implementation
must satisfy (fail-safe-to-mask default for non-owner viewers, explicit
opt-in to expose, audit-friendly toggle state). It does NOT prescribe
the toggle's UI, storage, or session-context plumbing.

**Alternative considered:** specifying a concrete shape (e.g. session
role enum, share-link tokens). **Rejected**: implementation detail
belongs in the downstream change; specifying it now risks locking in
assumptions before the implementing agent does the design work.

### D3: Doctrine reference is normative.

The refined `Sensitive episodes masked` scenario explicitly cites
`about/heart-and-soul/security.md` L168–185 as the controlling rationale.
Per `/project-direction` Rule 1 (specs as source of truth), spec changes
must trace to doctrine; an inline citation makes the trace visible without
forcing readers to chase a separate doctrine doc.

**Alternative considered:** cite only in the proposal, keep the spec
neutral. **Rejected**: future spec readers won't read the proposal
(it gets archived); the rationale must live where the requirement does.

## Risks / Trade-offs

- **[Risk]** Future contributors add a new sensitive source without
  realising the dashboard has no recipient-context plumbing to actually
  show the masking — masked-but-unviewable rows. **Mitigation**: the
  refined scenario explicitly notes that absent the toggle requirement
  being implemented, sensitive rows render as fully-masked envelopes for
  the owner, which is correct fail-safe behaviour.
- **[Trade-off]** The per-recipient toggle requirement is forward-looking
  and may sit unimplemented for a long time. **Mitigation**: it's a
  requirement, not a roadmap commitment — the spec records the contract
  the dashboard MUST satisfy *if* it ever gains non-owner viewers, and
  is otherwise a no-op.
- **[Risk]** Documenting a shipped change retroactively normalizes
  spec-after-code violations of `/project-direction` Rule 2. **Mitigation**:
  the proposal explicitly flags the violation and names the doctrine
  misalignment that justified the immediate fix; this is not a precedent
  for future spec-skipping.

## Migration Plan

None. The shipped code change (commit `f05b7d6c`) already includes the
adapter change and the idempotent `core_086` backfill. This OpenSpec
change is documentation-only.

Rollback: revert this OpenSpec change if the spec text proves wrong;
the underlying code change is independent and has its own revert path.

## Open Questions

- **[Unknown]** Should the `Per-Recipient Masking Toggle` requirement
  default to "mask for everyone except owner" (fail-safe-closed) or
  "render for everyone, mask only when toggle is on" (fail-safe-open
  for the owner-only world we live in today)? Resolved as fail-safe-closed
  in the spec text below; flagged for review.
- **[Unknown]** Does any non-Chronicles dashboard surface (Memory,
  Relationship, Telemetry) need a parallel cleanup? Not investigated;
  if so, separate OpenSpec changes per capability.

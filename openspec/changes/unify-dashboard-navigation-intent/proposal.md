## Why

Dashboard navigation currently uses two independent intent hooks. One warms TanStack Query data;
the other warms the lazy route chunk. Callers that wire only one hook pay the remaining cost after
activation, while callers that wire both duplicate timers and event coordination. The shell's
single capability manifest already resolves both resource kinds, but no governing requirement says
that one user intent must use both projections.

This change proposes that missing contract. It is a spec-only prerequisite for `bu-2jtfw.15` and
does not authorize implementation. The pursuit release authorizes this concrete draft for
independent and exact owner review; it does not approve the behavior described here.

## What Changes

- Add a uniquely named `Unified Navigation Intent Warmup` requirement to `dashboard-shell`.
- Define one intent cycle that warms every mapped route resource through one pending timer:
  - pointer dwell warms at 120 ms and a shorter pointer sweep warms nothing;
  - keyboard focus warms immediately;
  - click, Enter, and Space where the control exposes Space activation warm synchronously before
    navigation or activation starts;
  - chunk-only and query-only mappings proceed independently;
  - unmapped destinations remain no-ops;
  - rejection, target change, cancellation, unmount, and repeated signals remain contained.
- Preserve the destination's existing authentication, query key, stale-time, and cache semantics.
  Navigation intent may perform only the same side-effect-free read the destination would perform.
- Specify atomic migration of every current caller to one primitive and removal of the two internal
  predecessor hooks in the same implementation change.

## Proposed Owner Decisions (Unapproved)

The following product and privacy choices require exact owner approval of this artifact before any
implementation:

1. `OWNER-DECISION-NAV-001`: The pointer intent delay is exactly 120 ms. Focus, click, Enter, and
   Space where the control exposes Space activation do not wait for that delay.
2. `OWNER-DECISION-NAV-002`: Keyboard focus or a pointer dwell of at least 120 ms may initiate the
   destination's existing authenticated, side-effect-free read before navigation. The response may
   enter only the existing in-memory TanStack Query cache under the destination's normal query key,
   stale time, and garbage-collection policy. The warmup creates no separate persistent cache,
   credential, authorization path, telemetry payload, URL state, mutation, or provider effect.

Approval must identify the exact commit or artifact digest. Review comments, pursuit release, or
approval of a different revision do not approve these choices.

## Preserved Outcomes and Non-Goals

This slice preserves the original navigation outcome: a deliberate pointer pause on a supported
session destination warms both its chunk and query data, while pointer enter followed by leave
inside the debounce warms neither.

The original non-goals remain unchanged: no timing-token home, elapsed-route-frame work, universal
never-blank or stale-while-revalidate conversion across domain hooks, Dispatch retokening, status
registry, or Attention primitive. This change also adds no route, API, provider operation, server
cache, persisted schema, authentication mechanism, or implementation.

## Impact and Sequencing

- Affected spec: `dashboard-shell`
- Affected code in this draft: none
- Future implementation surfaces: the unified navigation-intent hook; the shell capability
  projections; `RowLink`, `DisclosureRow`, `SessionTable`, `Sidebar`, and `EntityFinder`; removal of
  `use-prefetch-on-intent.ts` and `use-route-chunk-prefetch-on-intent.ts`; focused hook and wiring
  tests.
- Serialization: implementation remains behind terminal PR #4042 and `bu-8cdl1.13`, followed by a
  fresh overlap/readiness scan.
- Adoption gate: independent exact-head semantic review and exact owner approval remain required.
  This change must stay draft and must not be merged or archived before those gates pass.

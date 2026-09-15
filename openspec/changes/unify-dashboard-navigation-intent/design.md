## Context

The shell capability manifest is already the common route authority. Each capability has a lazy
page loader and may have a query warmup that supplies the exact query key, function, and stale time
consumed by the destination. Thin projections expose those two resources through
`route-chunk-registry.ts` and `prefetch-registry.ts`.

Intent orchestration is still split. `usePrefetchOnIntent` owns query warmup and
`useRouteChunkPrefetchOnIntent` owns chunk warmup. `Sidebar` composes both sets of handlers and
therefore owns two timers per destination. `EntityFinder` combines one hook with a direct chunk
lookup. `RowLink`, `DisclosureRow`, and `SessionTable` use only the query hook, so a supported
destination can warm its data without warming its code.

No baseline requirement or active delta currently defines combined route-chunk and query warmup.
This design proposes that authority without modifying code.

## Goals / Non-Goals

**Goals:**

- Make one navigation-intent primitive responsible for every available warmable resource of a
  destination.
- Keep pointer sweeps cheap while keyboard and activation paths remain immediate.
- Preserve partial mappings, no-op destinations, failure containment, native cache deduplication,
  and current authentication boundaries.
- Give later implementation one behavior-executing hook seam and small caller-wiring tests.
- Remove the two internal predecessor hooks atomically when callers migrate.

**Non-Goals:**

- No timing-token home, route-frame timing instrumentation, universal never-blank or
  stale-while-revalidate contract, Dispatch retokening, status registry, or Attention primitive.
- No new route, API, query mapping, cache policy, authentication path, mutation, provider effect,
  runtime behavior, or implementation in this change.
- No guarantee that every route has query data to warm. The capability manifest remains partial by
  design.
- No cancellation of a network request or module import after warmup has started. Cancellation
  applies to pending intent work only.

## Decisions

### D1: One controller and one pending timer per mounted consumer

The future `useNavIntent(to)` seam owns one timer for the current destination and uses that single
timer to start all mapped resources. Timer state is per mounted hook instance, not global. A global
timer would make intent on one independent control cancel intent on another and would couple
otherwise isolated surfaces.

An intent cycle is bound to one mounted control and one target. It starts with the first pointer
enter, focus, imperative immediate-warm signal, or activation after the preceding cycle has ended.
A focus event joins an existing pointer cycle, and the immediately following click, Enter, or
supported Space activation joins that same cycle. The cycle records which mapped resources it has
started so joined signals do not submit them again.

Before activation, pointer leave or cancellation and blur update the cycle's pointer and focus
presence. The cycle ends when neither remains; any pending timer is cancelled then. Activation ends
the cycle after the caller's navigation or activation callback has been invoked, allowing a later
explicit activation to start a distinct cycle even if the control remains mounted. Target change
and unmount end the cycle immediately. A remount always starts with fresh cycle state. Once warmup
starts, ending a cycle does not abort or evict the underlying module or query request.

### D2: Resolve chunk and query mappings independently at execution time

At the end of the pointer delay, or immediately for focus and activation, the primitive resolves
the latest destination through the shell capability authority. It starts the chunk loader when one
exists and submits the query warmup when both a mapping and the current `QueryClient` exist. Either
resource may proceed without the other. No destination mapping means a complete no-op.

Resolution at execution time avoids warming a stale destination. A target change also cancels the
old timer, and the new target requires its own intent signal rather than inheriting elapsed dwell
from the old target.

### D3: Pointer intent is delayed; keyboard and activation intent are immediate

`OWNER-DECISION-NAV-001` proposes an exact 120 ms pointer dwell. Pointer enter schedules one intent
cycle; pointer leave or pointer cancellation before 120 ms starts neither resource. Keyboard focus
starts or joins the cycle immediately because focus is already deliberate navigation intent.

Click and Enter handlers, plus Space handlers on controls whose existing accessibility contract
exposes Space activation, call `warmNow` before invoking router navigation or the caller's
imperative activation callback. `warmNow` first clears the pending timer, then starts each available
resource not already started in that cycle. This ordering preserves Space behavior and prevents
activation and unmount from cancelling work that should have begun before navigation.

### D4: Warmup stays within existing authorization and cache boundaries

`OWNER-DECISION-NAV-002` proposes that focus or deliberate pointer dwell may initiate the same
authenticated, side-effect-free read used by the destination. The primitive does not bypass an
authorization check, mint or refresh credentials, broaden a query, or convert a mutation into a
prefetch. A query that cannot run under the current browser session fails under its ordinary
authorization behavior and does not block navigation.

Query results use the destination's exact existing query key, stale time, and TanStack Query cache
garbage-collection behavior. Warmup does not persist a second copy, write URL state, emit response
content to logs or telemetry, or publish it to the event bus. Route chunks use the browser module
cache. If no `QueryClient` is present, query warmup is unavailable while chunk warmup may still
proceed.

### D5: Contain speculative failures at the intent boundary

A rejected chunk loader or query prefetch is consumed locally. It does not become an unhandled
rejection, toast, error-boundary transition, navigation cancellation, or automatic retry outside
the resource's existing policy. Actual navigation remains the authoritative place to render its
normal loading, authorization, empty, and error states.

The two resources are failure-isolated: rejection of one does not cancel or evict the other.

### D6: Preserve deduplication without a second cache

Within one intent cycle, each mapped resource starts at most once. A scheduled pointer cycle followed
by focus and then click, Enter, or supported Space remains one cycle: focus cancels the timer and
starts the resources, while activation observes their started markers and does not submit them
again. Activation completion, loss of all pointer/focus presence, target change, or unmount resets
the local cycle state, so a later distinct cycle may request warmup again. Across separate consumers
or later cycles, TanStack Query's query key and freshness rules and the browser's module cache remain
the deduplication authorities. The primitive adds no global promise registry or cache with a
competing lifetime.

### D7: Migrate internal callers atomically

The later implementation migrates all current users of either predecessor hook in one change:
`RowLink`, `DisclosureRow`, `SessionTable`, `Sidebar`, and `EntityFinder`. It then removes
`usePrefetchOnIntent`, `useRouteChunkPrefetchOnIntent`, their duplicate delay constants, and their
old focused hook tests. Same-repository consumers give these hooks no public compatibility promise,
so temporary re-export aliases would add ambiguity and preserve the split contract.

One consolidated hook suite owns timing, partial mapping, failure, cancellation, target-change,
auth/provider absence, and dedupe semantics. Caller tests prove event-handler ordering and wiring
without duplicating the hook matrix. A repository import guard or equivalent static check prevents
reintroduction of the removed hook paths.

## Verification Design for the Future Implementation

The behavior-executing seam is the unified hook with fake time, a controlled chunk loader, and a
real or faithfully configured `QueryClient`. It must prove:

- `/sessions/abc` with both mappings invokes its loader and `prefetchQuery` after exactly 120 ms;
- pointer enter followed by leave or cancellation before 120 ms invokes neither;
- focus, click, Enter, and supported Space start both resources immediately, and activation observes
  warmup first;
- chunk-only and query-only destinations start the available resource;
- an unmapped destination and an absent query provider remain safe;
- rejecting either or both resources produces no uncaught rejection and does not suppress the
  other resource;
- unmount or target change cancels pending work; and
- real focus followed by click, Enter, or supported Space invokes each mapped resource once for that
  joined cycle, while blur then refocus or completion followed by a later explicit activation starts
  a distinct cycle without being suppressed.

`RowLink` and `DisclosureRow` tests prove composed handlers retain caller behavior, preserve Space
where those controls expose it, and call warmup before navigation or activation. Those caller tests
also exercise focus followed by Enter or Space against both mapped resources, rather than mocking
away the cycle boundary. `SessionTable`, `Sidebar`, and `EntityFinder` tests prove each uses the
unified seam. Frontend lint, typecheck/build, `knip`, focused Vitest coverage, and terminal hosted
frontend CI validate the exact implementation head.

## Risks / Trade-offs

- Focus and deliberate hover can issue an authenticated read even if navigation never follows.
  This is intentional only if the owner approves `OWNER-DECISION-NAV-002` exactly.
- A fixed 120 ms delay may later prove too eager or too slow. Changing it is a product behavior
  change until a timing-token contract exists; this proposal deliberately does not create that
  token home.
- Atomic removal can break an overlooked internal import. Repository-wide import search, `knip`,
  typecheck/build, and hosted CI make that failure visible in the implementation change.
- Resource-native dedupe does not promise that resolver functions are called only once across the
  whole application. It promises no duplicate resource work within one intent cycle and preserves
  the existing cache authorities across cycles.

## Rollback

The future implementation has no persisted migration. Before adoption, rollback is deletion of this
draft. After implementation, rollback restores the predecessor hooks and caller wiring in one code
revert. Existing query and browser module caches remain valid because the unified primitive does not
change their keys or formats.

## Open Questions

The engineering choices are resolved above. Adoption remains blocked only on independent exact-head
semantic review and exact owner approval of this artifact, including both proposed owner decisions.

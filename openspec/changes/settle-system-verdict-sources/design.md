## Context

`SystemVerdictBanner` already delegates its source gate to `DispatchVerdict`:
any loading source renders an unsettled skeleton, and any settled error adds a
named unavailable clause. The System page already fetches database and egress
facts for its tiles, but the banner does not invoke either hook, so neither can
participate in that gate. `useEgressFacts()` intentionally exposes HTTP 403 as
the real `isForbidden` flag while retaining the query error state.

## Goals / Non-Goals

**Goals:**

- Include database and egress hook state in the existing verdict source gate.
- Prevent database and ordinary egress failures from producing an all-clear.
- Preserve the existing owner-only egress 403 interpretation as settled,
  limited visibility rather than a service failure.
- Pin each state transition with focused render tests and specification
  scenarios.

**Non-Goals:**

- Adding endpoints, persistence, retry behavior, or database-size health
  rules.
- Treating egress activity or an empty egress catalog as an alarm.
- Changing authorization semantics or broadening error-honesty changes beyond
  the System verdict.

## Decisions

### Use the hooks' actual query flags in the existing source list

The banner will call `useDatabaseFacts()` and `useEgressFacts()` directly and
append their existing `isLoading` / `isError` state to `DispatchVerdict`'s
`sources` list. The egress entry will use
`isError: egress.isError && !egress.isForbidden`.

This keeps the loading and error behavior in the established primitive rather
than adding another status abstraction. A wrapper that normalizes source state
is rejected because it can drift from the real hook contract and would expand
the change without adding behavior.

### Classify the expected egress 403 before the verdict primitive sees it

`isForbidden` is the hook's explicit signal that the error is an expected
owner-only visibility boundary. The source remains settled, but not failed;
therefore it contributes neither an unavailable clause nor a false loading
state. If every other verdict input is healthy, the banner preserves its calm
all-clear state. The egress tile remains responsible for its owner-only detail.

Inspecting actor count, catalog coverage, or database-size fields is rejected:
those are facts rendered by their tiles, not health predicates for this
verdict.

## Risks / Trade-offs

- **[Risk] Hook flags change shape** → focused tests set and assert the real
  `isLoading`, `isError`, and `isForbidden` flags instead of introducing a
  made-up status enum.
- **[Risk] A 403 is presented as a fault** → gate egress errors on
  `!isForbidden` and cover the all-clear/non-unavailable result explicitly.
- **[Risk] A source is visible only as a tile and omitted from the verdict** →
  list it explicitly in the banner's `sources` array so `DispatchVerdict`
  supplies the settled-source invariant.

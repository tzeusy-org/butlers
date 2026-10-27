## Context

`write_day_close_cache` already performs deterministic prose admission. An
invalid candidate is either contained behind an active admissible row or stored
as an audit-only invalid row. The refresh route currently cannot observe that
admission decision and consequently reports the active row's timestamp as an
ordinary successful refresh in both cases.

## Goals / Non-Goals

**Goals:**

- Make a refresh-generated invalid candidate machine distinguishable with its
  canonical admission reason.
- Keep candidate prose and provenance behind the writer admission boundary.
- Preserve valid refresh, cache reuse, rate-limit, and error behavior.

**Non-Goals:**

- No new LLM invocation, database migration, scheduler, transport,
  cross-schema, GET-response, or frontend behavior change.
- No change to the writer's cache replacement rule or invalid-row audit
  retention.

## Decisions

### Return a minimal admission outcome from the existing writer

The writer returns an internal outcome containing only `invalid_reason`. The
route uses that outcome after the existing write attempt; it continues to read
the active row only for `cache_built_at`. This preserves a contained
admissible row's timestamp while disclosing the generated candidate's safe,
deterministic disposition.

Returning candidate prose or provenance is rejected because the route must not
weaken deterministic admission. Re-classifying the candidate in the route is
rejected because two independent admission decisions could drift.

### Extend the existing successful response additively

The existing refresh response retains `cache_key` and `cache_built_at` and
adds `invalid` plus nullable `invalid_reason`. Valid refreshes explicitly
return `invalid: false`; invalid candidates return `invalid: true` and the
canonical reason. No response branch has prose or provenance fields.

## Risks / Trade-offs

- [A writer failure may not produce a cache row] → The route retains its
  existing `cache_write_failed` error behavior when no active row is found.
- [The response adds fields] → The addition is backward compatible for
  existing JSON consumers and no frontend caller consumes this endpoint.

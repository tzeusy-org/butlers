## Context

The session-detail page and client already resolve IDs through the global
cross-butler route. Older dashboard links still include `?butler=<name>`, but
the page does not read that query state and therefore uses the same global
lookup for both URL forms.

`GET /api/sessions/aggregate` runs two independent fan-outs when callers opt
into `include_trigger_breakdown`: a scalar aggregate used for counts and a
separate `GROUP BY trigger_source` query used only for attribution. The scalar
path already reports its own pool failures in `meta.sources_degraded`; the
optional query currently throws away its own failed-source list.

## Goals / Non-Goals

**Goals:**

- Make the documented session-detail route and legacy-link behavior match the
  existing global implementation.
- Preserve failed sources from the opt-in trigger breakdown as typed aggregate
  data without changing the scalar aggregate degradation contract.
- Prevent a partial trigger breakdown from supporting a trigger-dominance
  statement in the sessions verdict.
- Keep the editable dashboard data-flow source and its checked-in SVG aligned.

**Non-Goals:**

- Add a new detail endpoint, restore a butler-scoped detail endpoint, or
  rewrite legacy inbound links.
- Change database schema, fan-out mechanics, owner policy, or scalar
  aggregation behavior.
- Redesign the sessions page or broaden degraded-source handling outside this
  optional attribution path.

## Decisions

### Carry trigger-breakdown failures in a dedicated read-model result

The trigger-breakdown read-model function will return a small typed result
containing its merged buckets and its own `degraded_sources` list. The router
will expose that list as the additive
`SessionAggregate.trigger_breakdown_degraded_sources` field when the opt-in
query runs.

This preserves the source provenance of the specific fan-out that produced
`by_trigger_source`. Putting those names in `meta.sources_degraded` was
rejected because that metadata is the existing scalar aggregate contract: a
breakdown-only failure must not imply scalar counts are incomplete.

### Treat trigger attribution as unavailable, not scalar failure counts

The verdict will continue to show the scalar failed-session count. When the
new breakdown-specific field is non-empty, it will name the unavailable
attribution and will not choose a trigger source as the dominant cluster. A
complete per-butler scalar breakdown remains eligible as a fallback cluster;
the existing scalar `meta.sources_degraded` behavior remains independent.

Suppressing the whole verdict was rejected because a breakdown-only failure
does not invalidate the scalar counts. Treating partial buckets as complete
was rejected because it can falsely elevate a surviving trigger source.

### Preserve legacy query links at the boundary

No frontend router or API route will be added for `?butler=`. Documentation
will describe the parameter as tolerated, ignored inbound state: legacy links
remain navigable and call `getSession(id)`, while new links may use the clean
`/sessions/{id}` form.

## Risks / Trade-offs

- [An optional field could be confused with scalar degradation] → Use a
  precise `trigger_breakdown_` prefix, retain the scalar metadata unchanged,
  and cover the two signals independently in API tests.
- [A partial breakdown could still look authoritative] → Render a named
  attribution-unavailable clause and gate trigger selection in the verdict.
- [Documentation could drift from the checked-in graphic] → Update the
  editable Excalidraw source and regenerate the existing SVG in the same
  change.

## Context

Connector-scoped rule rows include enabled and disabled non-archived rules. Filters must read the complete non-archived set and the archived set before concluding that a target is absent. The `rule` query parameter is a navigational focus target, not URL-backed filter state.

## Decisions

- Encode each rule id with `encodeURIComponent` and decode through the router search parameters exactly once. Compare opaque ids by equality; never interpolate them into CSS selectors.
- Require successful complete active and archived reads before resolving the target. A failed reader, including a failed refresh with stale data, produces an unavailable state with retry. Loading does not prove absence.
- Preserve the normal gate classification. A targeted channel-default rule also exposes its exact rule row at its gate, alongside the existing policy aggregate.
- Reveal archived targets without restoring them. Named rows support programmatic focus with visible selection and the Dispatch focus token. Scroll is immediate to respect reduced motion.
- Keep a per-target focus receipt in the mounted Filters view. Repeated URLs, refreshed data and re-expansion do not steal focus after the owner moves it; a different target or removing then adding a target permits one new focus move.

## Scope

This adds navigation only. Existing rule editing and connector operations are untouched. The additive delta extends the baseline Connector Detail and Filters Pipeline requirements and obeys dashboard-design-language Interaction Affordances. It composes with the active restore-ingestion-console-spec-coverage package.

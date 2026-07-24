/**
 * Initial entity-detail projection shared by the page and intent prefetch.
 *
 * Profile facts can be older than a standard page of results, so the detail
 * route starts with a wider slice. Keeping this shape in one place ensures a
 * hover prefetch warms the same TanStack Query entry that the route consumes.
 */
export const ENTITY_DETAIL_INITIAL_FACTS_LIMIT = 200;
export const ENTITY_DETAIL_INITIAL_PARAMS = {
  facts_limit: ENTITY_DETAIL_INITIAL_FACTS_LIMIT,
} as const;

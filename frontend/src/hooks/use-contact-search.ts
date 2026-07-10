import { useQuery } from "@tanstack/react-query";

import { searchContacts } from "@/api/index.ts";
import type { ContactSearchResponse } from "@/api/types.ts";

/**
 * Typeahead search over known person entities (GET /api/contacts/search).
 *
 * The caller is responsible for debouncing `q` (e.g. via `useDebounce`) — this
 * hook only gates the fetch on a non-empty trimmed query so a blank field never
 * hits the network. Errors surface through the returned `isError`/`error` so the
 * consuming picker can render an honest degraded state rather than a silently
 * empty list.
 */
export function useContactSearch(
  q: string,
  options?: { enabled?: boolean; limit?: number },
) {
  const trimmed = q.trim();
  return useQuery<ContactSearchResponse>({
    queryKey: ["contacts-search", { q: trimmed, limit: options?.limit }],
    queryFn: ({ signal }) =>
      searchContacts(trimmed, { limit: options?.limit, signal }),
    enabled: (options?.enabled ?? true) && trimmed.length > 0,
    staleTime: 30_000,
  });
}

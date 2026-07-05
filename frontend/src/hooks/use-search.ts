/**
 * TanStack Query hook for the global search API with debounce.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { searchAll } from "@/api/index.ts";

const DEBOUNCE_MS = 300;
const MIN_QUERY_LENGTH = 2;
const DEFAULT_LIMIT = 20;

/** Debounced global search hook. Only fires when query >= 2 characters. */
export function useSearch(query: string, options?: { limit?: number }) {
  const [debouncedQuery, setDebouncedQuery] = useState(query);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  return useQuery({
    queryKey: ["search", debouncedQuery, options?.limit],
    queryFn: () => searchAll(debouncedQuery, options?.limit ?? DEFAULT_LIMIT),
    enabled: debouncedQuery.length >= MIN_QUERY_LENGTH,
    // Never-blank floor (bu-nhcp5): each debounced keystroke is a new query
    // key, so without this the Sessions/State result groups would flash
    // empty between the debounce settling and the new fetch resolving. Keep
    // the previous keystroke's results visible (dimmed via FetchingDim at the
    // call site) instead.
    placeholderData: (previousData) => previousData,
  });
}

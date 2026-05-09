/**
 * Fetches the chronicles attention list for a single day window.
 *
 * Same data the editorial briefing embeds, exposed standalone for
 * cheaper polling.
 */

import { useQuery } from "@tanstack/react-query";

import { getChroniclesAttention } from "@/api/client.ts";
import type { ChroniclesAttentionItem } from "@/api/types.ts";

const ONE_MINUTE_MS = 60 * 1000;

interface UseChroniclesAttentionArgs {
  date?: string;
  tz?: string;
}

export const chroniclesAttentionKeys = {
  all: ["chronicler", "attention"] as const,
  byDate: (date: string | undefined, tz: string | undefined) =>
    ["chronicler", "attention", date ?? "default", tz ?? "default"] as const,
};

export function useChroniclesAttention(args: UseChroniclesAttentionArgs = {}) {
  const { date, tz } = args;
  return useQuery<{ data: ChroniclesAttentionItem[] }>({
    queryKey: chroniclesAttentionKeys.byDate(date, tz),
    queryFn: () => getChroniclesAttention({ date, tz }),
    staleTime: ONE_MINUTE_MS,
    refetchOnWindowFocus: true,
  });
}

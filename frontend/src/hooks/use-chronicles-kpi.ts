/**
 * Fetches the chronicles KPI snapshot for a single day window.
 *
 * Same numeric block the editorial briefing embeds, exposed standalone
 * for cheaper polling when the rest of the briefing is not needed.
 */

import { useQuery } from "@tanstack/react-query";

import { getChroniclesKpi } from "@/api/client.ts";
import type { ChroniclesKpi } from "@/api/types.ts";

const ONE_MINUTE_MS = 60 * 1000;

interface UseChroniclesKpiArgs {
  date?: string;
  tz?: string;
}

export const chroniclesKpiKeys = {
  all: ["chronicler", "kpi"] as const,
  byDate: (date: string | undefined, tz: string | undefined) =>
    ["chronicler", "kpi", date ?? "default", tz ?? "default"] as const,
};

export function useChroniclesKpi(args: UseChroniclesKpiArgs = {}) {
  const { date, tz } = args;
  return useQuery<{ data: ChroniclesKpi }>({
    queryKey: chroniclesKpiKeys.byDate(date, tz),
    queryFn: () => getChroniclesKpi({ date, tz }),
    staleTime: ONE_MINUTE_MS,
    refetchOnWindowFocus: true,
  });
}

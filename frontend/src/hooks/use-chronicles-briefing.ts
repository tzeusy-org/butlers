/**
 * Fetches the chronicles editorial briefing for a single day window.
 *
 * Distinct from the dashboard briefing (`use-briefing.ts`): this one is
 * sourced from the chronicler's own data and reads from the day-close
 * Tier-2 cache for the voice paragraph. Never calls the LLM directly.
 *
 * Capability spec: openspec/changes/chronicles-editorial-rewrite
 * Visual contract:  about/heart-and-soul/design-language.md (Editorial
 *   archetype, Voice surface, status pill).
 */

import { useQuery } from "@tanstack/react-query";

import { getChroniclesBriefing } from "@/api/client.ts";
import type { ChroniclesBriefing } from "@/api/types.ts";

const THIRTY_SECONDS_MS = 30 * 1000;

interface UseChroniclesBriefingArgs {
  date?: string;
  tz?: string;
}

export const chroniclesBriefingKeys = {
  all: ["chronicler", "briefing"] as const,
  byDate: (date: string | undefined, tz: string | undefined) =>
    ["chronicler", "briefing", date ?? "default", tz ?? "default"] as const,
};

export function useChroniclesBriefing(args: UseChroniclesBriefingArgs = {}) {
  const { date, tz } = args;
  return useQuery<ChroniclesBriefing>({
    queryKey: chroniclesBriefingKeys.byDate(date, tz),
    queryFn: () => getChroniclesBriefing({ date, tz }),
    // The archive shows only settled past days, which never change, so the
    // briefing does not auto-refresh (per dashboard-chronicles "Auto-Refresh
    // Adoption"). Staleness is surfaced via voice_source and the explicit
    // day-close refresh path, not a background poll.
    staleTime: THIRTY_SECONDS_MS,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  });
}

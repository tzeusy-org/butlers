/** Pure helpers for the Questions register (bu-q7vx1q.73). */

import type {
  MemoryGap,
  MemoryGapDemandResponse,
  MemoryGapDemandRow,
} from "@/api/memory-gaps.ts";
import type { KeysetResponse } from "@/api/types.ts";

const COVERAGE_WORDS: Record<string, string> = {
  unknown: "never looked",
  absent_proven: "looked, nothing there",
  unavailable: "unavailable",
  present: "answered",
};

/** The word for a coverage state; an unknown future state reads as itself. */
export function coverageWord(state: string): string {
  return COVERAGE_WORDS[state] ?? state.replace(/_/g, " ");
}

type GapPages = { pages: KeysetResponse<MemoryGap>[] } | undefined;

/** Every loaded row, in page order. Callers check the query's error state first. */
export function gapRows(data: GapPages): MemoryGap[] {
  return data ? data.pages.flatMap((page) => page.data) : [];
}

/** Pools any loaded page reported as dropped, de-duplicated. */
export function gapDegradedSources(data: GapPages): string[] {
  return data ? Array.from(new Set(data.pages.flatMap((page) => page.meta.sources_degraded ?? []))) : [];
}

export function demandRowsOf(data: MemoryGapDemandResponse | undefined): MemoryGapDemandRow[] {
  return data ? data.data : [];
}

export function demandDegradedSources(data: MemoryGapDemandResponse | undefined): string[] {
  return data?.meta.sources_degraded ? data.meta.sources_degraded : [];
}

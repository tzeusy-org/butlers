import { measurementDoorFromInsight, measurementDoorHref } from "@/lib/measurement-door";
import type { InsightCandidate } from "@/api/types";

/**
 * Map an InsightCandidate to a signal href.
 * Falls back to a same-origin default when the category is not mapped.
 */
export function insightHref(
  candidate: InsightCandidate,
  chartEligibleTypes: ReadonlySet<string>,
): string | null {
  const { category, metadata } = candidate;
  const measurementDoor = measurementDoorFromInsight(category, metadata);
  if (measurementDoor && chartEligibleTypes.has(measurementDoor.type)) {
    return measurementDoorHref(measurementDoor);
  }

  // Map known health signal categories to their fixed, same-origin sub-pages.
  // Untrusted metadata never controls a destination or query parameter.
  //
  // These MUST match the exact category strings run_insight_scan() submits
  // (roster/health/jobs/health_jobs.py) -- before bu-ep4ks.7 this switch used
  // singular guesses ("medication", "symptom", ...) that never matched any
  // real category, so every non-door insight silently fell through to the
  // `/health/measurements` default regardless of what it was actually about.
  switch (category) {
    case "medication-refill":
      return "/health/medications";
    case "symptom-trend":
      return "/health/symptoms";
    case "correlation-adherence":
      // Adherence dip preceding a symptom flare -- the leading signal is
      // medication adherence.
      return "/health/medications";
    case "correlation-environment":
      // HA environment reading co-occurring with short sleep or a symptom
      // entry -- the owned record type is the symptom log.
      return "/health/symptoms";
    case "health-streak":
      // Consecutive-day logging milestone: not tied to one record type: the
      // measurements ledger is the closest general "keep logging" surface.
      return "/health/measurements";
    default:
      return "/health/measurements";
  }
}

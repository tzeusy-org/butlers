// ---------------------------------------------------------------------------
// Health overview insight priority presentation
// ---------------------------------------------------------------------------

/** The severity labels accepted by the Health overview attention list. */
export type HealthInsightSeverity = "high" | "medium" | "low";

const HIGH_PRIORITY_THRESHOLD = 90;
const MEDIUM_PRIORITY_THRESHOLD = 50;

/**
 * Map a canonical InsightCandidate priority to the Health overview glyph
 * severity. Insight priority is higher-is-more-important end-to-end, so this
 * presentation mapping deliberately uses the backend's canonical thresholds.
 */
export function healthInsightSeverity(priority: number): HealthInsightSeverity {
  if (priority >= HIGH_PRIORITY_THRESHOLD) return "high";
  if (priority >= MEDIUM_PRIORITY_THRESHOLD) return "medium";
  return "low";
}

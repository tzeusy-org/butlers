// ---------------------------------------------------------------------------
// Routine provenance for inferred episodes (bu-whhll.11).
//
// The occupation-inference adapter (chronicler.occupation_inferred) stamps the
// producing routine onto every occupation_block episode's payload:
//   { routine_id, routine_label, local_date, corroborator_count }
// (see src/butlers/chronicler/adapters/occupation.py). The EpisodeDrawer uses
// this to show WHICH routine produced an inferred episode — closing the
// evidence chain from a bar on the timeline back to the schedule that implied
// it. Pure (no React) so it unit-tests directly.
// ---------------------------------------------------------------------------

export interface RoutineProvenance {
  routineId: string;
  routineLabel: string | null;
  localDate: string | null;
  corroboratorCount: number | null;
}

/**
 * Extract routine provenance from an episode payload, or null when the episode
 * was not produced by a routine (no `routine_id`). Tolerant of malformed
 * payloads: a present-but-wrong-typed field degrades to null rather than
 * throwing.
 */
export function extractRoutineProvenance(
  payload: Record<string, unknown> | null | undefined,
): RoutineProvenance | null {
  if (!payload) return null;
  const routineId = payload["routine_id"];
  if (typeof routineId !== "string" || routineId.length === 0) return null;

  const rawLabel = payload["routine_label"];
  const rawDate = payload["local_date"];
  const rawCount = payload["corroborator_count"];

  return {
    routineId,
    routineLabel: typeof rawLabel === "string" && rawLabel.length > 0 ? rawLabel : null,
    localDate: typeof rawDate === "string" && rawDate.length > 0 ? rawDate : null,
    corroboratorCount: typeof rawCount === "number" ? rawCount : null,
  };
}

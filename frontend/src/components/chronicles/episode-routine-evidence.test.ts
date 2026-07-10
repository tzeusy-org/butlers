import { describe, expect, it } from "vitest";

import { extractRoutineProvenance } from "./episode-routine-evidence";

describe("extractRoutineProvenance", () => {
  it("extracts full provenance from an occupation episode payload", () => {
    const p = extractRoutineProvenance({
      routine_id: "abc-123",
      routine_label: "Work at Acme",
      local_date: "2026-07-02",
      corroborator_count: 3,
    });
    expect(p).toEqual({
      routineId: "abc-123",
      routineLabel: "Work at Acme",
      localDate: "2026-07-02",
      corroboratorCount: 3,
    });
  });

  it("returns null when there is no routine_id", () => {
    expect(extractRoutineProvenance({ source: "spotify" })).toBeNull();
    expect(extractRoutineProvenance({})).toBeNull();
    expect(extractRoutineProvenance(null)).toBeNull();
    expect(extractRoutineProvenance(undefined)).toBeNull();
  });

  it("returns null when routine_id is empty or wrong-typed", () => {
    expect(extractRoutineProvenance({ routine_id: "" })).toBeNull();
    expect(extractRoutineProvenance({ routine_id: 42 })).toBeNull();
  });

  it("degrades malformed optional fields to null but keeps the id", () => {
    const p = extractRoutineProvenance({
      routine_id: "abc-123",
      routine_label: 99,
      local_date: null,
      corroborator_count: "many",
    });
    expect(p).toEqual({
      routineId: "abc-123",
      routineLabel: null,
      localDate: null,
      corroboratorCount: null,
    });
  });
});

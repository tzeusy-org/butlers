import { describe, expect, it } from "vitest";

import type { MeasurementTypeInfo } from "@/api/types";

import {
  chartableMeasurementTypes,
  selectKpiMeasurementSlots,
} from "./measurement-vocabulary";

function measurementType(
  overrides: Partial<MeasurementTypeInfo> & Pick<MeasurementTypeInfo, "type">,
): MeasurementTypeInfo {
  return {
    type: overrides.type,
    label: overrides.label ?? overrides.type.replace(/_/g, " "),
    sample_count: overrides.sample_count ?? 1,
    latest_at: overrides.latest_at ?? "2026-07-20T00:00:00Z",
    unit: overrides.unit ?? null,
    value_shape: overrides.value_shape ?? "scalar",
    chart_eligible: overrides.chart_eligible ?? true,
    kpi_eligible: overrides.kpi_eligible ?? false,
  };
}

describe("measurement vocabulary selection", () => {
  it("keeps only observed chartable types and derives a safe label when the server label is blank", () => {
    const chartTypes = chartableMeasurementTypes([
      measurementType({ type: "weight", label: "Weight" }),
      measurementType({ type: "hrv", label: "" }),
      measurementType({
        type: "recovery_note",
        chart_eligible: false,
        value_shape: "compound",
      }),
      measurementType({ type: "", label: "Broken" }),
    ]);

    expect(chartTypes).toEqual([
      expect.objectContaining({ type: "weight", label: "Weight" }),
      expect.objectContaining({ type: "hrv", label: "Hrv" }),
    ]);
  });

  it("keeps core vital positions and fills only absent slots with eligible dynamic types by newest sample", () => {
    const slots = selectKpiMeasurementSlots([
      measurementType({
        type: "active_minutes",
        label: "Active minutes",
        kpi_eligible: true,
        latest_at: "2026-07-18T00:00:00Z",
      }),
      measurementType({
        type: "blood_sugar",
        label: "Blood sugar",
        latest_at: "2026-07-15T00:00:00Z",
      }),
      measurementType({
        type: "hrv",
        label: "HRV",
        kpi_eligible: true,
        latest_at: "2026-07-19T00:00:00Z",
      }),
      measurementType({ type: "weight", label: "Weight" }),
      measurementType({
        type: "recovery_note",
        label: "Recovery note",
        kpi_eligible: false,
        latest_at: "2026-07-20T00:00:00Z",
        value_shape: "compound",
      }),
    ]);

    expect(slots).toEqual([
      expect.objectContaining({ type: "weight", label: "Weight" }),
      expect.objectContaining({ type: "hrv", label: "HRV" }),
      expect.objectContaining({ type: "active_minutes", label: "Active minutes" }),
      expect.objectContaining({ type: "blood_sugar", label: "Blood sugar" }),
    ]);
  });

  it("retains four named empty slots instead of inventing a value when no dynamic candidate is eligible", () => {
    const slots = selectKpiMeasurementSlots([
      measurementType({ type: "weight", label: "Weight" }),
      measurementType({
        type: "hrv",
        label: "HRV",
        kpi_eligible: false,
      }),
    ]);

    expect(slots).toEqual([
      expect.objectContaining({ type: "weight", label: "Weight" }),
      { type: null, label: "Blood pressure" },
      { type: null, label: "Heart rate" },
      { type: null, label: "Blood sugar" },
    ]);
  });
});

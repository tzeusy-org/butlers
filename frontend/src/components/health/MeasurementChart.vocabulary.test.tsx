/**
 * MeasurementChart — observed vocabulary contract [bu-kqnum.12.3]
 *
 * Chart tabs must come only from the server's observed chartable vocabulary;
 * a malformed reading must never fabricate a plotted number.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import MeasurementChart from "@/components/health/MeasurementChart";

const refetch = vi.fn();

const vocabulary = {
  types: [
    {
      type: "hrv",
      label: "HRV",
      sample_count: 3,
      latest_at: "2026-07-20T00:00:00Z",
      unit: "ms",
      value_shape: "scalar" as const,
      chart_eligible: true,
      kpi_eligible: false,
    },
    {
      type: "blood_pressure",
      label: "Blood pressure",
      sample_count: 2,
      latest_at: "2026-07-19T00:00:00Z",
      unit: "mmHg",
      value_shape: "compound" as const,
      chart_eligible: true,
      kpi_eligible: true,
    },
    {
      type: "recovery_note",
      label: "Recovery note",
      sample_count: 1,
      latest_at: "2026-07-18T00:00:00Z",
      unit: null,
      value_shape: "compound" as const,
      chart_eligible: false,
      kpi_eligible: false,
    },
  ],
};

let measurementsResult: {
  data: { data: Array<Record<string, unknown>> };
  isLoading: boolean;
  isError: boolean;
  refetch: typeof refetch;
} = {
  data: { data: [] },
  isLoading: false,
  isError: false,
  refetch,
};

vi.mock("@/hooks/use-health", () => ({
  useMeasurementTypes: () => ({
    data: vocabulary,
    isLoading: false,
    isError: false,
    refetch,
  }),
  useMeasurements: () => measurementsResult,
  useMeasurementTrend: () => ({
    data: { buckets: [] },
    isLoading: false,
    isError: false,
    refetch,
  }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  measurementsResult = {
    data: { data: [] },
    isLoading: false,
    isError: false,
    refetch,
  };
});

describe("MeasurementChart — observed vocabulary", () => {
  it("renders only observed chartable tabs and selects the first one when the old default is absent", () => {
    render(<MeasurementChart />);

    expect(screen.getByRole("tab", { name: "HRV" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "Blood pressure" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Recovery note" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Weight" })).toBeNull();
  });

  it("does not plot a malformed chartable reading as a numeric value", () => {
    measurementsResult = {
      data: {
        data: [
          {
            id: "bad-hrv",
            type: "hrv",
            value: { daily_rmssd: "not-a-number", coverage: "also-not-a-number" },
            measured_at: "2026-07-20T00:00:00Z",
            notes: null,
            created_at: "2026-07-20T00:00:00Z",
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch,
    };

    render(<MeasurementChart initialType="hrv" />);

    expect(screen.getByText(/no hrv readings for this range/i)).toBeTruthy();
  });
});

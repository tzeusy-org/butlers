/**
 * MeasurementChart — degraded-source honesty [bu-hmdqz.13]
 *
 * A failing trend or readings read MUST render a named SourceDegradedNote,
 * never a silently-empty trend list or chart.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import MeasurementChart from "@/components/health/MeasurementChart";

const refetchTrend = vi.fn();
const refetchReadings = vi.fn();

vi.mock("@/hooks/use-health", () => ({
  useMeasurementTypes: () => ({
    data: {
      types: [
        {
          type: "weight",
          label: "Weight",
          sample_count: 1,
          latest_at: "2026-07-20T00:00:00Z",
          unit: "kg",
          value_shape: "scalar",
          chart_eligible: true,
          kpi_eligible: true,
        },
      ],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useMeasurements: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    refetch: refetchReadings,
  }),
  useMeasurementTrend: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    refetch: refetchTrend,
  }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MeasurementChart — degraded source honesty", () => {
  it("names failing trend and readings sources instead of empty surfaces", () => {
    render(
      <MemoryRouter>
        <MeasurementChart />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("measurement-trend-degraded")).toBeTruthy();
    expect(screen.getByTestId("measurement-readings-degraded")).toBeTruthy();
  });
});

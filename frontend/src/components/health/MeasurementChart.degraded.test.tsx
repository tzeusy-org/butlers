/**
 * MeasurementChart — degraded-source honesty [bu-hmdqz.13]
 *
 * A failing trend or readings read MUST render a named SourceDegradedNote,
 * never a silently-empty trend list or chart.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import MeasurementChart from "@/components/health/MeasurementChart";

const refetchTrend = vi.fn();
const refetchReadings = vi.fn();

vi.mock("@/hooks/use-health", () => ({
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
    render(<MeasurementChart />);
    expect(screen.getByTestId("measurement-trend-degraded")).toBeTruthy();
    expect(screen.getByTestId("measurement-readings-degraded")).toBeTruthy();
  });
});

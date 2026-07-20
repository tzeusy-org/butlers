/**
 * MeasurementChart URL state [bu-kqnum.12.5]
 *
 * The chart must use the URL as the sole source for its type/date bounds and
 * must never issue a chart read for invalid URL state before it normalizes it.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router";

import MeasurementChart from "@/components/health/MeasurementChart";

const refetch = vi.fn();
const useMeasurementsMock = vi.fn();
let rawMeasurements: Array<Record<string, unknown>> = [];

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

vi.mock("@/hooks/use-health", () => ({
  useMeasurementTypes: () => ({
    data: vocabulary,
    isLoading: false,
    isError: false,
    refetch,
  }),
  useMeasurements: (...args: unknown[]) => {
    useMeasurementsMock(...args);
    return {
      data: { data: rawMeasurements },
      isLoading: false,
      isError: false,
      refetch,
    };
  },
  useMeasurementTrend: () => ({
    data: { buckets: [] },
    isLoading: false,
    isError: false,
    refetch,
  }),
}));

function LocationSearch() {
  const location = useLocation();
  return <output data-testid="measurement-chart-location">{location.search}</output>;
}

function NavigateToInvalidRange() {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() =>
        navigate("/health/measurements?keep=present&type=hrv&since=2026-07-20&until=2026-07-01")
      }
    >
      Use invalid range
    </button>
  );
}

function renderChart(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <MeasurementChart />
      <LocationSearch />
      <NavigateToInvalidRange />
    </MemoryRouter>,
  );
}

function currentParams(): URLSearchParams {
  return new URLSearchParams(screen.getByTestId("measurement-chart-location").textContent ?? "");
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  rawMeasurements = [];
});

describe("MeasurementChart — URL-authoritative state", () => {
  it("hydrates a valid chart type and bounds from the URL, then preserves unrelated keys on edits", async () => {
    renderChart("/health/measurements?keep=present&type=hrv&since=2026-07-01&until=2026-07-20");

    expect(screen.getByRole("tab", { name: "HRV" }).getAttribute("aria-selected")).toBe("true");
    expect(useMeasurementsMock).toHaveBeenCalledWith(
      {
        type: "hrv",
        since: "2026-07-01",
        until: "2026-07-20",
        limit: 500,
      },
      { enabled: true },
    );

    fireEvent.click(screen.getByRole("tab", { name: "Blood pressure" }));
    await waitFor(() => expect(currentParams().get("type")).toBe("blood_pressure"));

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-07-02" } });
    await waitFor(() => expect(currentParams().get("since")).toBe("2026-07-02"));

    const params = currentParams();
    expect(params.get("keep")).toBe("present");
    expect(params.get("until")).toBe("2026-07-20");
  });

  it.each(["unknown_type", "recovery_note"])(
    "suppresses the chart read for an unknown or non-chart-eligible URL type (%s)",
    (type) => {
      renderChart(`/health/measurements?keep=present&type=${type}`);

      expect(useMeasurementsMock).toHaveBeenCalledWith(
        expect.any(Object),
        { enabled: false },
      );

      expect(currentParams().get("type")).toBe(type);
      expect(currentParams().get("keep")).toBe("present");
      expect(
        useMeasurementsMock.mock.calls.some(
          ([params, options]) =>
            (params as { type?: string }).type === type &&
            (options as { enabled?: boolean }).enabled,
        ),
      ).toBe(false);
    },
  );

  it.each([
    "/health/measurements?keep=present&type=hrv&since=2026-02-30&until=2026-07-01",
    "/health/measurements?keep=present&type=hrv&since=2026-07-20&until=2026-07-01",
  ])("suppresses malformed or reversed date bounds without dropping unrelated keys", (path) => {
    renderChart(path);

    expect(useMeasurementsMock).toHaveBeenCalledWith(
      expect.any(Object),
      { enabled: false },
    );

    expect(currentParams().get("since")).toBe(new URL(path, "https://butlers.test").searchParams.get("since"));
    expect(currentParams().get("until")).toBe(new URL(path, "https://butlers.test").searchParams.get("until"));
    expect(currentParams().get("keep")).toBe("present");
  });

  it("hides warm raw data after navigation changes the URL to an invalid range", async () => {
    rawMeasurements = [
      {
        id: "warm-reading",
        type: "hrv",
        value: { value: 31.5 },
        measured_at: "2026-07-01T12:00:00Z",
        notes: "Warm cached reading",
      },
    ];
    renderChart("/health/measurements?keep=present&type=hrv");

    fireEvent.click(screen.getByRole("button", { name: "Show raw data" }));
    expect(screen.getByText("Warm cached reading")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Use invalid range" }));
    await waitFor(() => expect(currentParams().get("since")).toBe("2026-07-20"));

    expect(screen.getByText(/invalid type or date filters/i)).toBeTruthy();
    expect(screen.queryByText("Warm cached reading")).toBeNull();
    expect(screen.queryByRole("button", { name: /raw data/i })).toBeNull();
  });
});

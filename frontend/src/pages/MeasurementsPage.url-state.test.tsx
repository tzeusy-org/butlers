/**
 * MeasurementsPage URL query gates [bu-kqnum.12.5.1]
 *
 * Invalid chart-door URLs must disable both the chart and the reading log
 * without rewriting the owner's raw URL selection.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";

import MeasurementsPage from "@/pages/MeasurementsPage";

const refetch = vi.fn();
const useMeasurementsMock = vi.fn();
const useMeasurementTrendMock = vi.fn();

const measurementTypes = [
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
    type: "recovery_note",
    label: "Recovery note",
    sample_count: 1,
    latest_at: "2026-07-19T00:00:00Z",
    unit: null,
    value_shape: "compound" as const,
    chart_eligible: false,
    kpi_eligible: false,
  },
];
const defaultMeasurementTypesResult = {
  data: { types: measurementTypes },
  isLoading: false,
  isError: false,
  refetch,
};
type MeasurementTypesResult = {
  data: typeof defaultMeasurementTypesResult.data | undefined;
  isLoading: boolean;
  isError: boolean;
  refetch: typeof refetch;
};
let measurementTypesResult: MeasurementTypesResult = defaultMeasurementTypesResult;

vi.mock("@/hooks/use-health", () => ({
  useMeasurementTypes: () => measurementTypesResult,
  useMeasurementTrend: (...args: unknown[]) => {
    useMeasurementTrendMock(...args);
    return {
      data: { buckets: [] },
      isLoading: false,
      isError: false,
      refetch,
    };
  },
  useMeasurements: (...args: unknown[]) => {
    useMeasurementsMock(...args);
    return {
      data: { data: [], meta: { total: 0, has_more: false } },
      isLoading: false,
      isError: false,
      error: null,
      refetch,
    };
  },
  useCreateMeasurement: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateMeasurement: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteMeasurement: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function LocationSearch() {
  const location = useLocation();
  return <output data-testid="measurements-page-location">{location.search}</output>;
}

function renderPage(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <MeasurementsPage />
      <LocationSearch />
    </MemoryRouter>,
  );
}

function trackerReadCall() {
  return useMeasurementsMock.mock.calls.find(
    ([params]) => (params as { limit?: number }).limit === 50,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  measurementTypesResult = defaultMeasurementTypesResult;
});

describe("MeasurementsPage — URL query gates", () => {
  it.each([
    [
      "unknown type",
      "/health/measurements?keep=present&type=unknown_measurement&since=2026-07-01&until=2026-07-20",
    ],
    [
      "known but chart-ineligible type",
      "/health/measurements?keep=present&type=recovery_note&since=2026-07-01&until=2026-07-20",
    ],
    [
      "malformed date",
      "/health/measurements?keep=present&type=hrv&since=2026-02-30&until=2026-07-20",
    ],
    [
      "reversed dates",
      "/health/measurements?keep=present&type=hrv&since=2026-07-20&until=2026-07-01",
    ],
  ])("does not query chart or reading-log data for %s", (_description, path) => {
    renderPage(path);

    expect(
      useMeasurementsMock.mock.calls.some(
        ([, options]) => (options as { enabled?: boolean } | undefined)?.enabled !== false,
      ),
    ).toBe(false);
    expect(
      useMeasurementTrendMock.mock.calls.some(
        ([, options]) => (options as { enabled?: boolean } | undefined)?.enabled !== false,
      ),
    ).toBe(false);
    expect(trackerReadCall()?.[1]).toEqual({ enabled: false });
    expect(
      screen.getByText("That reading-log link has invalid type or date filters."),
    ).toBeTruthy();
    expect(screen.queryByText(/No measurements logged yet/i)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Clear" })).toHaveLength(2);

    const query = new URL(path, "https://butlers.test").search;
    expect(screen.getByTestId("measurements-page-location").textContent).toBe(query);
    expect((screen.getByLabelText("Filter by type") as HTMLSelectElement).value).toBe(
      new URL(path, "https://butlers.test").searchParams.get("type") ?? "",
    );
  });

  it("queries the raw reading log for a valid eligible URL selection", () => {
    renderPage("/health/measurements?keep=present&type=hrv&since=2026-07-01&until=2026-07-20");

    expect(trackerReadCall()).toEqual([
      {
        type: "hrv",
        since: "2026-07-01",
        until: "2026-07-20",
        offset: 0,
        limit: 50,
      },
      { enabled: true },
    ]);
    expect((screen.getByLabelText("Filter by type") as HTMLSelectElement).value).toBe("hrv");
  });

  it("keeps the unfiltered reading log query enabled", () => {
    renderPage("/health/measurements?keep=present");

    expect(trackerReadCall()).toEqual([
      {
        type: undefined,
        since: undefined,
        until: undefined,
        offset: 0,
        limit: 50,
      },
      { enabled: true },
    ]);
  });

  it("keeps an unfiltered reading log query enabled while vocabulary loads", () => {
    measurementTypesResult = {
      data: undefined,
      isLoading: true,
      isError: false,
      refetch,
    };

    renderPage("/health/measurements?keep=present");

    expect(trackerReadCall()?.[1]).toEqual({ enabled: true });
  });
});

/**
 * HealthOverviewPage — measurement vocabulary contract [bu-kqnum.12.3]
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type {
  MeasurementTypeInfo,
  MeasurementsLatestResponse,
} from "@/api/types";

const refetch = vi.fn();
const latestCalls = vi.fn();

function measurementType(
  type: string,
  label: string,
  kpiEligible = false,
  latestAt = "2026-07-20T00:00:00Z",
): MeasurementTypeInfo {
  return {
    type,
    label,
    sample_count: 1,
    latest_at: latestAt,
    unit: null,
    value_shape: type === "blood_pressure" ? "compound" : "scalar",
    chart_eligible: true,
    kpi_eligible: kpiEligible,
  };
}

let vocabularyResult: {
  data: { types: MeasurementTypeInfo[] } | undefined;
  isLoading: boolean;
  isError: boolean;
  refetch: typeof refetch;
};
let latestResult: {
  data: MeasurementsLatestResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  refetch: typeof refetch;
};

function resetResults() {
  vocabularyResult = {
    data: {
      types: [
        measurementType("weight", "Weight", true),
        measurementType("blood_sugar", "Blood sugar", true),
        measurementType("hrv", "HRV", true, "2026-07-20T00:00:00Z"),
        measurementType("active_minutes", "Active minutes", true, "2026-07-19T00:00:00Z"),
      ],
    },
    isLoading: false,
    isError: false,
    refetch,
  };
  latestResult = {
    data: {
      measurements: {
        weight: {
          measured_at: "2026-07-20T00:00:00Z",
          value: { value: 70 },
          unit: "kg",
          metadata: null,
        },
        hrv: {
          measured_at: "2026-07-20T00:00:00Z",
          value: { value: 31.5 },
          unit: "ms",
          metadata: null,
        },
        active_minutes: {
          measured_at: "2026-07-20T00:00:00Z",
          value: { value: 45 },
          unit: "minutes",
          metadata: null,
        },
        blood_sugar: {
          measured_at: "2026-07-20T00:00:00Z",
          value: { value: 95 },
          unit: "mg/dL",
          metadata: null,
        },
      },
    },
    isLoading: false,
    isError: false,
    refetch,
  };
}

resetResults();

vi.mock("@/hooks/use-health", () => ({
  useMeasurementTypes: () => vocabularyResult,
  useMeasurementsLatest: (types: string[]) => {
    latestCalls(types);
    return latestResult;
  },
  useMeasurementSources: () => ({
    data: [],
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("@/hooks/use-health-briefing", () => ({
  useHealthBriefing: () => ({
    data: {
      greet: "Good day.",
      headline: "Health is ready.",
      elaboration: "",
      source: "fallback",
      generated_at: "2026-07-20T00:00:00Z",
    },
    isFetching: false,
    isError: false,
    refetch,
  }),
}));

vi.mock("@/hooks/use-insights", () => ({
  useInsights: () => ({ data: [], isError: false, refetch }),
}));

vi.mock("@/lib/command-registry", () => ({
  useRegisterCommands: vi.fn(),
}));

import HealthOverviewPage from "./HealthOverviewPage";

function renderPage() {
  return render(
    <MemoryRouter>
      <HealthOverviewPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  resetResults();
});

describe("HealthOverviewPage — measurement vocabulary", () => {
  it("fills only absent core KPI positions with server-authorized dynamic types", () => {
    const { container } = renderPage();

    const strip = container.querySelector('[aria-label="Key performance indicators"]');
    expect(strip).toBeTruthy();
    expect(Array.from(strip!.children).map((cell) => cell.querySelector("p")?.textContent)).toEqual([
      "Weight",
      "HRV",
      "Active minutes",
      "Blood sugar",
    ]);
    expect(latestCalls).toHaveBeenCalledWith([
      "weight",
      "hrv",
      "active_minutes",
      "blood_sugar",
    ]);
  });

  it("retains four structural cells and rejects malformed scalar text", () => {
    vocabularyResult = {
      data: { types: [measurementType("hrv", "HRV", true)] },
      isLoading: false,
      isError: false,
      refetch,
    };
    latestResult = {
      data: {
        measurements: {
          hrv: {
            measured_at: "2026-07-20T00:00:00Z",
            value: { value: "31ms" },
            unit: "ms",
            metadata: null,
          },
        },
      },
      isLoading: false,
      isError: false,
      refetch,
    };

    const { container } = renderPage();

    const strip = container.querySelector('[aria-label="Key performance indicators"]');
    expect(strip?.children).toHaveLength(4);
    expect(screen.queryByText("31")).toBeNull();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("keeps the fixed cells visible and names a vocabulary failure", () => {
    vocabularyResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    };

    const { container } = renderPage();

    expect(container.querySelector('[aria-label="Key performance indicators"]')?.children).toHaveLength(4);
    expect(screen.getByTestId("measurement-types-degraded")).toBeTruthy();
  });
});

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
let insightsResult: {
  data: Array<Record<string, unknown>>;
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
  insightsResult = {
    data: [],
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
  useInsights: () => insightsResult,
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

  it("builds a same-origin measurement door only from typed gap metadata", () => {
    insightsResult = {
      data: [
        {
          id: "gap-weight",
          origin_butler: "health",
          priority: 75,
          category: "measurement-gap",
          dedup_key: "health:measurement-gap:weight",
          cooldown_days: null,
          expires_at: null,
          message: "Weight is overdue.",
          channel: null,
          metadata: {
            measurement_door: {
              type: "weight",
              since: "2026-07-01",
              until: "2026-07-20",
            },
          },
          created_at: null,
          status: "pending",
          delivered_at: null,
          delivery_attempt_count: 0,
        },
      ],
      isError: false,
      refetch,
    };

    renderPage();

    expect(screen.getByTestId("attention-item").getAttribute("href")).toBe(
      "/health/measurements?type=weight&since=2026-07-01&until=2026-07-20",
    );
  });

  it("ignores arbitrary insight href metadata when a measurement door is invalid", () => {
    insightsResult = {
      data: [
        {
          id: "gap-invalid",
          origin_butler: "health",
          priority: 55,
          category: "measurement-gap",
          dedup_key: "health:measurement-gap:weight",
          cooldown_days: null,
          expires_at: null,
          message: "Weight needs a check-in.",
          channel: null,
          metadata: {
            href: "https://untrusted.example/measurement",
            measurement_door: {
              type: "weight",
              since: "2026-07-20",
              until: "2026-07-01",
            },
          },
          created_at: null,
          status: "pending",
          delivered_at: null,
          delivery_attempt_count: 0,
        },
      ],
      isError: false,
      refetch,
    };

    renderPage();

    expect(screen.getByTestId("attention-item").getAttribute("href")).toBe(
      "/health/measurements",
    );
  });

  it.each([
    ["unknown", "unknown_measurement", undefined],
    ["known but ineligible", "recovery_note", false],
  ])("falls back for an %s measurement-door type", (_description, type, chartEligible) => {
    const types = [measurementType("weight", "Weight", true)];
    if (chartEligible !== undefined) {
      const observedType = measurementType(type, "Recovery note");
      observedType.chart_eligible = chartEligible;
      types.push(observedType);
    }
    vocabularyResult = {
      data: { types },
      isLoading: false,
      isError: false,
      refetch,
    };
    insightsResult = {
      data: [
        {
          id: `gap-${type}`,
          origin_butler: "health",
          priority: 55,
          category: "measurement-gap",
          dedup_key: `health:measurement-gap:${type}`,
          cooldown_days: null,
          expires_at: null,
          message: "A measurement needs a check-in.",
          channel: null,
          metadata: {
            measurement_door: {
              type,
              since: "2026-07-01",
              until: "2026-07-20",
            },
          },
          created_at: null,
          status: "pending",
          delivered_at: null,
          delivery_attempt_count: 0,
        },
      ],
      isError: false,
      refetch,
    };

    renderPage();

    expect(screen.getByTestId("attention-item").getAttribute("href")).toBe(
      "/health/measurements",
    );
  });
});

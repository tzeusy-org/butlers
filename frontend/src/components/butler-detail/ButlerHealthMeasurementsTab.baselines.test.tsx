// @vitest-environment jsdom
/**
 * Health measurements tab: the usual-range band (bu-q7vx1q.77).
 *
 * A ready baseline draws the band and the "n of m days" plaque; insufficient
 * history, an unmeasurable instrument, a stale or missing baseline render as one
 * sentence and no band element; an unreadable store says so and the trend keeps
 * rendering.
 */

import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

vi.mock("recharts", () => {
  const LineChart = ({ children }: { children?: ReactNode }) =>
    createElement("div", { "data-testid": "recharts-line-chart" }, children);
  const Line = ({ dataKey }: { dataKey: string }) =>
    createElement("div", { "data-testid": `recharts-line-${dataKey}` });
  const ReferenceArea = ({ y1, y2 }: { y1?: number; y2?: number }) =>
    y1 === undefined
      ? null
      : createElement("div", {
          "data-testid": "recharts-usual-band",
          "data-y1": String(y1),
          "data-y2": String(y2),
        });
  const Null = () => null;
  const ResponsiveContainer = ({ children }: { children?: ReactNode }) =>
    createElement("div", null, children);
  return {
    LineChart,
    Line,
    XAxis: Null,
    YAxis: Null,
    Tooltip: Null,
    ReferenceArea,
    ResponsiveContainer,
  };
});

vi.mock("@/hooks/use-health", () => ({
  useMeasurementsLatest: vi.fn(),
  useSleepLatest: vi.fn(),
  useMeasurementSources: vi.fn(),
  useExpectedSignals: vi.fn(),
  useMeasurements: vi.fn(),
  useMedications: vi.fn(),
  useConditions: vi.fn(),
}));

vi.mock("@/hooks/use-health-baselines", () => ({ useHealthBaselines: vi.fn() }));

vi.mock("@/hooks/use-prefers-reduced-motion", () => ({
  usePrefersReducedMotion: vi.fn().mockReturnValue(false),
}));

vi.mock("@/components/ui/timezone-context", () => ({ useTimezone: () => "UTC" }));

import {
  useConditions,
  useExpectedSignals,
  useMeasurements,
  useMeasurementSources,
  useMeasurementsLatest,
  useMedications,
  useSleepLatest,
} from "@/hooks/use-health";
import { useHealthBaselines } from "@/hooks/use-health-baselines";
import type { HealthBaseline } from "@/api/index.ts";

import ButlerHealthMeasurementsTab from "./ButlerHealthMeasurementsTab";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyQueryResult = any;

function baseline(over: Partial<HealthBaseline> & { metric_key: string }): HealthBaseline {
  return {
    state: "ready",
    band: { low: 50.8, high: 61.2 },
    center: 56,
    unit: "bpm",
    n_observed: 54,
    n_expected: 60,
    coverage: 0.9,
    computed_at: "2026-10-18T06:00:00Z",
    stale: false,
    ...over,
  };
}

const NONE = (key: string): HealthBaseline =>
  baseline({
    metric_key: key,
    state: "no_baseline",
    band: null,
    center: null,
    unit: null,
    n_observed: 0,
    n_expected: 0,
    coverage: 0,
    computed_at: null,
  });

function setBaselines(result: {
  baselines?: HealthBaseline[] | null;
  available?: boolean;
  isLoading?: boolean;
  isError?: boolean;
}) {
  vi.mocked(useHealthBaselines).mockReturnValue({
    data:
      result.isError || result.isLoading
        ? undefined
        : {
            baselines: result.baselines ?? null,
            available: result.available ?? true,
            degraded_reason: result.available === false ? "baselines_unavailable" : null,
          },
    isLoading: result.isLoading ?? false,
    isError: result.isError ?? false,
  } as AnyQueryResult);
}

const HRV_TREND = [
  {
    id: "m-1",
    type: "hrv",
    value: { value: 40 },
    measured_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    notes: null,
    created_at: "2026-10-17T08:00:00Z",
  },
  {
    id: "m-2",
    type: "hrv",
    value: { value: 44 },
    measured_at: new Date(Date.now() - 86_400_000).toISOString(),
    notes: null,
    created_at: "2026-10-18T08:00:00Z",
  },
];

function setupRest() {
  const idle = { data: undefined, isLoading: false, isError: false } as AnyQueryResult;
  vi.mocked(useMeasurementsLatest).mockReturnValue({
    data: { measurements: {} },
    isLoading: false,
  } as AnyQueryResult);
  vi.mocked(useSleepLatest).mockReturnValue({
    data: { session_date: null, total_minutes: null, stages: null, source: null },
    isLoading: false,
  } as AnyQueryResult);
  vi.mocked(useMeasurementSources).mockReturnValue({ ...idle, data: [] });
  vi.mocked(useExpectedSignals).mockReturnValue({
    ...idle,
    data: { signals: [], available: true, degraded_reason: null },
  });
  vi.mocked(useMeasurements).mockReturnValue({
    data: { data: HRV_TREND, meta: { total: 2, offset: 0, limit: 50 } },
    isLoading: false,
  } as AnyQueryResult);
  vi.mocked(useMedications).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20 } },
    isLoading: false,
  } as AnyQueryResult);
  vi.mocked(useConditions).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 10 } },
    isLoading: false,
  } as AnyQueryResult);
}

function renderTab() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <ButlerHealthMeasurementsTab />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  setupRest();
});

afterEach(() => {
  cleanup();
});

describe("usual-range panel", () => {
  it("draws the band and the n-of-m plaque for a ready baseline", () => {
    setBaselines({
      baselines: [
        baseline({ metric_key: "resting_hr" }),
        NONE("hrv"),
        NONE("sleep_duration"),
      ],
    });
    renderTab();

    expect(screen.getByTestId("baseline-band-resting_hr")).toBeTruthy();
    expect(screen.getByTestId("baseline-range-resting_hr").textContent).toBe(
      "Usual 50.8 to 61.2 bpm",
    );
    expect(screen.getByTestId("baseline-plaque-resting_hr").textContent).toBe("54 of 60 days");
    expect(screen.queryByTestId("baseline-words-resting_hr")).toBeNull();
  });

  it("says how many days are still missing and draws no band when history is thin", () => {
    setBaselines({
      baselines: [
        NONE("resting_hr"),
        baseline({
          metric_key: "hrv",
          state: "insufficient_history",
          band: null,
          center: null,
          unit: "ms",
          n_observed: 9,
          n_expected: 60,
        }),
        NONE("sleep_duration"),
      ],
    });
    renderTab();

    expect(screen.getByTestId("baseline-words-hrv").textContent).toBe(
      "Not enough days yet: 9 of 60",
    );
    expect(screen.queryByTestId("baseline-band-hrv")).toBeNull();
    expect(screen.queryByTestId("baseline-plaque-hrv")).toBeNull();
  });

  it("names an unmeasurable instrument in words, never a band", () => {
    setBaselines({
      baselines: [
        baseline({ metric_key: "resting_hr", state: "unmeasurable", band: null, center: null }),
        NONE("hrv"),
        NONE("sleep_duration"),
      ],
    });
    renderTab();

    expect(screen.getByTestId("baseline-words-resting_hr").textContent).toContain(
      "instrument is not reporting",
    );
    expect(screen.queryByTestId("baseline-band-resting_hr")).toBeNull();
  });

  it("renders a stale baseline as a sentence with its computed date and no band", () => {
    setBaselines({
      baselines: [
        NONE("resting_hr"),
        NONE("hrv"),
        baseline({
          metric_key: "sleep_duration",
          state: "stale",
          band: null,
          center: null,
          unit: "h",
          stale: true,
          computed_at: "2026-10-10T06:00:00Z",
        }),
      ],
    });
    renderTab();

    const words = screen.getByTestId("baseline-words-sleep_duration");
    expect(words.textContent).toContain("Last computed");
    expect(words.textContent).toContain("hidden until it refreshes");
    expect(screen.queryByTestId("baseline-band-sleep_duration")).toBeNull();
  });

  it("says no baseline yet when the job has not run", () => {
    setBaselines({ baselines: [NONE("resting_hr"), NONE("hrv"), NONE("sleep_duration")] });
    renderTab();

    expect(screen.getByTestId("baseline-words-resting_hr").textContent).toBe("No baseline yet");
    expect(screen.queryByTestId("baseline-band-resting_hr")).toBeNull();
  });

  it("says the ranges could not be read, and the trend keeps rendering", () => {
    setBaselines({ baselines: null, available: false });
    renderTab();

    expect(screen.getByTestId("health-baselines-unavailable").textContent).toContain(
      "could not be read",
    );
    expect(screen.queryByTestId("health-baselines-list")).toBeNull();
    expect(screen.getAllByTestId("trend-chart").length).toBeGreaterThan(0);
  });

  it("never draws a band from an envelope that says it is unavailable", () => {
    setBaselines({
      baselines: [baseline({ metric_key: "resting_hr" }), NONE("hrv"), NONE("sleep_duration")],
      available: false,
    });
    renderTab();

    expect(screen.getByTestId("health-baselines-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("baseline-band-resting_hr")).toBeNull();
  });

  it("treats a failed request like an unreadable store", () => {
    setBaselines({ isError: true });
    renderTab();

    expect(screen.getByTestId("health-baselines-unavailable")).toBeTruthy();
    expect(screen.getAllByTestId("trend-chart").length).toBeGreaterThan(0);
  });

  it("shows a loading line while the ranges load", () => {
    setBaselines({ isLoading: true });
    renderTab();

    expect(screen.getByTestId("health-baselines-loading")).toBeTruthy();
  });
});

describe("HRV trend band", () => {
  it("shades the usual range on the HRV trend only for a ready baseline", () => {
    setBaselines({
      baselines: [
        NONE("resting_hr"),
        baseline({ metric_key: "hrv", band: { low: 38, high: 52 }, unit: "ms" }),
        NONE("sleep_duration"),
      ],
    });
    renderTab();

    const hrvPanel = screen.getByTestId("trend-panel-hrv");
    const band = within(hrvPanel).getByTestId("recharts-usual-band");
    expect(band.getAttribute("data-y1")).toBe("38");
    expect(band.getAttribute("data-y2")).toBe("52");
    const glucose = screen.getByTestId("trend-panel-glucose");
    expect(within(glucose).queryByTestId("recharts-usual-band")).toBeNull();
  });

  it.each(["insufficient_history", "unmeasurable", "stale", "no_baseline"] as const)(
    "draws no band on the HRV trend when the baseline is %s",
    (state) => {
      setBaselines({
        baselines: [
          NONE("resting_hr"),
          baseline({ metric_key: "hrv", state, band: null, center: null, unit: "ms" }),
          NONE("sleep_duration"),
        ],
      });
      renderTab();

      expect(screen.queryByTestId("recharts-usual-band")).toBeNull();
      expect(within(screen.getByTestId("trend-panel-hrv")).getByTestId("trend-chart")).toBeTruthy();
    },
  );

  it("never draws a band from a ready-looking row whose state is stale", () => {
    setBaselines({
      baselines: [
        NONE("resting_hr"),
        baseline({ metric_key: "hrv", state: "stale", band: { low: 38, high: 52 } }),
        NONE("sleep_duration"),
      ],
    });
    renderTab();

    expect(screen.queryByTestId("recharts-usual-band")).toBeNull();
    expect(screen.queryByTestId("baseline-band-hrv")).toBeNull();
  });
});

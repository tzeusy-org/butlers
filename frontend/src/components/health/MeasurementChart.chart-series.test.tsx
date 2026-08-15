/**
 * MeasurementChart chart-series contract
 *
 * Recharts receives the theme's chart CSS variables directly as SVG stroke
 * values. Chromium resolves those presentation attributes at paint time, so a
 * computed-style-to-literal bridge would be a different contract.
 */

// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const DOMAIN_SPEC_PATH = resolve(process.cwd(), "../openspec/specs/dashboard-domain-pages/spec.md");
const DOMAIN_SPEC = readFileSync(DOMAIN_SPEC_PATH, "utf8");

vi.mock("recharts", () => {
  const LineChart = ({ children }: { children?: ReactNode }) =>
    createElement("div", null, children);
  const Line = ({ dataKey, stroke }: { dataKey: string; stroke?: string }) =>
    createElement("output", {
      "data-stroke": stroke,
      "data-testid": `measurement-chart-line-${dataKey}`,
    });
  const ResponsiveContainer = ({ children }: { children?: ReactNode }) =>
    createElement("div", null, children);

  return {
    CartesianGrid: () => null,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip: () => null,
    XAxis: () => null,
    YAxis: () => null,
  };
});

vi.mock("@/hooks/use-health", () => ({
  useMeasurementTypes: () => ({
    data: {
      types: [
        {
          type: "blood_pressure",
          label: "Blood pressure",
          sample_count: 1,
          latest_at: "2026-08-14T00:00:00Z",
          unit: "mmHg",
          value_shape: "compound" as const,
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
    data: {
      data: [
        {
          id: "bp-1",
          type: "blood_pressure",
          value: { systolic: 120, diastolic: 80 },
          measured_at: "2026-08-14T00:00:00Z",
          notes: null,
          created_at: "2026-08-14T00:00:00Z",
        },
      ],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useMeasurementTrend: () => ({
    data: { buckets: [] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));

import MeasurementChart from "./MeasurementChart";

afterEach(cleanup);

describe("MeasurementChart chart-series contract", () => {
  it("passes direct theme variables to Recharts SVG strokes and keeps the domain spec on that path", () => {
    render(
      <MemoryRouter initialEntries={["/health/measurements?type=blood_pressure"]}>
        <MeasurementChart />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("measurement-chart-line-systolic").getAttribute("data-stroke")).toBe(
      "var(--chart-1)",
    );
    expect(screen.getByTestId("measurement-chart-line-diastolic").getAttribute("data-stroke")).toBe(
      "var(--chart-2)",
    );
    expect(DOMAIN_SPEC).toMatch(
      /direct chart-series CSS custom-property reference `var\(--chart-1\)` passed to the Recharts\s+SVG `stroke` prop/,
    );
    expect(DOMAIN_SPEC).toMatch(
      /Chromium resolves that\s+CSS custom property in the SVG presentation attribute at paint time/,
    );
    expect(DOMAIN_SPEC).not.toContain("bridged to a literal color for recharts via a read");
  });
});

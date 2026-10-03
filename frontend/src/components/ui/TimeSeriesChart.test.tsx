// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DAY_MS, buildTimeSeries } from "@/lib/time-series";
import { TimeSeriesChart } from "./TimeSeriesChart";

vi.mock("recharts", () => {
  const Wrap = ({ children }: { children?: ReactNode }) => createElement("div", null, children);
  return {
    LineChart: Wrap,
    AreaChart: Wrap,
    ResponsiveContainer: Wrap,
    CartesianGrid: () => null,
    Tooltip: () => null,
    YAxis: () => null,
    ReferenceArea: () => createElement("i", { "data-testid": "stale-tail-area" }),
    XAxis: (p: { type?: string; scale?: string; domain?: number[] }) =>
      createElement("i", {
        "data-testid": "x-axis",
        "data-type": p.type,
        "data-scale": p.scale,
        "data-domain": JSON.stringify(p.domain),
      }),
    Line: (p: { type?: string; dot?: boolean; connectNulls?: boolean }) =>
      createElement("i", {
        "data-testid": "line",
        "data-join": p.type,
        "data-dot": String(p.dot),
        "data-connect": String(p.connectNulls),
      }),
    Area: (p: { fill?: string }) => createElement("i", { "data-testid": "area", "data-fill": p.fill }),
  };
});

const now = Date.UTC(2026, 2, 10);
const lines = [{ key: "v", name: "V", stroke: "var(--chart-1)" }];

function seriesOf(daysAgo: number[]) {
  return buildTimeSeries(
    daysAgo.map((d, i) => ({ at: now - d * DAY_MS, values: { v: i } })),
    ["v"],
    { windowEnd: now, maxGapMs: 7 * DAY_MS },
  );
}

describe("TimeSeriesChart", () => {
  afterEach(cleanup);

  it("draws a time axis ending at now with linear joins, marks, and no gap bridging", () => {
    render(<TimeSeriesChart series={seriesOf([69, 9, 8])} lines={lines} height={80} />);
    const axis = screen.getByTestId("x-axis");
    expect(axis.dataset.type).toBe("number");
    expect(axis.dataset.scale).toBe("time");
    expect(JSON.parse(axis.dataset.domain!)[1]).toBe(now);
    const line = screen.getByTestId("line");
    expect(line.dataset.join).toBe("linear");
    expect(line.dataset.dot).toBe("true");
    expect(line.dataset.connect).toBe("false");
  });

  it("labels and shades the tail when the last reading is over a day old", () => {
    render(<TimeSeriesChart series={seriesOf([9, 8])} lines={lines} height={80} />);
    expect(screen.getByTestId("time-series-tail").textContent).toBe("last reading 8d ago");
    expect(screen.getByTestId("stale-tail-area")).toBeTruthy();
  });

  it("shows no tail for a fresh series and drops marks past 40 points", () => {
    const fresh = seriesOf(Array.from({ length: 41 }, (_, i) => 0.5 + (40 - i) / 100));
    render(<TimeSeriesChart series={fresh} lines={lines} height={80} />);
    expect(screen.queryByTestId("time-series-tail")).toBeNull();
    expect(screen.getByTestId("line").dataset.dot).toBe("false");
  });

  it("fades the area variant with a per-instance gradient, not a flat fill", () => {
    const { container } = render(
      <>
        <TimeSeriesChart series={seriesOf([2, 1])} lines={lines} variant="area" height={80} />
        <TimeSeriesChart series={seriesOf([2, 1])} lines={lines} variant="area" height={80} />
      </>,
    );
    const fills = screen.getAllByTestId("area").map((a) => a.dataset.fill!);
    expect(fills[0]).toMatch(/^url\(#ts-fade-[^:)]+-v\)$/);
    expect(fills[0]).not.toBe(fills[1]);
    const gradient = container.querySelector(`linearGradient[id="${fills[0].slice(5, -1)}"]`);
    const stops = [...gradient!.querySelectorAll("stop")].map((s) => s.getAttribute("stop-opacity"));
    expect(stops).toEqual(["0.3", "0"]);
  });
});

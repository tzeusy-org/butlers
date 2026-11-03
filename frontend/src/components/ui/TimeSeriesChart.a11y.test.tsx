// @vitest-environment jsdom
import { StrictMode, type ComponentProps } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildTimeSeries, DAY_MS, type TimeSeriesInput } from "@/lib/time-series";
import { AppTimezoneProvider } from "./timezone-context";
import { TimeSeriesChart } from "./TimeSeriesChart";

const now = Date.UTC(2026, 2, 10);
const lines = [{ key: "v", name: "V", stroke: "#345678" }];
type ChartProps = ComponentProps<typeof TimeSeriesChart>;
const bounds = { x: 0, y: 0, left: 0, top: 0, right: 640, bottom: 240, width: 640, height: 240, toJSON: () => ({}) };

// Only the missing browser layout boundary is supplied. Line/Area, their
// dot renderers, axes, keyboard layer and SVG all come from real Recharts.
class LayoutObserver implements ResizeObserver {
  private callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }
  observe(target: Element) {
    this.callback([{ target, contentRect: bounds } as ResizeObserverEntry], this);
  }
  unobserve() {}
  disconnect() {}
}

function series(points: TimeSeriesInput<string>[], end = now, start?: number) {
  return buildTimeSeries(points, ["v", "other"], { windowEnd: end, windowStart: start, maxGapMs: 7 * DAY_MS });
}

function chart(props: Partial<ChartProps> = {}, timezone = "Asia/Singapore") {
  return (
    <AppTimezoneProvider timezone={timezone}>
      <TimeSeriesChart
        series={series([{ at: now - 8 * DAY_MS, values: { v: 0 } }])}
        lines={lines}
        height={240}
        testId="chart"
        {...props}
      />
    </AppTimezoneProvider>
  );
}

function staticMarks(container: HTMLElement, variant: "line" | "area") {
  return [...container.querySelectorAll<SVGCircleElement>(`.recharts-${variant}-dots circle`)];
}

describe("TimeSeriesChart accessible observations", () => {
  beforeEach(() => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(bounds);
    vi.stubGlobal("ResizeObserver", LayoutObserver);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("names real line/area marks without changing geometry, gaps, thresholds or keyboard glyphs", async () => {
    for (const variant of ["line", "area"] as const) {
      const irregular = series([
        { at: Date.UTC(2025, 11, 31), values: { v: 900 } },
        { at: "invalid", values: { v: 900 } },
        { at: Date.UTC(2026, 0, 1), values: { v: 0 } },
        { at: Date.UTC(2026, 2, 1), values: { v: -2 } },
        { at: Date.UTC(2026, 2, 2), values: { v: 4 } },
        { at: now + DAY_MS, values: { v: 900 } },
      ], now, Date.UTC(2026, 0, 1));
      const view = render(chart({ series: irregular, variant, yUnit: "kg" }));
      await waitFor(() => expect(staticMarks(view.container, variant)).toHaveLength(3));
      const names = [
        "V, 0 kg, 2026-01-01 08:00:00 +08:00",
        "V, -2 kg, 2026-03-01 08:00:00 +08:00",
        "V, 4 kg, 2026-03-02 08:00:00 +08:00",
      ];
      const marks = names.map((name) => within(view.container).getByRole("img", { name }));
      for (const mark of marks) {
        expect(mark.tagName.toLowerCase()).toBe("circle");
        expect(mark.querySelector("title")?.textContent).toBe(mark.getAttribute("aria-label"));
        expect(mark.hasAttribute("tabindex")).toBe(false);
        expect(Number(mark.getAttribute("r"))).toBe(3);
        expect(Number.isFinite(Number(mark.getAttribute("cy")))).toBe(true);
      }
      const xs = marks.map((mark) => Number(mark.getAttribute("cx")));
      expect((xs[1] - xs[0]) / (xs[2] - xs[1])).toBeCloseTo(59, 5);
      const path = view.container.querySelector(`.recharts-${variant}-curve`)!;
      expect(path.getAttribute("d")?.match(/M/g)).toHaveLength(2);
      expect(path.getAttribute("d")).not.toMatch(/[CQ]/);
      expect(within(view.container).queryByRole("img", { name: /900/ })).toBeNull();
      view.unmount();

      const twoSeries = series([
        { at: now - DAY_MS, values: { v: 0, other: null, private_hint: 9999 } },
        { at: now, values: { v: 2, other: -1, private_hint: 9999 } },
      ]);
      const multi = render(chart({ series: twoSeries, variant, compact: true, join: "step", lines: [...lines, { key: "other", name: "Other", stroke: "#765432" }] }));
      expect(within(multi.container).getAllByRole("img")).toHaveLength(3);
      expect(within(multi.container).getByRole("img", { name: "Other, -1, 2026-03-10 08:00:00 +08:00" })).toBeDefined();
      expect(multi.container.textContent).not.toContain("9999");
      expect(multi.container.querySelector(`.recharts-${variant}-curve`)?.getAttribute("d")?.match(/L/g)?.length).toBeGreaterThan(1);
      multi.unmount();

      for (const count of [1, 40, 41]) {
        const dense = series(Array.from({ length: count }, (_, i) => ({ at: now - (count - i) * 60_000, values: { v: i } })));
        const view = render(chart({ series: dense, variant, compact: true }));
        expect(staticMarks(view.container, variant)).toHaveLength(count <= 40 ? count : 0);
        expect(within(view.container).queryAllByRole("img")).toHaveLength(count <= 40 ? count : 0);
        const surface = within(view.container).getByRole("application");
        fireEvent.focus(surface);
        fireEvent.keyDown(surface, { key: "ArrowRight" });
        await waitFor(() => expect(view.container.querySelector(".recharts-active-dot circle")).not.toBeNull());
        const active = view.container.querySelector(".recharts-active-dot circle")!;
        expect(Number(active.getAttribute("r"))).toBe(4);
        if (count <= 40) {
          expect(active.getAttribute("aria-hidden")).toBe("true");
          expect(within(view.container).getAllByRole("img")).toHaveLength(count);
        } else {
          expect(active.getAttribute("role")).toBe("img");
          expect(within(view.container).getByRole("img", { name: /V, \d+, 2026-03-10 07:\d\d:00 \+08:00/ })).toBe(active);
        }
        view.unmount();
      }
    }

    const dst = series([
      { at: "2026-11-01T05:30:00Z", values: { v: 0 } },
      { at: "2026-11-01T06:30:00Z", values: { v: -1 } },
    ], Date.parse("2026-11-01T07:00:00Z"));
    const local = render(chart({ series: dst }, "America/New_York"));
    expect(screen.getByRole("img", { name: "V, 0, 2026-11-01 01:30:00 -04:00" })).toBeDefined();
    expect(screen.getByRole("img", { name: "V, -1, 2026-11-01 01:30:00 -05:00" })).toBeDefined();
    local.rerender(chart({ series: dst }, "UTC"));
    expect(screen.getByRole("img", { name: "V, -1, 2026-11-01 06:30:00 Z" })).toBeDefined();
    expect(screen.queryByRole("img", { name: /-05:00/ })).toBeNull();
    local.unmount();

    const escapedName = "<script>synthetic display name</script>";
    const escaped = render(chart({ lines: [{ ...lines[0], name: escapedName }], yUnit: "<kg>" }));
    expect(screen.getByRole("img", { name: "<script>synthetic display name</script>, 0 <kg>, 2026-03-02 08:00:00 +08:00" })).toBeDefined();
    expect(escaped.container.querySelector("script")).toBeNull();
    escaped.unmount();

    for (const value of [null, Number.NaN, Number.POSITIVE_INFINITY]) {
      const invalid = render(chart({
        series: series([{ at: now, values: { v: value } }], now, now - DAY_MS),
        compact: true,
        yDomain: [0, 10],
      }));
      expect(screen.queryAllByRole("img")).toHaveLength(0);
      invalid.unmount();
    }
  });

  it("associates the visible stale note with its own chart through refresh, independent instances and StrictMode", () => {
    const view = render(<StrictMode>{chart({ testId: "left" })}{chart({ testId: "right" })}</StrictMode>);
    const groups = screen.getAllByRole("group", { name: "Trend observations: V", description: "last reading 8d ago" });
    const notes = screen.getAllByRole("note", { name: "Stale trend tail" });
    expect(groups).toHaveLength(2);
    expect(notes.map((note) => note.textContent)).toEqual(["last reading 8d ago", "last reading 8d ago"]);
    expect(new Set(notes.map((note) => note.id)).size).toBe(2);
    for (const [index, group] of groups.entries()) {
      expect(group.getAttribute("aria-describedby")).toBe(notes[index].id);
      expect(group.contains(notes[index])).toBe(true);
      expect(group.querySelector(".recharts-reference-area-rect")?.getAttribute("aria-hidden")).toBe("true");
    }
    const fresh = series([{ at: now - 0.5 * DAY_MS, values: { v: 3 } }]);
    view.rerender(<StrictMode>{chart({ series: fresh })}</StrictMode>);
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.getByRole("img", { name: "V, 3, 2026-03-09 20:00:00 +08:00" })).toBeDefined();
    expect(screen.queryByRole("img", { name: "V, 0, 2026-03-02 08:00:00 +08:00" })).toBeNull();
    expect(screen.getByRole("group", { name: "Trend observations: V" }).hasAttribute("aria-describedby")).toBe(false);
    expect(screen.getByRole("group").hasAttribute("aria-live")).toBe(false);
    view.rerender(<StrictMode>{chart({ series: series([{ at: now - DAY_MS, values: { v: 3 } }]) })}</StrictMode>);
    expect(screen.getByRole("group", { description: "last reading 1d ago" })).toBeDefined();
    expect(screen.getByRole("note").textContent).toBe("last reading 1d ago");
    view.rerender(<StrictMode>{chart({ series: series([], now, now - DAY_MS) })}</StrictMode>);
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(screen.getByRole("group").hasAttribute("aria-describedby")).toBe(false);
    view.unmount();
    expect(screen.queryByRole("group")).toBeNull();
  });
});

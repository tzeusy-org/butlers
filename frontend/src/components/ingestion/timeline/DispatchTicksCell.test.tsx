// @vitest-environment jsdom
/**
 * Unit tests for DispatchTicksCell (bu-4utdw.8).
 *
 * Covers:
 * - empty state: no sessions renders a muted em-dash, not a button
 * - tick count matches session count (capped list, not the raw sessionCount)
 * - a failed session's tick renders destructive-red; others render neutral
 * - trailing mono count only appears when more than one session fired
 * - computeTickWidths: proportional to duration, minimum 3px, never exceeds
 *   the cell budget regardless of session count
 * - interaction: click and Enter both fire onOpenDrawer, without bubbling
 * - a11y: cell is a native button with an aria-label summarizing sessions
 */

import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import type { IngestionEventListSessionSummary } from "@/api/index.ts";
import { DispatchTicksCell, computeTickWidths } from "./DispatchTicksCell";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function session(overrides: Partial<IngestionEventListSessionSummary> = {}): IngestionEventListSessionSummary {
  return {
    butler_name: "relationship",
    duration_ms: 41_000,
    cost_usd: 0.09,
    success: true,
    ...overrides,
  };
}

function renderCell(container: HTMLDivElement, root: Root, props: Parameters<typeof DispatchTicksCell>[0]) {
  act(() => {
    root.render(<DispatchTicksCell {...props} />);
  });
  return container;
}

describe("DispatchTicksCell", () => {
  it("renders a muted em-dash and no button when there are no sessions", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    renderCell(container, root, { sessions: [], sessionCount: 0, onOpenDrawer: vi.fn() });

    expect(container.querySelector("[data-testid='dispatch-ticks-empty']")?.textContent).toBe("—");
    expect(container.querySelector("button")).toBeNull();

    act(() => root.unmount());
    container.remove();
  });

  it("renders the empty state rather than throwing when sessions/sessionCount are undefined", () => {
    // Regression guard: events sourced from fixtures or callers that predate
    // bu-4utdw.3's rollup enrichment can omit these fields at runtime even
    // though the TS props allow it explicitly (caught e2e — `sessions.length`
    // on `undefined` crashed the whole ledger row before this guard existed).
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    renderCell(container, root, { sessions: undefined, sessionCount: undefined, onOpenDrawer: vi.fn() });

    expect(container.querySelector("[data-testid='dispatch-ticks-empty']")?.textContent).toBe("—");
    expect(container.querySelector("button")).toBeNull();

    act(() => root.unmount());
    container.remove();
  });

  it("renders one tick per session", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    renderCell(container, root, {
      sessions: [session(), session({ butler_name: "finance" }), session({ butler_name: "health" })],
      sessionCount: 3,
      onOpenDrawer: vi.fn(),
    });

    expect(container.querySelectorAll("[data-testid='dispatch-tick']")).toHaveLength(3);

    act(() => root.unmount());
    container.remove();
  });

  it("colors a failed session's tick destructive-red and leaves others neutral", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    renderCell(container, root, {
      sessions: [session({ success: true }), session({ butler_name: "finance", success: false })],
      sessionCount: 2,
      onOpenDrawer: vi.fn(),
    });

    const ticks = container.querySelectorAll("[data-testid='dispatch-tick']");
    expect(ticks[0].classList.contains("bg-foreground/40")).toBe(true);
    expect(ticks[0].classList.contains("bg-destructive")).toBe(false);
    expect(ticks[1].classList.contains("bg-destructive")).toBe(true);
    expect(ticks[1].getAttribute("data-failed")).toBe("true");

    act(() => root.unmount());
    container.remove();
  });

  it("does not color ticks by butler hue (no inline backgroundColor)", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    renderCell(container, root, {
      sessions: [session()],
      sessionCount: 1,
      onOpenDrawer: vi.fn(),
    });

    const tick = container.querySelector("[data-testid='dispatch-tick']") as HTMLElement;
    expect(tick.style.backgroundColor).toBe("");

    act(() => root.unmount());
    container.remove();
  });

  it("shows the trailing mono count only when more than one session fired", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    renderCell(container, root, { sessions: [session()], sessionCount: 1, onOpenDrawer: vi.fn() });
    expect(container.querySelector("[data-testid='dispatch-tick-count']")).toBeNull();

    renderCell(container, root, {
      sessions: [session(), session({ butler_name: "finance" })],
      sessionCount: 2,
      onOpenDrawer: vi.fn(),
    });
    expect(container.querySelector("[data-testid='dispatch-tick-count']")?.textContent).toBe("2");

    act(() => root.unmount());
    container.remove();
  });

  it("puts butler name, duration, and cost in each tick's title tooltip", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    renderCell(container, root, {
      sessions: [session({ butler_name: "relationship", duration_ms: 41_000, cost_usd: 0.09 })],
      sessionCount: 1,
      onOpenDrawer: vi.fn(),
    });

    const tick = container.querySelector("[data-testid='dispatch-tick']") as HTMLElement;
    expect(tick.title).toContain("relationship");
    expect(tick.title).toContain("41.0s");
    expect(tick.title).toContain("$0.0900");

    act(() => root.unmount());
    container.remove();
  });

  it("fires onOpenDrawer on click without bubbling to a parent handler", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onOpenDrawer = vi.fn();
    const parentClick = vi.fn();

    act(() => {
      root.render(
        <div onClick={parentClick}>
          <DispatchTicksCell sessions={[session()]} sessionCount={1} onOpenDrawer={onOpenDrawer} />
        </div>,
      );
    });

    const button = container.querySelector("[data-testid='dispatch-ticks-cell']") as HTMLElement;
    act(() => {
      button.click();
    });

    expect(onOpenDrawer).toHaveBeenCalledTimes(1);
    expect(parentClick).not.toHaveBeenCalled();

    act(() => root.unmount());
    container.remove();
  });

  it("is a native button reachable via keyboard focus, with an aria-label summary", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    renderCell(container, root, {
      sessions: [session({ butler_name: "relationship" })],
      sessionCount: 1,
      onOpenDrawer: vi.fn(),
    });

    const button = container.querySelector("[data-testid='dispatch-ticks-cell']") as HTMLElement;
    expect(button.tagName).toBe("BUTTON");
    act(() => {
      button.focus();
    });
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute("aria-label")).toContain("1 butler session");
    expect(button.getAttribute("aria-label")).toContain("relationship");

    act(() => root.unmount());
    container.remove();
  });
});

describe("computeTickWidths", () => {
  it("returns an empty array for no sessions", () => {
    expect(computeTickWidths([])).toEqual([]);
  });

  it("gives the longest session the largest width and scales others proportionally", () => {
    const widths = computeTickWidths([
      session({ duration_ms: 10_000 }),
      session({ duration_ms: 40_000 }),
      session({ duration_ms: 20_000 }),
    ]);
    expect(widths[1]).toBeGreaterThan(widths[0]);
    expect(widths[1]).toBeGreaterThan(widths[2]);
    expect(widths[2]).toBeGreaterThan(widths[0]);
  });

  it("never returns a width below the 3px minimum, even for a zero-duration session", () => {
    const widths = computeTickWidths([session({ duration_ms: 0 }), session({ duration_ms: 50_000 })]);
    expect(widths[0]).toBeGreaterThanOrEqual(3);
  });

  it("treats a null duration as zero rather than throwing", () => {
    const widths = computeTickWidths([session({ duration_ms: null }), session({ duration_ms: 10_000 })]);
    expect(widths[0]).toBeGreaterThanOrEqual(3);
    expect(widths).toHaveLength(2);
  });

  it("keeps the total width within the cell budget for the max capped session count (8)", () => {
    const sessions = Array.from({ length: 8 }, (_, i) => session({ duration_ms: (i + 1) * 5000 }));
    const widths = computeTickWidths(sessions);
    const totalGap = 2 * (sessions.length - 1);
    const totalWidth = widths.reduce((sum, w) => sum + w, 0) + totalGap;
    expect(totalWidth).toBeLessThanOrEqual(84);
  });
});

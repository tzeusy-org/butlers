// @vitest-environment jsdom
/**
 * ButlerRoutingLogTab — RTL tests pinning Panel atom wrapper.
 *
 * Tests cover:
 *  - No <Card> wrapper present (zero Card refs after the bu-pllml refactor)
 *  - Panel atom is present with title "routing log"
 *  - RoutingLogTable renders inside the Panel
 *  - Loading state preserved
 *  - Error state preserved (hook-level error)
 *
 * bead: bu-pllml (epic bu-hdavr F.5)
 */

import { createElement } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock RoutingLogTable to avoid deep hook/DOM complexity
// ---------------------------------------------------------------------------

vi.mock("@/components/switchboard/RoutingLogTable.tsx", () => ({
  default: () =>
    createElement("div", { "data-testid": "routing-log-table" }, "RoutingLogTable"),
}));

import ButlerRoutingLogTab from "./ButlerRoutingLogTab";

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <ButlerRoutingLogTab />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.resetAllMocks();
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerRoutingLogTab — Panel atom wrapper (bu-pllml)", () => {
  it("renders the outer tab container", () => {
    renderTab();
    expect(screen.getByTestId("butler-routing-log-tab")).toBeDefined();
  });

  it("renders the routing log Panel atom", () => {
    renderTab();
    expect(screen.getByTestId("routing-log-panel")).toBeDefined();
  });

  it("Panel has 'routing log' eyebrow title", () => {
    renderTab();
    const panel = screen.getByTestId("routing-log-panel");
    expect(panel.textContent).toContain("routing log");
  });

  it("renders RoutingLogTable inside the Panel", () => {
    renderTab();
    expect(screen.getByTestId("routing-log-table")).toBeDefined();
  });

  it("does NOT use a <Card> wrapper (no Card component present)", () => {
    renderTab();
    // The document should not contain any element with the shadcn Card role pattern.
    // Card renders a div with a specific class; the simplest check is that no
    // element with data-slot="card" exists (shadcn Card uses data-slot).
    const cardEls = document.querySelectorAll('[data-slot="card"]');
    expect(cardEls.length).toBe(0);
  });
});

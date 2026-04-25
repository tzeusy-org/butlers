// @vitest-environment jsdom

/**
 * Smoke tests for ChroniclesPage shell (bu-ig72b.4).
 *
 * Verifies:
 *   - ChroniclesPage renders its heading and three labelled widget regions.
 *   - /timeline route still renders TimelinePage (regression guard).
 */

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router";

import ChroniclesPage from "@/pages/ChroniclesPage";

// ---------------------------------------------------------------------------
// Mocks needed for SourceStateBadgeStrip (uses TanStack Query)
// ---------------------------------------------------------------------------
vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesSourceState: () => ({ data: undefined, isLoading: false, isError: false }),
}));

// ---------------------------------------------------------------------------
// Mocks needed for TimelinePage (it pulls several hooks and components)
// ---------------------------------------------------------------------------
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: () => ({ data: undefined }),
}));
vi.mock("@/hooks/use-timeline", () => ({
  useTimeline: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("@/hooks/use-auto-refresh", () => ({
  useAutoRefresh: () => ({
    enabled: false,
    interval: 10000,
    refetchInterval: false,
    setEnabled: vi.fn(),
    setInterval: vi.fn(),
  }),
}));
vi.mock("@/components/ui/auto-refresh-toggle", () => ({
  AutoRefreshToggle: () => null,
}));
vi.mock("@/components/timeline/UnifiedTimeline", () => ({
  default: () => <div data-testid="unified-timeline-stub" />,
}));

// TimelinePage import (for the regression guard below)
import TimelinePage from "@/pages/TimelinePage";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderChroniclesPage(): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/chronicles"]}>
      <Routes>
        <Route path="/chronicles" element={<ChroniclesPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderTimelinePage(): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/timeline"]}>
      <Routes>
        <Route path="/timeline" element={<TimelinePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// ChroniclesPage
// ---------------------------------------------------------------------------

describe("ChroniclesPage", () => {
  it("renders the Chronicles page heading", () => {
    const html = renderChroniclesPage();
    expect(html).toContain("Chronicles");
  });

  it("renders the Gantt area widget region", () => {
    const html = renderChroniclesPage();
    expect(html).toContain("Gantt area");
  });

  it("renders the Map area widget region", () => {
    const html = renderChroniclesPage();
    expect(html).toContain("Map area");
  });

  it("renders the Aggregations area widget region", () => {
    const html = renderChroniclesPage();
    expect(html).toContain("Aggregations area");
  });
});

// ---------------------------------------------------------------------------
// TimelinePage regression guard — /timeline must still render TimelinePage
// ---------------------------------------------------------------------------

describe("TimelinePage smoke", () => {
  it("renders the Timeline page heading", () => {
    const html = renderTimelinePage();
    expect(html).toContain("Timeline");
  });

  it("does not render Chronicles heading text when at /timeline", () => {
    const html = renderTimelinePage();
    // TimelinePage has an h1 ("Timeline") but it must not say "Chronicles"
    expect(html).not.toContain(">Chronicles<");
  });
});

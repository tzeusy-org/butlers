// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";

import IngestionPage from "@/pages/IngestionPage";

// Mock tab content components so IngestionPage tab-routing tests do not
// require a QueryClientProvider. Behavioral tests for each tab component
// live in their own test files.
vi.mock("@/components/switchboard/BackfillHistoryTab", () => ({
  BackfillHistoryTab: () => <div data-testid="backfill-history-tab-stub">History stub</div>,
}));
vi.mock("@/components/ingestion/OverviewTab", () => ({
  OverviewTab: ({ isActive }: { isActive: boolean }) => (
    <div data-testid="overview-tab-stub" data-active={String(isActive)}>
      Overview stub
    </div>
  ),
}));
vi.mock("@/components/ingestion/ConnectorsTab", () => ({
  ConnectorsTab: ({ isActive }: { isActive: boolean }) => (
    <div data-testid="connectors-tab-stub" data-active={String(isActive)}>
      Connectors stub
    </div>
  ),
}));
// Mock FiltersTab so IngestionPage tab-routing tests do not
// require a QueryClientProvider (that concern belongs to FiltersTab.test.tsx).
vi.mock("@/components/switchboard/FiltersTab", () => ({
  FiltersTab: () => <div data-testid="filters-tab-stub">Filters stub</div>,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("IngestionPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  function render(initialPath = "/ingestion") {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/ingestion" element={<IngestionPage />} />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  it("renders the Ingestion page heading", () => {
    render();
    expect(container.querySelector("h1")?.textContent).toBe("Ingestion");
  });

  it("renders four tab triggers: Overview, Connectors, Filters, History", () => {
    render();
    const triggers = container.querySelectorAll('[role="tab"]');
    const labels = Array.from(triggers).map((t) => t.textContent?.trim());
    expect(labels).toContain("Overview");
    expect(labels).toContain("Connectors");
    expect(labels).toContain("Filters");
    expect(labels).toContain("History");
  });

  it("defaults to Overview tab when no ?tab param is present", () => {
    render("/ingestion");
    const activeTab = container.querySelector('[role="tab"][data-state="active"]');
    expect(activeTab?.textContent?.trim()).toBe("Overview");
  });

  it("activates Connectors tab when ?tab=connectors is in the URL", () => {
    render("/ingestion?tab=connectors");
    const activeTab = container.querySelector('[role="tab"][data-state="active"]');
    expect(activeTab?.textContent?.trim()).toBe("Connectors");
  });

  it("activates Filters tab when ?tab=filters is in the URL", () => {
    render("/ingestion?tab=filters");
    const activeTab = container.querySelector('[role="tab"][data-state="active"]');
    expect(activeTab?.textContent?.trim()).toBe("Filters");
  });

  it("activates History tab when ?tab=history is in the URL", () => {
    render("/ingestion?tab=history");
    const activeTab = container.querySelector('[role="tab"][data-state="active"]');
    expect(activeTab?.textContent?.trim()).toBe("History");
  });

  it("falls back to Overview tab for unknown ?tab values", () => {
    render("/ingestion?tab=unknown-garbage");
    const activeTab = container.querySelector('[role="tab"][data-state="active"]');
    expect(activeTab?.textContent?.trim()).toBe("Overview");
  });

  it("renders overview tab stub content by default", () => {
    render("/ingestion");
    const stub = container.querySelector('[data-testid="overview-tab-stub"]');
    expect(stub).not.toBeNull();
  });

  it("renders connectors tab stub when ?tab=connectors", () => {
    render("/ingestion?tab=connectors");
    const stub = container.querySelector('[data-testid="connectors-tab-stub"]');
    expect(stub).not.toBeNull();
  });
});

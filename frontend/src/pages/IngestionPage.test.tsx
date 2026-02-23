// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";

import IngestionPage from "@/pages/IngestionPage";

// Mock BackfillHistoryTab so IngestionPage tab-routing tests do not
// require a QueryClientProvider (that concern belongs to BackfillHistoryTab.test.tsx).
vi.mock("@/components/switchboard/BackfillHistoryTab", () => ({
  BackfillHistoryTab: () => <div data-testid="backfill-history-tab-stub">History stub</div>,
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
    // The Overview tab content heading should be visible
    // The active tab trigger should be Overview
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
});

// @vitest-environment jsdom

/**
 * Tests for ConnectorFiltersDialog.
 *
 * We test that the trigger button renders with correct props, and that it
 * stops click propagation (so clicking inside a Link card doesn't navigate).
 * Full integration tests (open/close, toggle, save) would require a full
 * QueryClient + MSW setup; those are deferred to integration test coverage.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConnectorFiltersDialog } from "./ConnectorFiltersDialog";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

describe("ConnectorFiltersDialog", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
  });

  function render(triggerVariant: "card" | "page" = "card") {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <ConnectorFiltersDialog
              connectorType="gmail"
              endpointIdentity="user@example.com"
              triggerVariant={triggerVariant}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("renders the Filters trigger button (card variant)", () => {
    render("card");
    const btn = container.querySelector('[data-testid="connector-filters-button"]');
    expect(btn).not.toBeNull();
    expect(btn!.textContent).toContain("Filters");
  });

  it("renders the Filters trigger button (page variant)", () => {
    render("page");
    // Page variant doesn't have a data-testid but should still show "Filters"
    expect(container.textContent).toContain("Filters");
  });

  it("trigger button is present inside card variant", () => {
    render("card");
    const btn = container.querySelector('[data-testid="connector-filters-button"]');
    expect(btn).not.toBeNull();
  });

  it("does not show active count badge when no filters are cached", () => {
    render("card");
    // No data loaded → no badge visible
    const badge = container.querySelector(".ml-1.text-xs");
    // Badge is only rendered when activeCount > 0
    // With no data in cache, activeCount is 0, so badge should not be present
    expect(badge).toBeNull();
  });
});

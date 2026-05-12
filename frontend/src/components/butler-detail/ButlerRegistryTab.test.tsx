// @vitest-environment jsdom
/**
 * ButlerRegistryTab — RTL tests pinning Panel atom wrapper.
 *
 * Tests cover:
 *  - No <Card> wrapper present (zero Card refs after the bu-b9jpn refactor)
 *  - Panel atom is present with title "butler registry"
 *  - RegistryTable renders inside the Panel
 *  - Outer tab container is present
 *  - Panel has correct testId
 *
 * bead: bu-b9jpn (epic bu-hdavr F.6)
 */

import { createElement } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock RegistryTable to avoid deep hook/DOM complexity
// ---------------------------------------------------------------------------

vi.mock("@/components/switchboard/RegistryTable.tsx", () => ({
  default: () =>
    createElement("div", { "data-testid": "registry-table" }, "RegistryTable"),
}));

import ButlerRegistryTab from "./ButlerRegistryTab";

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <ButlerRegistryTab />
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

describe("ButlerRegistryTab — Panel atom wrapper (bu-b9jpn)", () => {
  it("renders the outer tab container", () => {
    renderTab();
    expect(screen.getByTestId("butler-registry-tab")).toBeDefined();
  });

  it("renders the butler registry Panel atom", () => {
    renderTab();
    expect(screen.getByTestId("butler-registry-panel")).toBeDefined();
  });

  it("Panel has 'butler registry' eyebrow title", () => {
    renderTab();
    const panel = screen.getByTestId("butler-registry-panel");
    expect(panel.textContent).toContain("butler registry");
  });

  it("renders RegistryTable inside the Panel", () => {
    renderTab();
    expect(screen.getByTestId("registry-table")).toBeDefined();
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

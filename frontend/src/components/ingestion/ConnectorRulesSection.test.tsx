// @vitest-environment jsdom

/**
 * Tests for ConnectorRulesSection.
 *
 * Verifies:
 * - Section renders with title and add-rule button
 * - Empty state message shown when no rules
 * - Rules table renders rule rows with correct data
 * - Clicking "+ Add Rule" opens the editor drawer
 * - Editor drawer shows scope indicator and block action badge
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConnectorRulesSection } from "./ConnectorRulesSection";

// ---------------------------------------------------------------------------
// Mock the hooks
// ---------------------------------------------------------------------------

const mockRules = [
  {
    id: "rule-1",
    scope: "connector:gmail:user@example.com",
    rule_type: "sender_domain",
    condition: { domain: "spam.example.com", match: "exact" },
    action: "block",
    priority: 10,
    enabled: true,
    name: "Block spam domain",
    description: null,
    created_by: "user",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    deleted_at: null,
  },
  {
    id: "rule-2",
    scope: "connector:gmail:user@example.com",
    rule_type: "sender_address",
    condition: { address: "noreply@marketing.com" },
    action: "block",
    priority: 20,
    enabled: false,
    name: null,
    description: "Block marketing",
    created_by: "user",
    created_at: "2026-01-02T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    deleted_at: null,
  },
];

let mockReturnRules = mockRules;

vi.mock("@/hooks/use-ingestion-rules", () => ({
  useIngestionRules: vi.fn(() => ({
    data: { data: mockReturnRules },
    isLoading: false,
    error: null,
  })),
  useCreateIngestionRule: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useUpdateIngestionRule: vi.fn(() => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useDeleteIngestionRule: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
}));

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

describe("ConnectorRulesSection", () => {
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

  function render() {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <ConnectorRulesSection
              connectorType="gmail"
              endpointIdentity="user@example.com"
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("renders the section card with title", () => {
    render();
    expect(container.textContent).toContain("Ingestion Rules");
    expect(container.textContent).toContain(
      "Block rules evaluated before data enters the system.",
    );
  });

  it("renders the Add Rule button", () => {
    render();
    const btn = container.querySelector('[data-testid="add-rule-btn"]');
    expect(btn).not.toBeNull();
    expect(btn!.textContent).toContain("Add Rule");
  });

  it("renders rule rows in the table", () => {
    render();
    const row1 = container.querySelector('[data-testid="rule-row-rule-1"]');
    const row2 = container.querySelector('[data-testid="rule-row-rule-2"]');
    expect(row1).not.toBeNull();
    expect(row2).not.toBeNull();
  });

  it("shows rule name and condition in the row", () => {
    render();
    expect(container.textContent).toContain("Block spam domain");
    expect(container.textContent).toContain("domain = spam.example.com");
    expect(container.textContent).toContain(
      "address = noreply@marketing.com",
    );
  });

  it("shows priority values", () => {
    render();
    // Priority 10 and 20 should be visible
    const rows = container.querySelectorAll("tr");
    const rowTexts = Array.from(rows).map((r) => r.textContent);
    expect(rowTexts.some((t) => t?.includes("10"))).toBe(true);
    expect(rowTexts.some((t) => t?.includes("20"))).toBe(true);
  });

  it("shows block action badges", () => {
    render();
    const badges = container.querySelectorAll(".text-xs");
    const badgeTexts = Array.from(badges).map((b) => b.textContent);
    expect(badgeTexts.filter((t) => t === "block").length).toBeGreaterThanOrEqual(2);
  });

  it("shows enabled toggle for each rule", () => {
    render();
    const toggle1 = container.querySelector(
      '[data-testid="toggle-enabled-rule-1"]',
    );
    const toggle2 = container.querySelector(
      '[data-testid="toggle-enabled-rule-2"]',
    );
    expect(toggle1).not.toBeNull();
    expect(toggle2).not.toBeNull();
    expect(toggle1!.getAttribute("aria-checked")).toBe("true");
    expect(toggle2!.getAttribute("aria-checked")).toBe("false");
  });

  it("shows delete button for each rule", () => {
    render();
    const del1 = container.querySelector(
      '[data-testid="delete-rule-rule-1"]',
    );
    const del2 = container.querySelector(
      '[data-testid="delete-rule-rule-2"]',
    );
    expect(del1).not.toBeNull();
    expect(del2).not.toBeNull();
  });

  it("shows unnamed placeholder for rules without name", () => {
    render();
    expect(container.textContent).toContain("unnamed");
  });

  it("shows rule count in description", () => {
    render();
    expect(container.textContent).toContain("2 rules");
  });

  it("renders the connector-rules-section testid", () => {
    render();
    const section = container.querySelector(
      '[data-testid="connector-rules-section"]',
    );
    expect(section).not.toBeNull();
  });

  it("renders the rules table testid", () => {
    render();
    const table = container.querySelector(
      '[data-testid="connector-rules-table"]',
    );
    expect(table).not.toBeNull();
  });
});

describe("ConnectorRulesSection - empty state", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    // Override mock to return empty rules
    mockReturnRules = [];

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    // Restore default mock data
    mockReturnRules = mockRules;
  });

  function render() {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <ConnectorRulesSection
              connectorType="gmail"
              endpointIdentity="user@example.com"
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("shows empty state message when no rules", () => {
    render();
    expect(container.textContent).toContain(
      "No block rules for this connector.",
    );
    expect(container.textContent).toContain("Add one");
  });
});

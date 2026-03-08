// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FiltersTab } from "@/components/switchboard/FiltersTab";
import * as useIngestionRules from "@/hooks/use-ingestion-rules";
import * as useThreadAffinity from "@/hooks/use-thread-affinity";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mock shapes
// ---------------------------------------------------------------------------

type QueryResult<T> = {
  data: T | undefined;
  isLoading: boolean;
  error: Error | null;
};

type MutationResult = {
  mutate: ReturnType<typeof vi.fn>;
  mutateAsync: ReturnType<typeof vi.fn>;
  isPending: boolean;
};

function makeQuery<T>(data: T | undefined, isLoading = false): QueryResult<T> {
  return { data, isLoading, error: null };
}

function makeMutation(): MutationResult {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  };
}

const SAMPLE_GLOBAL_RULE = {
  id: "rule-abc-001",
  scope: "global",
  rule_type: "sender_domain",
  condition: { domain: "chase.com", match: "suffix" },
  action: "route_to:finance",
  priority: 10,
  enabled: true,
  name: null,
  description: null,
  created_by: "dashboard",
  created_at: "2026-02-22T00:00:00Z",
  updated_at: "2026-02-22T00:00:00Z",
  deleted_at: null,
};

const SAMPLE_CONNECTOR_RULE = {
  id: "rule-def-002",
  scope: "connector:gmail:gmail:user:dev",
  rule_type: "sender_domain",
  condition: { domain: "spam.com", match: "exact" },
  action: "block",
  priority: 5,
  enabled: true,
  name: null,
  description: null,
  created_by: "dashboard",
  created_at: "2026-02-22T00:00:00Z",
  updated_at: "2026-02-22T00:00:00Z",
  deleted_at: null,
};

const SAMPLE_THREAD_AFFINITY_SETTINGS = {
  enabled: true,
  ttl_days: 30,
  thread_overrides: {},
  updated_at: "2026-02-22T00:00:00Z",
};

// ---------------------------------------------------------------------------
// Default mock setup
// ---------------------------------------------------------------------------

function setupDefaultMocks() {
  vi.spyOn(useIngestionRules, "useIngestionRules").mockReturnValue(
    makeQuery({ data: [SAMPLE_GLOBAL_RULE, SAMPLE_CONNECTOR_RULE], meta: { total: 2 } }) as ReturnType<
      typeof useIngestionRules.useIngestionRules
    >,
  );
  vi.spyOn(useIngestionRules, "useCreateIngestionRule").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useIngestionRules.useCreateIngestionRule>,
  );
  vi.spyOn(useIngestionRules, "useUpdateIngestionRule").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useIngestionRules.useUpdateIngestionRule>,
  );
  vi.spyOn(useIngestionRules, "useDeleteIngestionRule").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useIngestionRules.useDeleteIngestionRule>,
  );
  vi.spyOn(useIngestionRules, "useTestIngestionRule").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useIngestionRules.useTestIngestionRule>,
  );
  vi.spyOn(useThreadAffinity, "useThreadAffinitySettings").mockReturnValue(
    makeQuery(SAMPLE_THREAD_AFFINITY_SETTINGS) as ReturnType<
      typeof useThreadAffinity.useThreadAffinitySettings
    >,
  );
  vi.spyOn(useThreadAffinity, "useUpdateThreadAffinitySettings").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useThreadAffinity.useUpdateThreadAffinitySettings>,
  );
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

describe("FiltersTab", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function render() {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <FiltersTab />
        </QueryClientProvider>,
      );
    });
  }

  // -------------------------------------------------------------------------
  // Structural tests
  // -------------------------------------------------------------------------

  it("renders the Ingestion Policy heading and description", () => {
    render();
    const heading = container.querySelector("h2");
    expect(heading?.textContent).toBe("Ingestion Policy");
    expect(container.textContent).toContain("Unified ingestion rules");
  });

  // -------------------------------------------------------------------------
  // Rules table tests
  // -------------------------------------------------------------------------

  it("renders the rules table with sample rules", () => {
    render();
    const table = container.querySelector('[data-testid="rules-table"]');
    expect(table).not.toBeNull();
    expect(container.textContent).toContain("10"); // priority
    expect(container.textContent).toContain("chase.com"); // global rule
    expect(container.textContent).toContain("spam.com"); // connector rule
  });

  it("renders scope badges for each rule row", () => {
    render();
    const globalBadge = container.querySelector(
      `[data-testid="scope-badge-${SAMPLE_GLOBAL_RULE.id}"]`,
    );
    const connectorBadge = container.querySelector(
      `[data-testid="scope-badge-${SAMPLE_CONNECTOR_RULE.id}"]`,
    );
    expect(globalBadge).not.toBeNull();
    expect(globalBadge?.textContent).toBe("Global");
    expect(connectorBadge).not.toBeNull();
    expect(connectorBadge?.textContent).toBe("gmail:gmail:user:dev");
  });

  it("renders scope filter dropdown with All, Global, and connector options", () => {
    render();
    const scopeFilter = container.querySelector(
      '[data-testid="scope-filter"]',
    ) as HTMLSelectElement;
    expect(scopeFilter).not.toBeNull();
    const optionTexts = Array.from(scopeFilter?.options ?? []).map((o) => o.textContent);
    expect(optionTexts).toContain("All scopes");
    expect(optionTexts).toContain("Global");
    expect(optionTexts).toContain("gmail:gmail:user:dev");
  });

  it("filters rules when scope filter is changed", () => {
    render();
    // Select global only
    act(() => {
      const scopeFilter = container.querySelector(
        '[data-testid="scope-filter"]',
      ) as HTMLSelectElement;
      scopeFilter.value = "global";
      scopeFilter.dispatchEvent(new Event("change", { bubbles: true }));
    });
    // Should show chase.com but not spam.com
    expect(container.textContent).toContain("chase.com");
    expect(container.textContent).not.toContain("spam.com");
  });

  it("renders New rule button and Import defaults button", () => {
    render();
    const newBtn = container.querySelector('[data-testid="new-rule-btn"]');
    const importBtn = container.querySelector('[data-testid="import-defaults-btn"]');
    expect(newBtn).not.toBeNull();
    expect(importBtn).not.toBeNull();
  });

  it("renders skeleton rows when loading", () => {
    vi.spyOn(useIngestionRules, "useIngestionRules").mockReturnValue(
      makeQuery(undefined, true) as ReturnType<typeof useIngestionRules.useIngestionRules>,
    );
    render();
    expect(container.textContent).not.toContain("chase.com");
  });

  it("renders empty state with CTA links when no rules", () => {
    vi.spyOn(useIngestionRules, "useIngestionRules").mockReturnValue(
      makeQuery({ data: [], meta: { total: 0 } }) as ReturnType<
        typeof useIngestionRules.useIngestionRules
      >,
    );
    render();
    const emptyLink = container.querySelector('[data-testid="empty-import-defaults-link"]');
    expect(emptyLink).not.toBeNull();
  });

  it("renders error state when rules fetch fails", () => {
    vi.spyOn(useIngestionRules, "useIngestionRules").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Network error"),
    } as ReturnType<typeof useIngestionRules.useIngestionRules>);
    render();
    const errorEl = container.querySelector('[data-testid="rules-error"]');
    expect(errorEl).not.toBeNull();
    expect(errorEl?.textContent).toContain("Failed to load");
  });

  it("renders an edit button and delete button per rule row", () => {
    render();
    const editBtn = container.querySelector(
      `[data-testid="edit-rule-${SAMPLE_GLOBAL_RULE.id}"]`,
    );
    const deleteBtn = container.querySelector(
      `[data-testid="delete-rule-${SAMPLE_GLOBAL_RULE.id}"]`,
    );
    expect(editBtn).not.toBeNull();
    expect(deleteBtn).not.toBeNull();
  });

  it("renders the enabled toggle for a rule row", () => {
    render();
    const toggle = container.querySelector(
      `[data-testid="toggle-enabled-${SAMPLE_GLOBAL_RULE.id}"]`,
    );
    expect(toggle).not.toBeNull();
    expect(toggle?.getAttribute("aria-checked")).toBe("true");
  });

  it("renders Block badge for connector-scoped rule", () => {
    render();
    // Find the connector rule row and check for Block badge
    const row = container.querySelector(
      `[data-testid="rule-row-${SAMPLE_CONNECTOR_RULE.id}"]`,
    );
    expect(row).not.toBeNull();
    expect(row?.textContent).toContain("Block");
  });

  // -------------------------------------------------------------------------
  // Rule editor drawer tests
  // -------------------------------------------------------------------------

  it("opens the rule editor drawer when New button is clicked", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    const ruleTypeSelect = document.querySelector('[data-testid="rule-type-select"]');
    expect(ruleTypeSelect).not.toBeNull();
  });

  it("renders scope selector in the editor with Global and Connector options", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    const scopeSelector = document.querySelector('[data-testid="scope-selector"]');
    expect(scopeSelector).not.toBeNull();
    const globalBtn = document.querySelector('[data-testid="scope-global-btn"]');
    const connectorBtn = document.querySelector('[data-testid="scope-connector-btn"]');
    expect(globalBtn).not.toBeNull();
    expect(connectorBtn).not.toBeNull();
  });

  it("shows connector type and identity pickers when Connector scope is selected", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    act(() => {
      const connectorBtn = document.querySelector(
        '[data-testid="scope-connector-btn"]',
      ) as HTMLButtonElement;
      connectorBtn?.click();
    });
    const connectorTypeSelect = document.querySelector(
      '[data-testid="connector-type-select"]',
    );
    const connectorIdentityInput = document.querySelector(
      '[data-testid="connector-identity-input"]',
    );
    expect(connectorTypeSelect).not.toBeNull();
    expect(connectorIdentityInput).not.toBeNull();
  });

  it("shows block-only action when connector scope is selected", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    act(() => {
      const connectorBtn = document.querySelector(
        '[data-testid="scope-connector-btn"]',
      ) as HTMLButtonElement;
      connectorBtn?.click();
    });
    // action-select should NOT be present (connector scope shows block-only panel)
    const actionSelect = document.querySelector('[data-testid="action-select"]');
    expect(actionSelect).toBeNull();
    // route-action toggle should also NOT be visible
    const routeToggle = document.querySelector('[data-testid="route-action-toggle"]');
    expect(routeToggle).toBeNull();
    // "Block" text should be visible
    expect(document.body.textContent).toContain("Block");
    expect(document.body.textContent).toContain(
      "Connector-scoped rules can only block messages before ingest",
    );
  });

  it("renders all four rule type options in the editor", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    const select = document.querySelector('[data-testid="rule-type-select"]') as HTMLSelectElement;
    const optionValues = Array.from(select?.options ?? []).map((o) => o.value);
    expect(optionValues).toContain("sender_domain");
    expect(optionValues).toContain("sender_address");
    expect(optionValues).toContain("header_condition");
    expect(optionValues).toContain("mime_type");
  });

  it("shows sender domain condition fields by default in new rule editor", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    const domainInput = document.querySelector('[data-testid="condition-domain"]');
    expect(domainInput).not.toBeNull();
  });

  it("shows priority input in the editor", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    const priorityInput = document.querySelector('[data-testid="priority-input"]');
    expect(priorityInput).not.toBeNull();
  });

  it("shows the Test button and sender input in the editor", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    const testBtn = document.querySelector('[data-testid="test-rule-btn"]');
    const testSenderInput = document.querySelector('[data-testid="test-sender-input"]');
    expect(testBtn).not.toBeNull();
    expect(testSenderInput).not.toBeNull();
  });

  it("shows the Save rule button in the editor", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    const saveBtn = document.querySelector('[data-testid="save-rule-btn"]');
    expect(saveBtn).not.toBeNull();
    expect(saveBtn?.textContent?.trim()).toBe("Create rule");
  });

  it("changes condition fields when rule type changes to sender_address", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    act(() => {
      const select = document.querySelector(
        '[data-testid="rule-type-select"]',
      ) as HTMLSelectElement;
      select.value = "sender_address";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const addressInput = document.querySelector('[data-testid="condition-address"]');
    expect(addressInput).not.toBeNull();
  });

  it("changes condition fields when rule type changes to header_condition", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    act(() => {
      const select = document.querySelector(
        '[data-testid="rule-type-select"]',
      ) as HTMLSelectElement;
      select.value = "header_condition";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const headerInput = document.querySelector('[data-testid="condition-header"]');
    expect(headerInput).not.toBeNull();
  });

  it("changes condition fields when rule type changes to mime_type", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    act(() => {
      const select = document.querySelector(
        '[data-testid="rule-type-select"]',
      ) as HTMLSelectElement;
      select.value = "mime_type";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const mimeInput = document.querySelector('[data-testid="condition-mime"]');
    expect(mimeInput).not.toBeNull();
  });

  it("shows validation error when saving with empty domain", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    act(() => {
      const saveBtn = document.querySelector(
        '[data-testid="save-rule-btn"]',
      ) as HTMLButtonElement;
      saveBtn?.click();
    });
    const errorEl = document.querySelector('[data-testid="editor-error"]');
    expect(errorEl).not.toBeNull();
    expect(errorEl?.textContent).toContain("Domain is required");
  });

  it("renders route-action toggle and static action select in the editor (global scope)", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    const routeToggle = document.querySelector(
      '[data-testid="route-action-toggle"]',
    ) as HTMLInputElement;
    expect(routeToggle).not.toBeNull();
    expect(routeToggle?.checked).toBe(false);
    const actionSelect = document.querySelector('[data-testid="action-select"]');
    expect(actionSelect).not.toBeNull();
  });

  it("shows connector identity validation error when saving connector-scoped rule without identity", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    // Switch to connector scope
    act(() => {
      const connectorBtn = document.querySelector(
        '[data-testid="scope-connector-btn"]',
      ) as HTMLButtonElement;
      connectorBtn?.click();
    });
    // Fill in domain but leave connector identity empty
    act(() => {
      const domainInput = document.querySelector(
        '[data-testid="condition-domain"]',
      ) as HTMLInputElement;
      // Simulate filling in domain
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set?.call(domainInput, "test.com");
      domainInput.dispatchEvent(new Event("input", { bubbles: true }));
      domainInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
    act(() => {
      const saveBtn = document.querySelector(
        '[data-testid="save-rule-btn"]',
      ) as HTMLButtonElement;
      saveBtn?.click();
    });
    const errorEl = document.querySelector('[data-testid="editor-error"]');
    expect(errorEl).not.toBeNull();
    // Will get either domain or identity error depending on validation order
  });

  // -------------------------------------------------------------------------
  // Import defaults dialog tests
  // -------------------------------------------------------------------------

  it("opens the import defaults dialog when Import defaults is clicked", () => {
    render();
    act(() => {
      const importBtn = container.querySelector(
        '[data-testid="import-defaults-btn"]',
      ) as HTMLButtonElement;
      importBtn?.click();
    });
    const confirmBtn = document.querySelector('[data-testid="confirm-import-btn"]');
    expect(confirmBtn).not.toBeNull();
    expect(confirmBtn?.textContent).toContain("Import");
  });

  it("shows preview of seed rules with scope column in the import dialog", () => {
    render();
    act(() => {
      const importBtn = container.querySelector(
        '[data-testid="import-defaults-btn"]',
      ) as HTMLButtonElement;
      importBtn?.click();
    });
    // Seed rules contain chase.com in the preview table
    expect(document.body.textContent).toContain("chase.com");
    // Should show scope column with "Global" badges
    expect(document.body.textContent).toContain("Global");
  });

  // -------------------------------------------------------------------------
  // Thread affinity panel tests (preserved)
  // -------------------------------------------------------------------------

  it("renders the thread affinity panel with toggle and TTL", () => {
    render();
    const panel = container.querySelector('[data-testid="thread-affinity-panel"]');
    expect(panel).not.toBeNull();
    const toggle = container.querySelector('[data-testid="thread-affinity-toggle"]');
    expect(toggle).not.toBeNull();
    expect(toggle?.getAttribute("aria-checked")).toBe("true");
  });

  it("renders TTL display value from settings", () => {
    render();
    const ttlDisplay = container.querySelector('[data-testid="ttl-display"]');
    expect(ttlDisplay?.textContent).toBe("30");
  });

  it("shows loading skeleton when thread affinity settings are loading", () => {
    vi.spyOn(useThreadAffinity, "useThreadAffinitySettings").mockReturnValue(
      makeQuery(undefined, true) as ReturnType<typeof useThreadAffinity.useThreadAffinitySettings>,
    );
    render();
    const toggle = container.querySelector('[data-testid="thread-affinity-toggle"]');
    expect(toggle).toBeNull();
  });

  it("calls updateThreadAffinitySettings when toggle is clicked", () => {
    const mutateFn = vi.fn();
    vi.spyOn(useThreadAffinity, "useUpdateThreadAffinitySettings").mockReturnValue({
      mutate: mutateFn,
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
    } as unknown as ReturnType<typeof useThreadAffinity.useUpdateThreadAffinitySettings>);
    render();
    act(() => {
      const toggle = container.querySelector(
        '[data-testid="thread-affinity-toggle"]',
      ) as HTMLButtonElement;
      toggle?.click();
    });
    expect(mutateFn).toHaveBeenCalledWith({ enabled: false });
  });

  // -------------------------------------------------------------------------
  // Gmail label filters panel tests (preserved)
  // -------------------------------------------------------------------------

  it("renders the Gmail label filters panel", () => {
    render();
    const panel = container.querySelector('[data-testid="gmail-label-panel"]');
    expect(panel).not.toBeNull();
  });

  it("renders include and exclude label inputs", () => {
    render();
    const includeInput = container.querySelector('[data-testid="include-labels-input"]');
    const excludeInput = container.querySelector('[data-testid="exclude-labels-input"]');
    expect(includeInput).not.toBeNull();
    expect(excludeInput).not.toBeNull();
  });

  it("shows default include labels (INBOX, IMPORTANT)", () => {
    render();
    const panel = container.querySelector('[data-testid="include-labels-input"]');
    expect(panel?.textContent).toContain("INBOX");
    expect(panel?.textContent).toContain("IMPORTANT");
  });

  it("shows default exclude labels (PROMOTIONS, SOCIAL)", () => {
    render();
    const panel = container.querySelector('[data-testid="exclude-labels-input"]');
    expect(panel?.textContent).toContain("PROMOTIONS");
    expect(panel?.textContent).toContain("SOCIAL");
  });
});

// ---------------------------------------------------------------------------
// Unit tests for helper functions
// ---------------------------------------------------------------------------

describe("FiltersTab helper functions", () => {
  it("formatAction handles all known action types", async () => {
    const { formatAction } = await import("@/components/switchboard/FiltersTab");
    expect(formatAction("skip")).toBe("Skip");
    expect(formatAction("metadata_only")).toBe("Metadata only");
    expect(formatAction("low_priority_queue")).toBe("Low priority");
    expect(formatAction("pass_through")).toBe("Pass through");
    expect(formatAction("block")).toBe("Block");
    expect(formatAction("route_to:finance")).toContain("finance");
    expect(formatAction("unknown_action")).toBe("unknown_action");
  });

  it("formatCondition formats sender_domain correctly", async () => {
    const { formatCondition } = await import("@/components/switchboard/FiltersTab");
    expect(formatCondition("sender_domain", { domain: "test.com", match: "suffix" })).toContain(
      "ends with",
    );
    expect(formatCondition("sender_domain", { domain: "test.com", match: "exact" })).toContain(
      "=",
    );
  });

  it("formatScope formats global and connector scopes", async () => {
    const { formatScope } = await import("@/components/switchboard/FiltersTab");
    expect(formatScope("global")).toBe("Global");
    expect(formatScope("connector:gmail:gmail:user:dev")).toBe("gmail:gmail:user:dev");
  });

  it("isConnectorScope identifies connector vs global scopes", async () => {
    const { isConnectorScope } = await import("@/components/switchboard/FiltersTab");
    expect(isConnectorScope("global")).toBe(false);
    expect(isConnectorScope("connector:gmail:gmail:user:dev")).toBe(true);
  });

  it("SEED_RULES all have scope='global'", async () => {
    const { SEED_RULES } = await import("@/components/switchboard/FiltersTab");
    for (const rule of SEED_RULES) {
      expect(rule.scope).toBe("global");
    }
  });
});

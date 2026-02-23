// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FiltersTab } from "@/components/switchboard/FiltersTab";
import * as useTriage from "@/hooks/use-triage";

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

const SAMPLE_RULE = {
  id: "rule-abc-001",
  rule_type: "sender_domain",
  condition: { domain: "chase.com", match: "suffix" },
  action: "route_to:finance",
  priority: 10,
  enabled: true,
  created_by: "dashboard",
  created_at: "2026-02-22T00:00:00Z",
  updated_at: "2026-02-22T00:00:00Z",
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
  vi.spyOn(useTriage, "useTriageRules").mockReturnValue(
    makeQuery({ data: [SAMPLE_RULE], meta: { total: 1 } }) as ReturnType<
      typeof useTriage.useTriageRules
    >,
  );
  vi.spyOn(useTriage, "useThreadAffinitySettings").mockReturnValue(
    makeQuery(SAMPLE_THREAD_AFFINITY_SETTINGS) as ReturnType<
      typeof useTriage.useThreadAffinitySettings
    >,
  );
  vi.spyOn(useTriage, "useCreateTriageRule").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useTriage.useCreateTriageRule>,
  );
  vi.spyOn(useTriage, "useUpdateTriageRule").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useTriage.useUpdateTriageRule>,
  );
  vi.spyOn(useTriage, "useDeleteTriageRule").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useTriage.useDeleteTriageRule>,
  );
  vi.spyOn(useTriage, "useTestTriageRule").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useTriage.useTestTriageRule>,
  );
  vi.spyOn(useTriage, "useUpdateThreadAffinitySettings").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useTriage.useUpdateThreadAffinitySettings>,
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

  it("renders the Filters tab heading and description", () => {
    render();
    const heading = container.querySelector("h2");
    expect(heading?.textContent).toBe("Filters");
    expect(container.textContent).toContain("Deterministic ingestion policy");
  });

  it("renders Email and Telegram module tabs", () => {
    render();
    const tabTriggers = container.querySelectorAll('[role="tab"]');
    const labels = Array.from(tabTriggers).map((t) => t.textContent?.trim());
    expect(labels).toContain("Email");
    expect(labels).toContain("Telegram");
  });

  it("shows the Email tab as default active tab", () => {
    render();
    const activeTabs = Array.from(container.querySelectorAll('[role="tab"][data-state="active"]'));
    expect(activeTabs.some((t) => t.textContent?.trim() === "Email")).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Rules table tests
  // -------------------------------------------------------------------------

  it("renders the rules table with sample rule", () => {
    render();
    const table = container.querySelector('[data-testid="rules-table"]');
    expect(table).not.toBeNull();
    expect(container.textContent).toContain("10");
    expect(container.textContent).toContain("chase.com");
  });

  it("renders New rule button and Import defaults button", () => {
    render();
    const newBtn = container.querySelector('[data-testid="new-rule-btn"]');
    const importBtn = container.querySelector('[data-testid="import-defaults-btn"]');
    expect(newBtn).not.toBeNull();
    expect(importBtn).not.toBeNull();
  });

  it("renders skeleton rows when loading", () => {
    vi.spyOn(useTriage, "useTriageRules").mockReturnValue(
      makeQuery(undefined, true) as ReturnType<typeof useTriage.useTriageRules>,
    );
    render();
    // No actual rule text when loading
    expect(container.textContent).not.toContain("chase.com");
  });

  it("renders empty state with CTA links when no rules", () => {
    vi.spyOn(useTriage, "useTriageRules").mockReturnValue(
      makeQuery({ data: [], meta: { total: 0 } }) as ReturnType<typeof useTriage.useTriageRules>,
    );
    render();
    const emptyLink = container.querySelector('[data-testid="empty-import-defaults-link"]');
    expect(emptyLink).not.toBeNull();
  });

  it("renders error state when rules fetch fails", () => {
    vi.spyOn(useTriage, "useTriageRules").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Network error"),
    } as ReturnType<typeof useTriage.useTriageRules>);
    render();
    const errorEl = container.querySelector('[data-testid="rules-error"]');
    expect(errorEl).not.toBeNull();
    expect(errorEl?.textContent).toContain("Failed to load");
  });

  it("renders an edit button and delete button per rule row", () => {
    render();
    const editBtn = container.querySelector(`[data-testid="edit-rule-${SAMPLE_RULE.id}"]`);
    const deleteBtn = container.querySelector(`[data-testid="delete-rule-${SAMPLE_RULE.id}"]`);
    expect(editBtn).not.toBeNull();
    expect(deleteBtn).not.toBeNull();
  });

  it("renders the enabled toggle for a rule row", () => {
    render();
    const toggle = container.querySelector(
      `[data-testid="toggle-enabled-${SAMPLE_RULE.id}"]`,
    );
    expect(toggle).not.toBeNull();
    expect(toggle?.getAttribute("aria-checked")).toBe("true");
  });

  // -------------------------------------------------------------------------
  // Rule editor drawer tests — dialog content renders in document.body portal
  // -------------------------------------------------------------------------

  it("opens the rule editor drawer when New button is clicked", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    // Sheet/Drawer portals into document.body
    const ruleTypeSelect = document.querySelector('[data-testid="rule-type-select"]');
    expect(ruleTypeSelect).not.toBeNull();
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
    // Don't fill in domain — click save
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

  it("renders route-action toggle and static action select in the editor", () => {
    render();
    act(() => {
      const newBtn = container.querySelector('[data-testid="new-rule-btn"]') as HTMLButtonElement;
      newBtn?.click();
    });
    // The route-action toggle should be present and unchecked by default
    const routeToggle = document.querySelector(
      '[data-testid="route-action-toggle"]',
    ) as HTMLInputElement;
    expect(routeToggle).not.toBeNull();
    expect(routeToggle?.checked).toBe(false);
    // Static action selector is visible when toggle is unchecked
    const actionSelect = document.querySelector('[data-testid="action-select"]');
    expect(actionSelect).not.toBeNull();
  });

  // -------------------------------------------------------------------------
  // Import defaults dialog tests — also portal-rendered in document.body
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

  it("shows preview of seed rules in the import dialog", () => {
    render();
    act(() => {
      const importBtn = container.querySelector(
        '[data-testid="import-defaults-btn"]',
      ) as HTMLButtonElement;
      importBtn?.click();
    });
    // Seed rules contain chase.com in the preview table
    expect(document.body.textContent).toContain("chase.com");
  });

  // -------------------------------------------------------------------------
  // Thread affinity panel tests
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
    vi.spyOn(useTriage, "useThreadAffinitySettings").mockReturnValue(
      makeQuery(undefined, true) as ReturnType<typeof useTriage.useThreadAffinitySettings>,
    );
    render();
    // No toggle visible while loading
    const toggle = container.querySelector('[data-testid="thread-affinity-toggle"]');
    expect(toggle).toBeNull();
  });

  it("calls updateThreadAffinitySettings when toggle is clicked", () => {
    const mutateFn = vi.fn();
    vi.spyOn(useTriage, "useUpdateThreadAffinitySettings").mockReturnValue({
      mutate: mutateFn,
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
    } as unknown as ReturnType<typeof useTriage.useUpdateThreadAffinitySettings>);
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
  // Gmail label filters panel tests
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

  // -------------------------------------------------------------------------
  // Telegram placeholder
  // -------------------------------------------------------------------------

  it("renders the Telegram tab trigger", () => {
    render();
    // Telegram tab trigger should exist
    const tabs = container.querySelectorAll('[role="tab"]');
    const telegramTab = Array.from(tabs).find((t) => t.textContent?.trim() === "Telegram");
    expect(telegramTab).not.toBeNull();
    // Telegram tab should initially be inactive (Email is default)
    expect(telegramTab?.getAttribute("data-state")).toBe("inactive");
  });
});

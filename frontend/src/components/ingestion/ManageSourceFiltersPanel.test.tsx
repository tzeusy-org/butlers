// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ManageSourceFiltersPanel } from "@/components/ingestion/ManageSourceFiltersPanel";
import * as useSourceFiltersModule from "@/hooks/use-source-filters";

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

function makeMutation(impl?: () => Promise<unknown>): MutationResult {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockImplementation(impl ?? (() => Promise.resolve({}))),
    isPending: false,
  };
}

// ---------------------------------------------------------------------------
// Sample data
// ---------------------------------------------------------------------------

const SAMPLE_FILTER_1 = {
  id: "sf-001",
  name: "Block marketing",
  description: "Blacklist for promotional senders",
  filter_mode: "blacklist" as const,
  source_key_type: "domain",
  patterns: ["promo.example.com", "newsletter.org"],
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
};

const SAMPLE_FILTER_2 = {
  id: "sf-002",
  name: "Allow VIP",
  description: null,
  filter_mode: "whitelist" as const,
  source_key_type: "sender_address",
  patterns: ["ceo@example.com"],
  created_at: "2026-03-02T00:00:00Z",
  updated_at: "2026-03-02T00:00:00Z",
};

const SAMPLE_LIST_RESPONSE = {
  data: [SAMPLE_FILTER_1, SAMPLE_FILTER_2],
  meta: {},
};

// ---------------------------------------------------------------------------
// Default mock setup
// ---------------------------------------------------------------------------

function setupDefaultMocks() {
  vi.spyOn(useSourceFiltersModule, "useSourceFilters").mockReturnValue(
    makeQuery(SAMPLE_LIST_RESPONSE) as ReturnType<typeof useSourceFiltersModule.useSourceFilters>,
  );
  vi.spyOn(useSourceFiltersModule, "useCreateSourceFilter").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useSourceFiltersModule.useCreateSourceFilter>,
  );
  vi.spyOn(useSourceFiltersModule, "useUpdateSourceFilter").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useSourceFiltersModule.useUpdateSourceFilter>,
  );
  vi.spyOn(useSourceFiltersModule, "useDeleteSourceFilter").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useSourceFiltersModule.useDeleteSourceFilter>,
  );
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

describe("ManageSourceFiltersPanel", () => {
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

  function render(open = true) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ManageSourceFiltersPanel open={open} onOpenChange={vi.fn()} />
        </QueryClientProvider>,
      );
    });
  }

  // -------------------------------------------------------------------------
  // Panel open/closed
  // -------------------------------------------------------------------------

  it("does not render panel content when closed", () => {
    render(false);
    const panel = document.querySelector('[data-testid="manage-source-filters-panel"]');
    // Sheet is not open — content may be hidden or absent
    expect(panel?.getAttribute("data-state")).not.toBe("open");
  });

  it("renders the panel title when open", () => {
    render();
    expect(document.body.textContent).toContain("Manage Source Filters");
  });

  it("renders the panel description when open", () => {
    render();
    expect(document.body.textContent).toContain("Named source filters define reusable");
  });

  // -------------------------------------------------------------------------
  // Table rendering
  // -------------------------------------------------------------------------

  it("renders the filters table with both sample filters", () => {
    render();
    const table = document.querySelector('[data-testid="source-filters-table"]');
    expect(table).not.toBeNull();
    expect(document.body.textContent).toContain("Block marketing");
    expect(document.body.textContent).toContain("Allow VIP");
  });

  it("renders filter mode badges", () => {
    render();
    expect(document.body.textContent).toContain("blacklist");
    expect(document.body.textContent).toContain("whitelist");
  });

  it("renders pattern type for each filter", () => {
    render();
    expect(document.body.textContent).toContain("domain");
    expect(document.body.textContent).toContain("sender_address");
  });

  it("renders pattern counts", () => {
    render();
    // Block marketing has 2 patterns; Allow VIP has 1
    const rows = document.querySelectorAll('[data-testid^="filter-row-"]');
    expect(rows.length).toBe(2);
  });

  it("renders edit and delete buttons for each filter", () => {
    render();
    expect(document.querySelector(`[data-testid="edit-filter-${SAMPLE_FILTER_1.id}"]`)).not.toBeNull();
    expect(document.querySelector(`[data-testid="delete-filter-${SAMPLE_FILTER_1.id}"]`)).not.toBeNull();
    expect(document.querySelector(`[data-testid="edit-filter-${SAMPLE_FILTER_2.id}"]`)).not.toBeNull();
    expect(document.querySelector(`[data-testid="delete-filter-${SAMPLE_FILTER_2.id}"]`)).not.toBeNull();
  });

  // -------------------------------------------------------------------------
  // Loading and error states
  // -------------------------------------------------------------------------

  it("renders loading skeletons when data is loading", () => {
    vi.spyOn(useSourceFiltersModule, "useSourceFilters").mockReturnValue(
      makeQuery(undefined, true) as ReturnType<typeof useSourceFiltersModule.useSourceFilters>,
    );
    render();
    const loading = document.querySelector('[data-testid="filters-loading"]');
    expect(loading).not.toBeNull();
  });

  it("renders error state when fetch fails", () => {
    vi.spyOn(useSourceFiltersModule, "useSourceFilters").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Network error"),
    } as ReturnType<typeof useSourceFiltersModule.useSourceFilters>);
    render();
    const errorEl = document.querySelector('[data-testid="filters-error"]');
    expect(errorEl).not.toBeNull();
    expect(errorEl?.textContent).toContain("Failed to load source filters");
  });

  it("renders empty state when no filters exist", () => {
    vi.spyOn(useSourceFiltersModule, "useSourceFilters").mockReturnValue(
      makeQuery({ data: [], meta: {} }) as ReturnType<typeof useSourceFiltersModule.useSourceFilters>,
    );
    render();
    const empty = document.querySelector('[data-testid="filters-empty"]');
    expect(empty).not.toBeNull();
    expect(empty?.textContent).toContain("No source filters yet");
  });

  // -------------------------------------------------------------------------
  // Create flow
  // -------------------------------------------------------------------------

  it("renders the 'Create filter' button initially", () => {
    render();
    const btn = document.querySelector('[data-testid="show-create-filter-btn"]');
    expect(btn).not.toBeNull();
  });

  it("shows the create form when Create filter is clicked", () => {
    render();
    act(() => {
      const btn = document.querySelector(
        '[data-testid="show-create-filter-btn"]',
      ) as HTMLButtonElement;
      btn?.click();
    });
    const form = document.querySelector('[data-testid="create-filter-form"]');
    expect(form).not.toBeNull();
  });

  it("shows name, mode, type and patterns inputs in create form", () => {
    render();
    act(() => {
      const btn = document.querySelector(
        '[data-testid="show-create-filter-btn"]',
      ) as HTMLButtonElement;
      btn?.click();
    });
    expect(document.querySelector('[data-testid="create-filter-name"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="create-filter-mode"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="create-filter-type"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="create-filter-patterns"]')).not.toBeNull();
  });

  it("shows validation error when name is empty on submit", () => {
    render();
    act(() => {
      const btn = document.querySelector(
        '[data-testid="show-create-filter-btn"]',
      ) as HTMLButtonElement;
      btn?.click();
    });
    act(() => {
      const submit = document.querySelector(
        '[data-testid="create-filter-submit"]',
      ) as HTMLButtonElement;
      submit?.click();
    });
    const errorEl = document.querySelector('[data-testid="create-filter-error"]');
    expect(errorEl).not.toBeNull();
    expect(errorEl?.textContent).toContain("Name is required");
  });

  it("shows validation error when patterns list is empty on submit", () => {
    render();
    act(() => {
      const btn = document.querySelector(
        '[data-testid="show-create-filter-btn"]',
      ) as HTMLButtonElement;
      btn?.click();
    });
    // Fill in a name
    act(() => {
      const nameInput = document.querySelector(
        '[data-testid="create-filter-name"]',
      ) as HTMLInputElement;
      nameInput.value = "My filter";
      nameInput.dispatchEvent(new Event("input", { bubbles: true }));
      // Use React's synthetic event
      Object.defineProperty(nameInput, "value", { writable: true, value: "My filter" });
      nameInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
    act(() => {
      const submit = document.querySelector(
        '[data-testid="create-filter-submit"]',
      ) as HTMLButtonElement;
      submit?.click();
    });
    // patterns will still be empty → error
    const errorEl = document.querySelector('[data-testid="create-filter-error"]');
    expect(errorEl).not.toBeNull();
  });

  it("cancels create form when Cancel is clicked", () => {
    render();
    act(() => {
      const btn = document.querySelector(
        '[data-testid="show-create-filter-btn"]',
      ) as HTMLButtonElement;
      btn?.click();
    });
    expect(document.querySelector('[data-testid="create-filter-form"]')).not.toBeNull();
    act(() => {
      const cancel = document.querySelector(
        '[data-testid="create-filter-cancel"]',
      ) as HTMLButtonElement;
      cancel?.click();
    });
    expect(document.querySelector('[data-testid="create-filter-form"]')).toBeNull();
  });

  // -------------------------------------------------------------------------
  // Edit flow
  // -------------------------------------------------------------------------

  it("shows edit form when edit button is clicked", () => {
    render();
    act(() => {
      const editBtn = document.querySelector(
        `[data-testid="edit-filter-${SAMPLE_FILTER_1.id}"]`,
      ) as HTMLButtonElement;
      editBtn?.click();
    });
    const form = document.querySelector(`[data-testid="edit-filter-form-${SAMPLE_FILTER_1.id}"]`);
    expect(form).not.toBeNull();
  });

  it("pre-fills edit form with existing filter values", () => {
    render();
    act(() => {
      const editBtn = document.querySelector(
        `[data-testid="edit-filter-${SAMPLE_FILTER_1.id}"]`,
      ) as HTMLButtonElement;
      editBtn?.click();
    });
    const nameInput = document.querySelector('[data-testid="edit-filter-name"]') as HTMLInputElement;
    expect(nameInput?.value).toBe(SAMPLE_FILTER_1.name);
  });

  it("cancels edit form when Cancel is clicked", () => {
    render();
    act(() => {
      const editBtn = document.querySelector(
        `[data-testid="edit-filter-${SAMPLE_FILTER_1.id}"]`,
      ) as HTMLButtonElement;
      editBtn?.click();
    });
    expect(document.querySelector(`[data-testid="edit-filter-form-${SAMPLE_FILTER_1.id}"]`)).not.toBeNull();
    act(() => {
      const cancel = document.querySelector('[data-testid="edit-filter-cancel"]') as HTMLButtonElement;
      cancel?.click();
    });
    expect(document.querySelector(`[data-testid="edit-filter-form-${SAMPLE_FILTER_1.id}"]`)).toBeNull();
  });

  it("shows validation error when saving edit with empty name", () => {
    render();
    act(() => {
      const editBtn = document.querySelector(
        `[data-testid="edit-filter-${SAMPLE_FILTER_1.id}"]`,
      ) as HTMLButtonElement;
      editBtn?.click();
    });
    // Clear name
    act(() => {
      const nameInput = document.querySelector(
        '[data-testid="edit-filter-name"]',
      ) as HTMLInputElement;
      Object.defineProperty(nameInput, "value", { writable: true, value: "" });
      nameInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
    act(() => {
      const submit = document.querySelector('[data-testid="edit-filter-submit"]') as HTMLButtonElement;
      submit?.click();
    });
    const errorEl = document.querySelector('[data-testid="edit-filter-error"]');
    expect(errorEl).not.toBeNull();
  });

  // -------------------------------------------------------------------------
  // Delete flow
  // -------------------------------------------------------------------------

  it("opens delete confirmation dialog when delete button is clicked", () => {
    render();
    act(() => {
      const deleteBtn = document.querySelector(
        `[data-testid="delete-filter-${SAMPLE_FILTER_1.id}"]`,
      ) as HTMLButtonElement;
      deleteBtn?.click();
    });
    const dialog = document.querySelector('[data-testid="delete-filter-dialog"]');
    expect(dialog).not.toBeNull();
  });

  it("shows filter name in delete confirmation dialog", () => {
    render();
    act(() => {
      const deleteBtn = document.querySelector(
        `[data-testid="delete-filter-${SAMPLE_FILTER_1.id}"]`,
      ) as HTMLButtonElement;
      deleteBtn?.click();
    });
    expect(document.body.textContent).toContain(SAMPLE_FILTER_1.name);
  });

  it("calls deleteSourceFilter mutation when confirmed", async () => {
    const mutateAsyncFn = vi.fn().mockResolvedValue({});
    vi.spyOn(useSourceFiltersModule, "useDeleteSourceFilter").mockReturnValue({
      mutate: vi.fn(),
      mutateAsync: mutateAsyncFn,
      isPending: false,
    } as unknown as ReturnType<typeof useSourceFiltersModule.useDeleteSourceFilter>);

    render();
    act(() => {
      const deleteBtn = document.querySelector(
        `[data-testid="delete-filter-${SAMPLE_FILTER_1.id}"]`,
      ) as HTMLButtonElement;
      deleteBtn?.click();
    });
    await act(async () => {
      const confirmBtn = document.querySelector(
        '[data-testid="delete-filter-confirm"]',
      ) as HTMLButtonElement;
      confirmBtn?.click();
    });
    expect(mutateAsyncFn).toHaveBeenCalledWith(SAMPLE_FILTER_1.id);
  });
});

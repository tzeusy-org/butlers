/**
 * SettingsModelsPage — /settings/models
 *
 * Satisfies OpenSpec §4.7: happy-path + one error-state model row.
 *
 * Tests verify:
 *   - Page renders breadcrumb and heading (page loads)
 *   - Model list renders — at least one model row visible (happy path)
 *   - Verified model row renders with ✓ indicator
 *   - Error-state model row (last_verified_ok=false) renders with ✗ indicator
 *   - Never-verified (null) model row renders without either indicator
 *   - Tier-grouped sections render in canonical order (Reasoning, Workhorse, …)
 *   - Empty tier section shows "Nothing in this tier." serif italic text
 *   - Loading state renders loading copy
 *   - Error state renders failure copy
 *   - Filter chips render for tier and state
 *   - "Verify all" button renders
 *   - Priority stepper renders ↑ / ↓ controls per row
 *   - Enable toggle renders per row (aria-label reflects model alias)
 *   - Row action links render (Test, Edit, Delete)
 *   - Disabled model row carries opacity-60 class
 *   - Breadcrumb counter reflects model count + verified count
 *   - EditModelDialog: open/close, validation, save payload, success/error callbacks (bu-mjo90)
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { render, cleanup, fireEvent, screen, act } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SettingsModelsPage from "@/pages/SettingsModelsPage";
import type { ModelCatalogEntry } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-model-catalog", () => ({
  useModelCatalog: vi.fn(),
  useCreateModelCatalogEntry: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUpdateModelCatalogEntry: vi.fn(),
  useTestModelCatalogEntry: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useDeleteModelCatalogEntry: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
  useUpdateModelPriority: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useVerifyAllModels: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useSetModelTokenLimits: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useResetModelUsage: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useModelUsageDetail: vi.fn(() => ({ data: undefined, isLoading: false })),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Imports after mocks
// ---------------------------------------------------------------------------

import {
  useCreateModelCatalogEntry,
  useDeleteModelCatalogEntry,
  useModelCatalog,
  useResetModelUsage,
  useSetModelTokenLimits,
  useUpdateModelCatalogEntry,
} from "@/hooks/use-model-catalog";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function makeModel(overrides: Partial<ModelCatalogEntry> = {}): ModelCatalogEntry {
  return {
    id: "model-1",
    alias: "claude-sonnet",
    runtime_type: "claude",
    model_id: "claude-sonnet-4-5-20250514",
    extra_args: [],
    complexity_tier: "workhorse",
    enabled: true,
    priority: 10,
    session_timeout_s: 300,
    usage_24h: 1234,
    usage_30d: 45000,
    limit_24h: null,
    limit_30d: null,
    last_verified_at: null,
    last_verified_latency_ms: null,
    last_verified_ok: null,
    ...overrides,
  };
}

function setHookState({
  entries = [] as ModelCatalogEntry[],
  isLoading = false,
  isError = false,
}: {
  entries?: ModelCatalogEntry[];
  isLoading?: boolean;
  isError?: boolean;
}) {
  vi.mocked(useModelCatalog).mockReturnValue({
    data: isLoading || isError ? undefined : { data: entries, meta: {} },
    isLoading,
    isError,
    error: isError ? new Error("network error") : null,
    isPending: isLoading,
    isSuccess: !isLoading && !isError,
  } as AnyMock);

  vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as AnyMock);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsModelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mountPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsModelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.resetAllMocks();
  setHookState({ entries: [] });
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Page structure (breadcrumb + heading)
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — page structure", () => {
  it("renders the breadcrumb trail", () => {
    setHookState({ entries: [] });
    const html = renderPage();
    expect(html).toContain("butlers");
    expect(html).toContain("settings");
    expect(html).toContain("model catalog");
  });

  it("renders the page heading", () => {
    setHookState({ entries: [] });
    const html = renderPage();
    expect(html).toContain("Every model the staff can call");
  });

  it("renders the Verify All button", () => {
    setHookState({ entries: [] });
    const html = renderPage();
    expect(html).toContain("Verify all");
  });
});

// ---------------------------------------------------------------------------
// Filter chips
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — filter chips", () => {
  it("renders tier filter chips for all six canonical tiers", () => {
    setHookState({ entries: [] });
    const html = renderPage();
    expect(html).toContain("reasoning");
    expect(html).toContain("workhorse");
    expect(html).toContain("cheap");
    expect(html).toContain("specialty");
    expect(html).toContain("local");
    expect(html).toContain("legacy");
  });

  it("renders state filter chips (verified, attention)", () => {
    setHookState({ entries: [] });
    const html = renderPage();
    expect(html).toContain("verified");
    expect(html).toContain("attention");
  });
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — loading state", () => {
  it("renders loading copy while data is fetching", () => {
    setHookState({ isLoading: true });
    const html = renderPage();
    expect(html).toContain("Loading catalog");
  });

  it("does not render model rows while loading", () => {
    setHookState({ isLoading: true });
    const html = renderPage();
    // Tier section headers (TierHeader) should not appear — loading replaces the body
    expect(html).not.toContain("Reasoning");
  });
});

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — error state", () => {
  it("renders failure copy when the catalog fetch fails", () => {
    setHookState({ isError: true });
    const html = renderPage();
    expect(html).toContain("Failed to load model catalog");
  });

  it("does not render tier sections on error", () => {
    setHookState({ isError: true });
    const html = renderPage();
    expect(html).not.toContain("Reasoning");
  });
});

// ---------------------------------------------------------------------------
// Empty tier state
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — empty tier state", () => {
  it("renders all six tier section headers even when catalog is empty", () => {
    setHookState({ entries: [] });
    const html = renderPage();
    expect(html).toContain("Reasoning");
    expect(html).toContain("Workhorse");
    expect(html).toContain("Cheap");
    expect(html).toContain("Specialty");
    expect(html).toContain("Local");
    expect(html).toContain("Legacy");
  });

  it("renders 'Nothing in this tier.' for each empty tier", () => {
    setHookState({ entries: [] });
    const html = renderPage();
    // Six tiers × empty state = six occurrences
    const matches = [...html.matchAll(/Nothing in this tier\./g)];
    expect(matches.length).toBe(6);
  });
});

// ---------------------------------------------------------------------------
// Happy path — verified model row
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — happy path: verified model row", () => {
  it("renders at least one model row", () => {
    const model = makeModel({ alias: "claude-sonnet", last_verified_ok: true });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("claude-sonnet");
  });

  it("renders the model alias in the row", () => {
    const model = makeModel({ alias: "my-workhorse-model", last_verified_ok: true });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("my-workhorse-model");
  });

  it("renders model_id and runtime_type in the sub-label", () => {
    const model = makeModel({
      alias: "sonnet",
      model_id: "claude-sonnet-4-5",
      runtime_type: "claude",
      last_verified_ok: true,
    });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("claude-sonnet-4-5");
    expect(html).toContain("claude");
  });

  it("renders ✓ verified indicator for last_verified_ok=true", () => {
    const model = makeModel({ alias: "verified-model", last_verified_ok: true });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("✓");
  });

  it("does not render ✗ for a verified model", () => {
    const model = makeModel({ alias: "verified-only", last_verified_ok: true });
    setHookState({ entries: [model] });
    const html = renderPage();
    // ✗ should not appear when only verified models are present
    expect(html).not.toContain("✗");
  });

  it("renders priority stepper controls (↑ and ↓)", () => {
    const model = makeModel({ alias: "stepper-model", priority: 5 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("↑");
    expect(html).toContain("↓");
  });

  it("renders priority value in stepper", () => {
    const model = makeModel({ alias: "prio-model", priority: 7 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("7");
  });

  it("renders enable toggle with accessible aria-label", () => {
    const model = makeModel({ alias: "toggle-model", enabled: true });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("Disable toggle-model");
  });

  it("renders Test, Edit, Delete row actions", () => {
    const model = makeModel({ alias: "action-model" });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("Test");
    expect(html).toContain("Edit");
    expect(html).toContain("Delete");
  });

  it("renders compact usage_24h token count in the row", () => {
    const model = makeModel({ alias: "usage-model", usage_24h: 5000 });
    setHookState({ entries: [model] });
    const html = renderPage();
    // Compact format: 5000 → "5K"
    expect(html).toContain("5K");
  });

  it("renders the Workhorse tier section with model count", () => {
    const model = makeModel({ alias: "wh-model", complexity_tier: "workhorse" });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("Workhorse");
    expect(html).toContain("1 model");
  });

  it("uses 'models' (plural) when tier has multiple entries", () => {
    const m1 = makeModel({ id: "a", alias: "m1", complexity_tier: "cheap" });
    const m2 = makeModel({ id: "b", alias: "m2", complexity_tier: "cheap" });
    setHookState({ entries: [m1, m2] });
    const html = renderPage();
    expect(html).toContain("2 models");
  });

  it("breadcrumb counter reflects total and verified counts", () => {
    const m1 = makeModel({ id: "a", alias: "m1", last_verified_ok: true });
    const m2 = makeModel({ id: "b", alias: "m2", last_verified_ok: false });
    setHookState({ entries: [m1, m2] });
    const html = renderPage();
    expect(html).toContain("2 models");
    expect(html).toContain("1 verified");
  });
});

// ---------------------------------------------------------------------------
// Error-state model row (last_verified_ok = false)
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — error-state model row (§4.7)", () => {
  it("renders ✗ error indicator for last_verified_ok=false", () => {
    const model = makeModel({ alias: "broken-model", last_verified_ok: false });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("✗");
  });

  it("does not render ✓ for an error-state model", () => {
    const model = makeModel({ alias: "error-only-model", last_verified_ok: false });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).not.toContain("✓");
  });

  it("renders the error-state model alias in the row", () => {
    const model = makeModel({ alias: "rate-limited-model", last_verified_ok: false });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("rate-limited-model");
  });

  it("renders both verified and error indicators when catalog has both", () => {
    const ok = makeModel({ id: "ok", alias: "ok-model", last_verified_ok: true });
    const err = makeModel({ id: "err", alias: "err-model", last_verified_ok: false });
    setHookState({ entries: [ok, err] });
    const html = renderPage();
    expect(html).toContain("✓");
    expect(html).toContain("✗");
  });
});

// ---------------------------------------------------------------------------
// Never-verified model row (last_verified_ok = null / untested)
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — untested model row", () => {
  it("renders the model alias without ✓ or ✗ when never verified", () => {
    const model = makeModel({ alias: "untested-model", last_verified_ok: null });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("untested-model");
    expect(html).not.toContain("✓");
    expect(html).not.toContain("✗");
  });
});

// ---------------------------------------------------------------------------
// Disabled model row visual treatment
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — disabled model row", () => {
  it("applies opacity-60 class to disabled model rows", () => {
    const model = makeModel({ alias: "disabled-model", enabled: false });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("opacity-60");
  });

  it("does not apply opacity-60 to enabled model rows", () => {
    const model = makeModel({ alias: "enabled-model", enabled: true });
    setHookState({ entries: [model] });
    const html = renderPage();
    // opacity-60 must not appear at all — only disabled rows get it
    expect(html).not.toContain("opacity-60");
  });

  it("renders aria-label with 'Enable' prefix for disabled toggle", () => {
    const model = makeModel({ alias: "off-model", enabled: false });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("Enable off-model");
  });
});

// ---------------------------------------------------------------------------
// End-of-catalog footer
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — catalog footer", () => {
  it("renders 'end of catalog' footer text", () => {
    const model = makeModel({ alias: "footer-model" });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("end of catalog");
  });

  it("renders singular 'entry' when catalog has one model", () => {
    const model = makeModel({ alias: "single-model" });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("1 entry");
  });

  it("renders plural 'entries' when catalog has multiple models", () => {
    const m1 = makeModel({ id: "a", alias: "m1" });
    const m2 = makeModel({ id: "b", alias: "m2" });
    setHookState({ entries: [m1, m2] });
    const html = renderPage();
    expect(html).toContain("2 entries");
  });
});

// ---------------------------------------------------------------------------
// Canonical tier order
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — canonical tier order", () => {
  it("renders tier headers in canonical order: reasoning → workhorse → cheap → specialty → local → legacy", () => {
    setHookState({ entries: [] });
    const html = renderPage();
    const reasoning = html.indexOf("Reasoning");
    const workhorse = html.indexOf("Workhorse");
    const cheap = html.indexOf("Cheap");
    const specialty = html.indexOf("Specialty");
    const local = html.indexOf("Local");
    const legacy = html.indexOf("Legacy");
    expect(reasoning).toBeLessThan(workhorse);
    expect(workhorse).toBeLessThan(cheap);
    expect(cheap).toBeLessThan(specialty);
    expect(specialty).toBeLessThan(local);
    expect(local).toBeLessThan(legacy);
  });
});

// ---------------------------------------------------------------------------
// EditModelDialog — open/close, validation, save payload, callbacks (bu-mjo90)
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — EditModelDialog", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as AnyMock);
    setHookState({ entries: [makeModel()] });
  });

  // -------------------------------------------------------------------------
  // Static-markup contract
  // -------------------------------------------------------------------------

  it("renders an Edit button for each model row", () => {
    setHookState({
      entries: [makeModel(), makeModel({ id: "entry-2", alias: "claude-haiku" })],
    });
    const html = renderPage();
    const editButtons = html.match(/aria-label="Edit /g) ?? [];
    expect(editButtons).toHaveLength(2);
  });

  it("renders the Edit button with correct aria-label for the model alias", () => {
    setHookState({ entries: [makeModel({ alias: "my-model" })] });
    const html = renderPage();
    expect(html).toContain('aria-label="Edit my-model"');
  });

  // -------------------------------------------------------------------------
  // Dialog open / close interaction
  // -------------------------------------------------------------------------

  it("opens the edit dialog when the Edit button is clicked", async () => {
    mountPage();
    const editBtn = screen.getByLabelText("Edit claude-sonnet");
    await act(async () => {
      fireEvent.click(editBtn);
    });
    // Dialog title should appear
    expect(screen.getByText(/Edit model/)).toBeTruthy();
  });

  it("closes the dialog when Cancel is clicked", async () => {
    mountPage();
    const editBtn = screen.getByLabelText("Edit claude-sonnet");
    await act(async () => {
      fireEvent.click(editBtn);
    });
    // Dialog title visible
    expect(screen.getByText(/Edit model/)).toBeTruthy();

    const cancelBtn = screen.getByRole("button", { name: /cancel/i });
    await act(async () => {
      fireEvent.click(cancelBtn);
    });
    // Dialog title gone
    expect(screen.queryByText(/Edit model/)).toBeNull();
  });

  // -------------------------------------------------------------------------
  // Validation
  // -------------------------------------------------------------------------

  it("shows validation error when alias is cleared before save", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const aliasInput = screen.getByLabelText(/^alias$/i);
    await act(async () => {
      fireEvent.change(aliasInput, { target: { value: "" } });
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(screen.getByText("Alias is required")).toBeTruthy();
    expect(vi.mocked(useUpdateModelCatalogEntry)().mutate).not.toHaveBeenCalled();
  });

  it("shows validation error for invalid JSON in args field (raw-JSON mode)", async () => {
    // The default mode is the KV editor. Users must toggle to raw-JSON mode to type raw JSON.
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    // Toggle to raw-JSON mode
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Switch to raw JSON editor"));
    });

    const argsField = screen.getByLabelText("Args (JSON array)");
    await act(async () => {
      fireEvent.change(argsField, { target: { value: "not-valid-json" } });
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(screen.getByText("Invalid JSON")).toBeTruthy();
    expect(vi.mocked(useUpdateModelCatalogEntry)().mutate).not.toHaveBeenCalled();
  });

  it("shows validation error when args is not a JSON array (raw-JSON mode)", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    // Toggle to raw-JSON mode
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Switch to raw JSON editor"));
    });

    const argsField = screen.getByLabelText("Args (JSON array)");
    await act(async () => {
      fireEvent.change(argsField, { target: { value: '{"key": "val"}' } });
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(screen.getByText("Must be a JSON array")).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // Happy-path save
  // -------------------------------------------------------------------------

  it("calls mutate with correct payload on valid save", async () => {
    const mutate = vi.fn();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    // Change alias
    const aliasInput = screen.getByLabelText(/^alias$/i);
    await act(async () => {
      fireEvent.change(aliasInput, { target: { value: "renamed-sonnet" } });
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "model-1",
        body: expect.objectContaining({
          alias: "renamed-sonnet",
          runtime_type: "claude",
          complexity_tier: "workhorse",
          priority: 10,
          session_timeout_s: 300,
          enabled: true,
          extra_args: [],
        }),
      }),
      expect.any(Object),
    );
  });

  // -------------------------------------------------------------------------
  // Per-session timeout field (bu-cw3xt)
  // -------------------------------------------------------------------------

  it("renders the per-session timeout input pre-populated from the catalog row", async () => {
    setHookState({ entries: [makeModel({ session_timeout_s: 900 })] });
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const timeoutInput = screen.getByLabelText(/per-session timeout/i) as HTMLInputElement;
    expect(timeoutInput).toBeTruthy();
    expect(timeoutInput.value).toBe("900");
  });

  it("includes the edited session_timeout_s in the save payload", async () => {
    const mutate = vi.fn();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const timeoutInput = screen.getByLabelText(/per-session timeout/i);
    await act(async () => {
      fireEvent.change(timeoutInput, { target: { value: "1200" } });
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "model-1",
        body: expect.objectContaining({ session_timeout_s: 1200 }),
      }),
      expect.any(Object),
    );
  });

  it("rejects a non-positive session timeout and blocks save", async () => {
    const mutate = vi.fn();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const timeoutInput = screen.getByLabelText(/per-session timeout/i);
    await act(async () => {
      fireEvent.change(timeoutInput, { target: { value: "0" } });
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(
      screen.getByText("Session timeout must be a positive integer (seconds)"),
    ).toBeTruthy();
    expect(mutate).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // Runtime type field (bu-o8m01)
  // -------------------------------------------------------------------------

  it("renders the runtime type select pre-populated from the catalog row", async () => {
    setHookState({ entries: [makeModel({ runtime_type: "codex" })] });
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    // The SelectTrigger should display the current runtime_type value
    expect(screen.getByText("codex")).toBeTruthy();
  });

  it("includes runtime_type in the save payload", async () => {
    const mutate = vi.fn();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);
    // Keep the catalog data for the model
    vi.mocked(useModelCatalog).mockReturnValue({
      data: { data: [makeModel({ runtime_type: "claude" })], meta: {} },
      isLoading: false,
      isError: false,
      error: null,
      isPending: false,
      isSuccess: true,
    } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "model-1",
        body: expect.objectContaining({ runtime_type: "claude" }),
      }),
      expect.any(Object),
    );
  });

  it("renders a custom runtime_type not in RUNTIME_TYPES as an option in the select", async () => {
    setHookState({ entries: [makeModel({ runtime_type: "fable" })] });
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    // The custom value should appear in the dropdown options
    expect(screen.getByText("fable")).toBeTruthy();
  });

  it("calls onSuccess toast and closes dialog when mutate resolves", async () => {
    const { toast } = await import("sonner");
    let savedCallbacks: { onSuccess?: () => void; onError?: () => void } = {};
    const mutate = vi.fn((_payload, callbacks) => {
      savedCallbacks = callbacks;
    });
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    // Simulate success
    await act(async () => {
      savedCallbacks.onSuccess?.();
    });

    expect(toast.success).toHaveBeenCalled();
    expect(screen.queryByText(/Edit model/)).toBeNull();
  });

  it("shows error toast and keeps dialog open when mutate fails", async () => {
    const { toast } = await import("sonner");
    let savedCallbacks: { onSuccess?: () => void; onError?: ((err: Error) => void) } = {};
    const mutate = vi.fn((_payload, callbacks) => {
      savedCallbacks = callbacks;
    });
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    // Simulate error
    await act(async () => {
      savedCallbacks.onError?.(new Error("Network failure"));
    });

    expect(toast.error).toHaveBeenCalled();
    // Dialog remains open
    expect(screen.getByText(/Edit model/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AddModelDialog — open/close, validation, create payload, callbacks
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — AddModelDialog", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useCreateModelCatalogEntry).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as AnyMock);
    setHookState({ entries: [] });
  });

  it("renders the New model button in the page header", () => {
    const html = renderPage();
    expect(html).toContain("New model");
  });

  it("opens the add dialog when New model is clicked", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    });
    expect(screen.getByText(/Register a new entry/)).toBeTruthy();
  });

  it("closes the add dialog when Cancel is clicked", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    });
    expect(screen.getByText(/Register a new entry/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    });
    expect(screen.queryByText(/Register a new entry/)).toBeNull();
  });

  it("blocks create and shows validation errors when required fields are empty", async () => {
    const mutate = vi.fn();
    vi.mocked(useCreateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    });

    // Alias and model_id are empty by default
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    });

    expect(screen.getByText("Alias is required")).toBeTruthy();
    expect(screen.getByText("Model ID is required")).toBeTruthy();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("sends a create payload with canonical defaults when valid", async () => {
    const mutate = vi.fn();
    vi.mocked(useCreateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    });

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/^alias$/i), { target: { value: "my-new-model" } });
      fireEvent.change(screen.getByLabelText(/model id/i), { target: { value: "claude-x-1" } });
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        alias: "my-new-model",
        model_id: "claude-x-1",
        runtime_type: "claude",
        complexity_tier: "workhorse",
        priority: 0,
        session_timeout_s: 1800,
        enabled: true,
        extra_args: [],
      }),
      expect.any(Object),
    );
  });

  it("surfaces a duplicate-alias (409) error via toast and keeps dialog open", async () => {
    const { toast } = await import("sonner");
    const { ApiError } = await import("@/api/index.ts");
    let savedCallbacks: { onSuccess?: () => void; onError?: (err: unknown) => void } = {};
    const mutate = vi.fn((_payload, callbacks) => {
      savedCallbacks = callbacks;
    });
    vi.mocked(useCreateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    });
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/^alias$/i), { target: { value: "claude-sonnet" } });
      fireEvent.change(screen.getByLabelText(/model id/i), { target: { value: "claude-x-1" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    });

    await act(async () => {
      savedCallbacks.onError?.(new ApiError("conflict", "alias exists", 409));
    });

    expect(toast.error).toHaveBeenCalled();
    // Dialog remains open so the user can fix the alias
    expect(screen.getByText(/Register a new entry/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Usage columns — 30d usage, progress bars, color thresholds, BLOCKED badge,
// reset, tooltip, inline limit editing (bu-1ywfy; spec: catalog-token-limits
// "Dashboard Usage Columns")
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — token-usage columns", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setHookState({ entries: [] });
  });

  it("renders both 24h and 30d compact usage values", () => {
    const model = makeModel({ alias: "u", usage_24h: 142_312, usage_30d: 3_400_000 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("142K"); // rolling 24h
    expect(html).toContain("3.4M"); // rolling 30d
  });

  it("renders the 24h and 30d window labels per row", () => {
    setHookState({ entries: [makeModel({ alias: "u" })] });
    const html = renderPage();
    expect(html).toContain("24h");
    expect(html).toContain("30d");
  });

  it("renders a green progress bar when usage is below 60% of the limit", () => {
    const model = makeModel({ alias: "g", usage_24h: 100_000, limit_24h: 500_000 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("bg-green-500");
    expect(html).not.toContain("bg-red-500");
  });

  it("renders a yellow progress bar between 60% and 85%", () => {
    const model = makeModel({ alias: "y", usage_24h: 350_000, limit_24h: 500_000 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("bg-yellow-500");
  });

  it("renders a red progress bar at or above 85%", () => {
    const model = makeModel({ alias: "r", usage_24h: 450_000, limit_24h: 500_000 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("bg-red-500");
  });

  it("renders a BLOCKED badge when usage exceeds the limit", () => {
    const model = makeModel({ alias: "b", usage_24h: 600_000, limit_24h: 500_000 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("BLOCKED");
  });

  it("does not render a BLOCKED badge when under the limit", () => {
    const model = makeModel({ alias: "ok", usage_24h: 100_000, limit_24h: 500_000 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).not.toContain("BLOCKED");
  });

  it("shows used/- with no progress fill when no limit is configured", () => {
    const model = makeModel({ alias: "nolimit", usage_24h: 42_000, limit_24h: null });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("42K");
    // The dashed no-limit placeholder, not a colored fill bar.
    expect(html).toContain("border-dashed");
  });

  it("exposes the exact counts, percent, and window label via the tooltip aria-label", () => {
    const model = makeModel({
      id: "t",
      alias: "tip",
      usage_24h: 142_312,
      limit_24h: 500_000,
    });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("142,312 / 500,000 tokens");
    expect(html).toContain("28% used");
    expect(html).toContain("Rolling 24h window");
  });

  it("renders a reset button for each window", () => {
    setHookState({ entries: [makeModel({ alias: "rst" })] });
    const html = renderPage();
    expect(html).toContain("Reset 24h usage for rst");
    expect(html).toContain("Reset 30d usage for rst");
  });

  it("calls reset-usage with the correct window when the reset button is clicked", async () => {
    const mutate = vi.fn();
    vi.mocked(useResetModelUsage).mockReturnValue({ mutate, isPending: false } as AnyMock);
    setHookState({ entries: [makeModel({ id: "model-1", alias: "rst" })] });

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Reset 24h usage for rst"));
    });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ id: "model-1", body: { window: "24h" } }),
      expect.any(Object),
    );
  });

  it("opens an inline limit editor and saves both windows when the limit is edited", async () => {
    const mutate = vi.fn();
    vi.mocked(useSetModelTokenLimits).mockReturnValue({ mutate, isPending: false } as AnyMock);
    setHookState({
      entries: [
        makeModel({ id: "model-1", alias: "ed", limit_24h: null, limit_30d: 9_000_000 }),
      ],
    });

    mountPage();
    // Click the 24h limit ("-") to open the inline editor.
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Set 24h limit for ed"));
    });

    const input = screen.getByLabelText("Set 24h limit for ed") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { value: "750000" } });
      fireEvent.keyDown(input, { key: "Enter" });
    });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "model-1",
        // Preserves the untouched 30d limit, updates 24h.
        body: { limit_24h: 750000, limit_30d: 9_000_000 },
      }),
      expect.any(Object),
    );
  });
});

// ---------------------------------------------------------------------------
// ExtraArgsEditor — KV editor serialize/deserialize, add/remove, raw-JSON toggle
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — ExtraArgsEditor (bu-6jxcw)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setHookState({ entries: [makeModel()] });
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as AnyMock);
  });

  it("renders 'Raw JSON →' toggle button when in KV mode", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });
    expect(screen.getByLabelText("Switch to raw JSON editor")).toBeTruthy();
  });

  it("switching to raw-JSON mode reveals the Args (JSON array) textarea", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Switch to raw JSON editor"));
    });
    // Textarea now visible with args aria-label
    expect(screen.getByLabelText("Args (JSON array)")).toBeTruthy();
    // Toggle label changed to "← KV editor"
    expect(screen.getByLabelText("Switch to key-value editor")).toBeTruthy();
  });

  it("'+ Add arg' button appends a new row input", async () => {
    // Model starts with one existing arg so we can see two after adding
    setHookState({ entries: [makeModel({ extra_args: ["--foo"] })] });
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });
    // Should start with one row ("Arg 1")
    expect(screen.getByLabelText("Arg 1")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add arg/i }));
    });
    // Should now have two rows
    expect(screen.getByLabelText("Arg 1")).toBeTruthy();
    expect(screen.getByLabelText("Arg 2")).toBeTruthy();
  });

  it("remove-arg button removes the corresponding row", async () => {
    setHookState({ entries: [makeModel({ extra_args: ["--foo", "--bar"] })] });
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });
    // Should start with two rows
    expect(screen.getByLabelText("Arg 1")).toBeTruthy();
    expect(screen.getByLabelText("Arg 2")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByLabelText("Remove arg 1"));
    });
    // One row left
    expect(screen.getByLabelText("Arg 1")).toBeTruthy();
    expect(screen.queryByLabelText("Arg 2")).toBeNull();
  });

  it("KV rows serialize correctly into extra_args on save", async () => {
    // setHookState sets useUpdateModelCatalogEntry internally; override AFTER it.
    setHookState({ entries: [makeModel({ extra_args: [] })] });
    const mutate = vi.fn();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    // Add a row and type a CLI token
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add arg/i }));
    });
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Arg 1"), { target: { value: "--max-turns" } });
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save/i }));
    });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({ extra_args: ["--max-turns"] }),
      }),
      expect.any(Object),
    );
  });

  it("raw-JSON mode valid input deserializes and includes correct extra_args on save", async () => {
    // setHookState sets useUpdateModelCatalogEntry internally; override AFTER it.
    setHookState({ entries: [makeModel({ extra_args: [] })] });
    const mutate = vi.fn();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Switch to raw JSON editor"));
    });

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Args (JSON array)"), {
        target: { value: '["--config","model_reasoning_effort=high"]' },
      });
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save/i }));
    });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          extra_args: ["--config", "model_reasoning_effort=high"],
        }),
      }),
      expect.any(Object),
    );
  });

  it("raw-JSON mode rejects arrays with non-string elements", async () => {
    // Arrays like [1, 2] pass Array.isArray() but extra_args requires string[].
    // The editor must show an error and NOT call mutate.
    setHookState({ entries: [makeModel({ extra_args: [] })] });
    const mutate = vi.fn();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({ mutate, isPending: false } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Switch to raw JSON editor"));
    });

    // Type a JSON array with numeric elements — invalid for extra_args: string[]
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Args (JSON array)"), {
        target: { value: "[1, 2]" },
      });
    });

    // Error message must be visible
    expect(screen.getByText(/all elements must be strings/i)).toBeTruthy();

    // Attempting to save must be blocked — mutate must not be called
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save/i }));
    });
    expect(mutate).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Template dropdown — selecting a template pre-fills runtimeType + extraArgs
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — template dropdown (bu-6jxcw)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useCreateModelCatalogEntry).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as AnyMock);
    setHookState({ entries: [] });
  });

  it("renders the 'Use template' label in the Add Model dialog", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    });
    // Template section should be visible
    expect(screen.getByText(/use template/i)).toBeTruthy();
  });

  it("template dropdown is not rendered in the Edit dialog", async () => {
    setHookState({ entries: [makeModel()] });
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });
    // Should not have a template label in the edit dialog
    expect(screen.queryByText(/use template/i)).toBeNull();
  });

  it("renders the template Select trigger placeholder in the Add Model dialog", async () => {
    // Radix SelectContent (the dropdown options) is only mounted when the Select
    // is open — which requires pointer-event interactions not supported in JSDOM.
    // We verify the Select trigger (always mounted) renders with the correct placeholder.
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    });
    // The placeholder text must appear in the closed Select trigger.
    expect(screen.getByText("— pick a template (optional)")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Edit-alias warning (bu-6jxcw)
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — edit-alias warning (bu-6jxcw)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as AnyMock);
    setHookState({ entries: [makeModel({ alias: "claude-sonnet" })] });
  });

  it("does NOT show alias warning when dialog first opens (alias unchanged)", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });
    expect(screen.queryByText(/changing the alias may break/i)).toBeNull();
  });

  it("shows alias warning when the alias field is changed", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const aliasInput = screen.getByLabelText(/^alias$/i);
    await act(async () => {
      fireEvent.change(aliasInput, { target: { value: "claude-sonnet-v2" } });
    });

    expect(screen.getByText(/changing the alias may break/i)).toBeTruthy();
  });

  it("hides the alias warning when the alias is changed back to original", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Edit claude-sonnet"));
    });

    const aliasInput = screen.getByLabelText(/^alias$/i);
    await act(async () => {
      fireEvent.change(aliasInput, { target: { value: "different-alias" } });
    });
    expect(screen.getByText(/changing the alias may break/i)).toBeTruthy();

    await act(async () => {
      fireEvent.change(aliasInput, { target: { value: "claude-sonnet" } });
    });
    expect(screen.queryByText(/changing the alias may break/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Delete confirmation dialog — replaces window.confirm() (bu-6jxcw)
// ---------------------------------------------------------------------------

describe("SettingsModelsPage — delete confirmation dialog (bu-6jxcw)", () => {
  // Use an alias that doesn't contain "delete" to avoid aria-label collisions.
  const ALIAS = "my-catalog-entry";

  beforeEach(() => {
    vi.resetAllMocks();
    setHookState({ entries: [makeModel({ alias: ALIAS })] });
    vi.mocked(useUpdateModelCatalogEntry).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as AnyMock);
  });

  it("opens a confirmation dialog when Delete is clicked (not window.confirm)", async () => {
    // window.confirm should NOT be called (we use a Dialog instead)
    const confirmSpy = vi.spyOn(window, "confirm");

    mountPage();
    await act(async () => {
      // Use the row delete button's aria-label for unambiguous targeting.
      fireEvent.click(screen.getByLabelText(`Delete ${ALIAS}`));
    });

    // A dialog should appear — "Delete model:" title is the most specific indicator
    expect(screen.getByText(/delete model/i)).toBeTruthy();
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("Cancel button closes the dialog without calling mutate", async () => {
    const mutate = vi.fn();
    vi.mocked(useDeleteModelCatalogEntry).mockReturnValue({
      mutate,
      isPending: false,
    } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText(`Delete ${ALIAS}`));
    });

    // Dialog open — click Cancel
    const cancelBtn = screen.getByRole("button", { name: /^cancel$/i });
    await act(async () => {
      fireEvent.click(cancelBtn);
    });

    expect(mutate).not.toHaveBeenCalled();
    // Dialog closed — title gone
    expect(screen.queryByText(/delete model/i)).toBeNull();
  });

  it("the confirmation dialog mentions cascade-delete of butler overrides", async () => {
    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText(`Delete ${ALIAS}`));
    });
    expect(screen.getByText(/cascade-delete/i)).toBeTruthy();
  });

  it("clicking the Delete button in the dialog calls mutate with the model id", async () => {
    const mutate = vi.fn();
    vi.mocked(useDeleteModelCatalogEntry).mockReturnValue({
      mutate,
      isPending: false,
    } as AnyMock);

    mountPage();
    await act(async () => {
      fireEvent.click(screen.getByLabelText(`Delete ${ALIAS}`));
    });

    // Find the destructive "Delete →" button inside the confirmation dialog.
    // Both the row trigger and dialog confirm have text "Delete →", but only the
    // dialog one is visible while the dialog is open and it is the SECOND element.
    const deleteBtns = screen.getAllByRole("button", { name: /^delete →$/i });
    // Last one is the dialog's action button (dialog renders after row in the DOM).
    const dialogDeleteBtn = deleteBtns[deleteBtns.length - 1];
    await act(async () => {
      fireEvent.click(dialogDeleteBtn);
    });

    expect(mutate).toHaveBeenCalledWith(
      "model-1",
      expect.any(Object),
    );
  });
});

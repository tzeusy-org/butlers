/**
 * SettingsModelsPage — Vitest coverage for /settings/models (bu-1pxfh)
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
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SettingsModelsPage from "@/pages/SettingsModelsPage";
import type { ModelCatalogEntry } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-model-catalog", () => ({
  useModelCatalog: vi.fn(),
  useUpdateModelCatalogEntry: vi.fn(),
  useTestModelCatalogEntry: vi.fn(),
  useDeleteModelCatalogEntry: vi.fn(),
  useUpdateModelPriority: vi.fn(),
  useVerifyAllModels: vi.fn(),
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
  useModelCatalog,
  useUpdateModelCatalogEntry,
  useTestModelCatalogEntry,
  useDeleteModelCatalogEntry,
  useUpdateModelPriority,
  useVerifyAllModels,
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

/** A no-op mutation stub that covers the minimal mutation interface. */
function makeMutationStub() {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
    isIdle: true,
    error: null,
    data: undefined,
    reset: vi.fn(),
    status: "idle",
    submittedAt: 0,
    variables: undefined,
    context: undefined,
    failureCount: 0,
    failureReason: null,
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

  vi.mocked(useUpdateModelCatalogEntry).mockReturnValue(makeMutationStub() as AnyMock);
  vi.mocked(useTestModelCatalogEntry).mockReturnValue(makeMutationStub() as AnyMock);
  vi.mocked(useDeleteModelCatalogEntry).mockReturnValue(makeMutationStub() as AnyMock);
  vi.mocked(useUpdateModelPriority).mockReturnValue(makeMutationStub() as AnyMock);
  vi.mocked(useVerifyAllModels).mockReturnValue(makeMutationStub() as AnyMock);
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

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.resetAllMocks();
  setHookState({ entries: [] });
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

  it("renders usage_24h token count in the row", () => {
    const model = makeModel({ alias: "usage-model", usage_24h: 5000 });
    setHookState({ entries: [model] });
    const html = renderPage();
    expect(html).toContain("5,000 tok");
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

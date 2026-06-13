// @vitest-environment jsdom
/**
 * ButlerManagementTab — PromptEditModal mutation wiring tests.
 *
 * Covers:
 *  - Save button calls useUpdateButlerPrompt with edited prompt
 *  - On success: modal closes, success toast fires
 *  - On error: modal stays open, error toast fires
 *  - Save button disabled when draft unchanged from currentPrompt
 *  - Save button disabled while mutation is pending
 *  - Hook called at top of component (not inside conditional)
 *
 * bead: bu-g3ks5
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerManagementTab from "./ButlerManagementTab";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerHourlyActivity: vi.fn(() => ({ data: undefined })),
}));

vi.mock("@/hooks/use-butlers", () => ({
  useRuntimeConfig: vi.fn(() => ({ data: null, isLoading: false })),
  usePatchRuntimeConfig: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false, isError: false })),
}));

vi.mock("@/hooks/use-butler-management", () => ({
  useButlerPrompt: vi.fn(),
  useUpdateButlerPrompt: vi.fn(),
  useButlerPromptHistory: vi.fn(),
  useButlerTools: vi.fn(),
  useButlerMemoryAccess: vi.fn(),
  useKillButler: vi.fn(),
}));

vi.mock("@/hooks/use-model-catalog", () => ({
  useResolveModel: vi.fn(),
}));

import {
  useButlerPrompt,
  useUpdateButlerPrompt,
  useButlerPromptHistory,
  useButlerTools,
  useButlerMemoryAccess,
  useKillButler,
} from "@/hooks/use-butler-management";
import { useResolveModel } from "@/hooks/use-model-catalog";
import { useRuntimeConfig, usePatchRuntimeConfig } from "@/hooks/use-butlers";
import { toast } from "sonner";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab(butlerName = "general") {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter>
        <ButlerManagementTab butlerName={butlerName} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const PROMPT_TEXT = "You are a helpful butler.";

function setupDefaultHooks(mutateFn = vi.fn()) {
  vi.mocked(useButlerPrompt).mockReturnValue({
    data: { data: { version: 1, prompt: PROMPT_TEXT, updated_by: "owner" } },
    isLoading: false,
  } as ReturnType<typeof useButlerPrompt>);

  vi.mocked(useUpdateButlerPrompt).mockReturnValue({
    mutate: mutateFn,
    isPending: false,
  } as unknown as ReturnType<typeof useUpdateButlerPrompt>);

  vi.mocked(useButlerTools).mockReturnValue({
    data: { data: [] },
    isLoading: false,
  } as unknown as ReturnType<typeof useButlerTools>);

  vi.mocked(useButlerMemoryAccess).mockReturnValue({
    data: undefined,
    isLoading: false,
  } as unknown as ReturnType<typeof useButlerMemoryAccess>);

  vi.mocked(useKillButler).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useKillButler>);

  vi.mocked(useButlerPromptHistory).mockReturnValue({
    data: { data: [] },
    isLoading: false,
  } as unknown as ReturnType<typeof useButlerPromptHistory>);

  vi.mocked(useResolveModel).mockReturnValue({
    data: {
      data: {
        butler_name: "general",
        complexity: "medium",
        runtime_type: "codex",
        model_id: "claude-opus-4-8",
        extra_args: [],
        session_timeout_s: 1800,
        resolved: true,
        quota_blocked: false,
        usage_24h: 0,
        limit_24h: null,
        usage_30d: 0,
        limit_30d: null,
      },
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useResolveModel>);
}

/** Open the PromptEditModal by clicking "edit prompt →". */
function openEditModal() {
  const editButton = screen.getByText("edit prompt →");
  fireEvent.click(editButton);
}

// ---------------------------------------------------------------------------
// Tests: PromptEditModal mutation wiring
// ---------------------------------------------------------------------------

describe("PromptEditModal — mutation wiring", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });
  afterEach(() => cleanup());

  it("calls useUpdateButlerPrompt at component top level (hook always called)", () => {
    // PromptEditModal is only rendered when showEdit=true, so we must open
    // the modal to mount it. Once mounted, the hook must be called unconditionally
    // at the top of PromptEditModal (not inside any conditional) — React rules of hooks.
    setupDefaultHooks();
    renderTab();
    openEditModal();
    expect(vi.mocked(useUpdateButlerPrompt)).toHaveBeenCalledWith("general");
  });

  it("Save button is disabled when draft equals currentPrompt", () => {
    setupDefaultHooks();
    renderTab();
    openEditModal();
    const saveBtn = screen.getByText("save version →");
    expect(saveBtn).toHaveProperty("disabled", true);
  });

  it("Save button is disabled while isPending is true", () => {
    const mutateFn = vi.fn();
    setupDefaultHooks(mutateFn);
    vi.mocked(useUpdateButlerPrompt).mockReturnValue({
      mutate: mutateFn,
      isPending: true,
    } as unknown as ReturnType<typeof useUpdateButlerPrompt>);

    renderTab();
    openEditModal();
    const saveBtn = screen.getByText(/saving/);
    expect(saveBtn).toHaveProperty("disabled", true);
  });

  it("Save button calls mutation with edited prompt payload", () => {
    const mutateFn = vi.fn();
    setupDefaultHooks(mutateFn);
    renderTab();
    openEditModal();

    const textarea = screen.getByPlaceholderText("Enter system prompt…");
    fireEvent.change(textarea, { target: { value: "Updated prompt body" } });

    const saveBtn = screen.getByText("save version →");
    fireEvent.click(saveBtn);

    expect(mutateFn).toHaveBeenCalledWith(
      { prompt: "Updated prompt body" },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
  });

  it("onSuccess callback shows success toast and closes modal", () => {
    let capturedCallbacks: Record<string, (...args: unknown[]) => void> = {};
    const mutateFn = vi.fn((_payload, callbacks) => {
      capturedCallbacks = callbacks;
    });
    setupDefaultHooks(mutateFn);
    renderTab();
    openEditModal();

    // Edit textarea so Save is enabled
    const textarea = screen.getByPlaceholderText("Enter system prompt…");
    fireEvent.change(textarea, { target: { value: "New prompt" } });
    fireEvent.click(screen.getByText("save version →"));

    // Simulate successful mutation response wrapped in act to flush React state updates
    act(() => {
      capturedCallbacks.onSuccess?.();
    });

    expect(toast.success).toHaveBeenCalledWith("System prompt updated");
    // Modal should be gone — textarea no longer in DOM
    expect(screen.queryByPlaceholderText("Enter system prompt…")).toBeNull();
  });

  it("onError callback shows error toast and keeps modal open", () => {
    let capturedCallbacks: Record<string, (...args: unknown[]) => void> = {};
    const mutateFn = vi.fn((_payload, callbacks) => {
      capturedCallbacks = callbacks;
    });
    setupDefaultHooks(mutateFn);
    renderTab();
    openEditModal();

    const textarea = screen.getByPlaceholderText("Enter system prompt…");
    fireEvent.change(textarea, { target: { value: "New prompt" } });
    fireEvent.click(screen.getByText("save version →"));

    // Simulate error response wrapped in act to flush React state updates
    act(() => {
      capturedCallbacks.onError?.(new Error("API error"));
    });

    expect(toast.error).toHaveBeenCalledWith("API error");
    // Modal should remain open — textarea still in DOM
    expect(screen.getByPlaceholderText("Enter system prompt…")).toBeTruthy();
  });

  it("onError with non-Error value shows fallback message", () => {
    let capturedCallbacks: Record<string, (...args: unknown[]) => void> = {};
    const mutateFn = vi.fn((_payload, callbacks) => {
      capturedCallbacks = callbacks;
    });
    setupDefaultHooks(mutateFn);
    renderTab();
    openEditModal();

    const textarea = screen.getByPlaceholderText("Enter system prompt…");
    fireEvent.change(textarea, { target: { value: "New prompt" } });
    fireEvent.click(screen.getByText("save version →"));

    // Simulate non-Error failure wrapped in act to flush React state updates
    act(() => {
      capturedCallbacks.onError?.("some string error");
    });

    expect(toast.error).toHaveBeenCalledWith("Failed to save system prompt");
    expect(screen.getByPlaceholderText("Enter system prompt…")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Tests: RuntimeConfigCard mounted on the Manage tab (bu-dr03f.3)
//
// The editable runtime-config card was previously orphaned (mounted nowhere);
// these tests pin it to the Manage tab's §1 Identity & routing section and
// confirm an edit drives usePatchRuntimeConfig.
// ---------------------------------------------------------------------------

const RUNTIME_CONFIG = {
  butler_name: "general",
  core_groups: ["infra"] as string[] | null,
  max_concurrent: 3,
  max_queued: 10,
  seeded_at: null,
  updated_at: "2026-06-14T00:00:00Z",
  field_tiers: { max_concurrent: "cold", max_queued: "cold", core_groups: "cold" } as Record<
    string,
    "hot" | "cold"
  >,
};

describe("RuntimeConfigCard — mounted on Manage tab", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultHooks();
    vi.mocked(useRuntimeConfig).mockReturnValue({
      data: RUNTIME_CONFIG,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRuntimeConfig>);
  });
  afterEach(() => cleanup());

  it("renders the editable Runtime Config card with a Save control", () => {
    vi.mocked(usePatchRuntimeConfig).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof usePatchRuntimeConfig>);

    renderTab();

    // The orphaned read-only ConfigRows are gone; the editable card title is present.
    expect(screen.getByText("Runtime Config")).toBeTruthy();
    expect(screen.getByText("Save")).toBeTruthy();
  });

  it("surfaces the cold (restart required) tier badge for ceiling fields", () => {
    vi.mocked(usePatchRuntimeConfig).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof usePatchRuntimeConfig>);

    renderTab();

    // Cold fields render the honest "restart required" badge.
    expect(screen.getAllByText("restart required").length).toBeGreaterThan(0);
  });

  it("editing a field and saving calls usePatchRuntimeConfig.mutateAsync with the patch", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ restart_required: ["max_concurrent"] });
    vi.mocked(usePatchRuntimeConfig).mockReturnValue({
      mutateAsync,
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof usePatchRuntimeConfig>);

    renderTab();

    // Edit the Max Concurrent input (first number input in the card).
    const numberInputs = document.querySelectorAll('input[type="number"]');
    expect(numberInputs.length).toBeGreaterThan(0);
    fireEvent.change(numberInputs[0], { target: { value: "5" } });

    await act(async () => {
      fireEvent.click(screen.getByText("Save"));
    });

    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ max_concurrent: 5 }),
    );
  });
});

// ---------------------------------------------------------------------------
// Tests: §1 model display + session timeout (bu-dr03f.4)
//
// The old §1 rendered a fake "configured" literal for the model. It now shows
// the resolved model_id (from the model catalog, "medium" tier) and the
// resolved per-session timeout, both read-only with a deep link to the Models
// tab — session_timeout_s lives on public.model_catalog (core_073), not
// runtime_config.
// ---------------------------------------------------------------------------

describe("§1 model display + session timeout", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultHooks();
    vi.mocked(useRuntimeConfig).mockReturnValue({
      data: RUNTIME_CONFIG,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRuntimeConfig>);
    vi.mocked(usePatchRuntimeConfig).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof usePatchRuntimeConfig>);
  });
  afterEach(() => cleanup());

  it("shows the resolved model_id instead of a fake 'configured' literal", () => {
    renderTab();
    expect(screen.getByText("claude-opus-4-8")).toBeTruthy();
    expect(screen.queryByText("configured")).toBeNull();
  });

  it("surfaces the resolved session timeout and a link to the Models tab", () => {
    renderTab();
    expect(screen.getByText("1800s")).toBeTruthy();
    expect(screen.getByText("edit in models →")).toBeTruthy();
  });

  it("shows 'not configured' when the model does not resolve", () => {
    vi.mocked(useResolveModel).mockReturnValue({
      data: {
        data: {
          butler_name: "general",
          complexity: "medium",
          runtime_type: null,
          model_id: null,
          extra_args: [],
          session_timeout_s: null,
          resolved: false,
          quota_blocked: false,
          usage_24h: 0,
          limit_24h: null,
          usage_30d: 0,
          limit_30d: null,
        },
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useResolveModel>);
    renderTab();
    expect(screen.getByText("not configured")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Tests: prompt diff modal (bu-dr03f.4)
//
// The "diff vs v{n-1}" affordance was a dead preventDefault link. It now opens
// a modal that diffs the current head against the prior version using the
// prompt-history reader.
// ---------------------------------------------------------------------------

describe("prompt diff modal", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultHooks();
    vi.mocked(useRuntimeConfig).mockReturnValue({
      data: RUNTIME_CONFIG,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRuntimeConfig>);
    vi.mocked(usePatchRuntimeConfig).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof usePatchRuntimeConfig>);
    // Head is version 2 so the "diff vs v1" link renders.
    vi.mocked(useButlerPrompt).mockReturnValue({
      data: { data: { version: 2, prompt: "line a\nline c", updated_by: "owner" } },
      isLoading: false,
    } as ReturnType<typeof useButlerPrompt>);
    vi.mocked(useButlerPromptHistory).mockReturnValue({
      data: {
        data: [
          { butler_name: "general", prompt: "line a\nline c", version: 2, updated_at: "", updated_by: "owner" },
          { butler_name: "general", prompt: "line a\nline b", version: 1, updated_at: "", updated_by: "owner" },
        ],
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useButlerPromptHistory>);
  });
  afterEach(() => cleanup());

  it("opens a diff modal showing added/removed lines instead of a dead link", () => {
    renderTab();
    fireEvent.click(screen.getByText("diff vs v1 →"));
    // Modal header references the version range.
    expect(screen.getByText(/diff · v1 → v2/)).toBeTruthy();
    // The changed lines appear in the diff body.
    expect(screen.getByText("line b")).toBeTruthy();
    expect(screen.getByText("line c")).toBeTruthy();
  });
});

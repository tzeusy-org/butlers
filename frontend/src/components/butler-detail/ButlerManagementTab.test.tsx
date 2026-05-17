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
}));

vi.mock("@/hooks/use-butler-management", () => ({
  useButlerPrompt: vi.fn(),
  useUpdateButlerPrompt: vi.fn(),
  useButlerTools: vi.fn(),
  useButlerMemoryAccess: vi.fn(),
  useKillButler: vi.fn(),
}));

import {
  useButlerPrompt,
  useUpdateButlerPrompt,
  useButlerTools,
  useButlerMemoryAccess,
  useKillButler,
} from "@/hooks/use-butler-management";
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

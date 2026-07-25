// @vitest-environment jsdom
/**
 * ButlerDetailActions — Pause/Resume undo-window (bu-ep4ks.11).
 *
 * Pause/Resume used to fire setEligibility.mutate synchronously on click,
 * bypassing the undo-window pattern ButlersPage's board already established
 * for the identical restore action (bu-86c4c.15). This now schedules the
 * mutation behind useUndoWindow and offers an "Undo" toast action, mirroring
 * ButlersPage.interaction.test.tsx's coverage of the board's own restore chip.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

// sonner's real export is a CALLABLE function (toast(msg, opts)) that also
// carries .success/.error statics -- the undo-toast path calls it directly,
// existing call sites use the statics (mirrors ButlersPage.interaction.test.tsx).
vi.mock("sonner", () => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { toast: toastFn };
});

vi.mock("@/api/index.ts", () => ({ triggerButler: vi.fn() }));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(),
  useSetEligibility: vi.fn(),
}));

vi.mock("@/components/chat/ChatPanel", () => ({
  ChatPanel: () => null,
}));

import { ButlerDetailActions } from "./ButlerDetailActions";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";
import type { RegistryEntry } from "@/api/types";

const UNDO_WINDOW_MS = 5_000;

function mockRegistry(overrides: Partial<RegistryEntry> = {}) {
  const entry: RegistryEntry = {
    name: "general",
    endpoint_url: "http://x",
    description: null,
    modules: [],
    capabilities: [],
    last_seen_at: null,
    eligibility_state: "active",
    derived_eligibility_state: "active",
    liveness_ttl_seconds: 300,
    quarantined_at: null,
    quarantine_reason: null,
    route_contract_min: 1,
    route_contract_max: 1,
    eligibility_updated_at: null,
    registered_at: "2026-01-01T00:00:00Z",
    agent_type: "butler",
    ...overrides,
  };
  vi.mocked(useRegistry).mockReturnValue({
    data: { data: [entry] },
    isLoading: false,
  } as unknown as ReturnType<typeof useRegistry>);
}

const mockMutate = vi.fn();

function renderActions(butlerName = "general") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ButlerDetailActions butlerName={butlerName} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(useSetEligibility).mockReturnValue({
    mutate: mockMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useSetEligibility>);
});

afterEach(() => {
  // useUndoWindow's store is module-scoped (mirrors ButlersPage's own
  // scheduled-restore store) -- flush any pending schedule before the next
  // test so it never leaks across tests in this file.
  act(() => {
    vi.advanceTimersByTime(UNDO_WINDOW_MS);
  });
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe("ButlerDetailActions — Pause undo-window (bu-ep4ks.11)", () => {
  it("does not fire the mutation immediately on click", () => {
    mockRegistry({ eligibility_state: "active" });
    renderActions();

    fireEvent.click(screen.getByTestId("butler-pause"));

    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("fires setEligibility.mutate with quarantined once the undo window elapses", () => {
    mockRegistry({ eligibility_state: "active" });
    renderActions("general");

    fireEvent.click(screen.getByTestId("butler-pause"));
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    expect(mockMutate).toHaveBeenCalledOnce();
    expect(mockMutate).toHaveBeenCalledWith(
      { name: "general", state: "quarantined" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("shows an Undo toast action immediately on click, before the mutation fires", () => {
    mockRegistry({ eligibility_state: "active" });
    renderActions("general");

    fireEvent.click(screen.getByTestId("butler-pause"));

    expect(toast).toHaveBeenCalledWith(
      "Pausing general",
      expect.objectContaining({
        action: expect.objectContaining({ label: "Undo", onClick: expect.any(Function) }),
      }),
    );
  });

  it("cancels the pause entirely when Undo is clicked before the window elapses", () => {
    mockRegistry({ eligibility_state: "active" });
    renderActions("general");

    fireEvent.click(screen.getByTestId("butler-pause"));

    const [, opts] = vi.mocked(toast).mock.calls[0];
    const action = (opts as unknown as { action: { onClick: () => void } }).action;
    act(() => {
      action.onClick();
    });

    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS * 2);
    });

    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("shows the Pausing… pending label for the whole scheduled window, and disables the button", () => {
    mockRegistry({ eligibility_state: "active" });
    renderActions();

    fireEvent.click(screen.getByTestId("butler-pause"));

    const btn = screen.getByTestId("butler-pause") as HTMLButtonElement;
    expect(btn.textContent).toBe("Pausing…");
    expect(btn.disabled).toBe(true);
  });

  it("ignores a second click while a pause is already scheduled (no double-fire)", () => {
    mockRegistry({ eligibility_state: "active" });
    renderActions();

    const btn = screen.getByTestId("butler-pause");
    fireEvent.click(btn);
    fireEvent.click(btn);

    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    expect(mockMutate).toHaveBeenCalledOnce();
  });

  it("shows a success toast naming the resulting state once the mutation succeeds", () => {
    mockRegistry({ eligibility_state: "active" });
    mockMutate.mockImplementation((_vars: unknown, callbacks: { onSuccess?: () => void }) => {
      callbacks?.onSuccess?.();
    });
    renderActions("general");

    fireEvent.click(screen.getByTestId("butler-pause"));
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    expect(toast.success).toHaveBeenCalledWith("general paused");
  });

  it("shows an error toast when the mutation fails", () => {
    mockRegistry({ eligibility_state: "active" });
    mockMutate.mockImplementation(
      (_vars: unknown, callbacks: { onError?: (err: Error) => void }) => {
        callbacks?.onError?.(new Error("registry unreachable"));
      },
    );
    renderActions("general");

    fireEvent.click(screen.getByTestId("butler-pause"));
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    expect(toast.error).toHaveBeenCalledWith(
      "Failed to pause general",
      expect.objectContaining({ description: "registry unreachable" }),
    );
  });
});

describe("ButlerDetailActions — Resume undo-window (bu-ep4ks.11)", () => {
  it("fires setEligibility.mutate with active once the undo window elapses", () => {
    mockRegistry({ eligibility_state: "quarantined" });
    renderActions("general");

    fireEvent.click(screen.getByTestId("butler-pause"));
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    expect(mockMutate).toHaveBeenCalledWith(
      { name: "general", state: "active" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("shows a Resuming… pending label immediately on click", () => {
    mockRegistry({ eligibility_state: "quarantined" });
    renderActions("general");

    fireEvent.click(screen.getByTestId("butler-pause"));

    expect((screen.getByTestId("butler-pause") as HTMLButtonElement).textContent).toBe(
      "Resuming…",
    );
  });
});

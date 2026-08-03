// @vitest-environment jsdom
/**
 * ButlersPage — click-interaction tests for quarantine/stale restore chip.
 * (bu-p55gz)
 *
 * Complements the static-markup coverage in ButlersPage.test.tsx. Uses
 * @testing-library/react + fireEvent to exercise the restore chip click path
 * and assert that setEligibility.mutate is called with the correct payload.
 *
 * Two cases are tested:
 *   1. activity='quarantined' — chip shows QUARANTINED, mutate called with state='active'
 *   2. eligibility='stale'  — chip shows IDLE, mutate called with state='active'
 *
 * Additional assertion: clicking the restore chip does NOT trigger navigation
 * (e.stopPropagation is called; window.location.href must not change).
 *
 * Restore-with-reason-and-undo (JARVIS audit move 6, bu-86c4c.15): a click no
 * longer fires setEligibility.mutate instantly — it schedules it
 * RESTORE_UNDO_WINDOW_MS (5s) out behind an "Undo" toast action, mirroring
 * ApprovalsPage's scheduled-decision pattern (bu-86c4c.14). Tests below use
 * fake timers and advance past that window before asserting the mutation
 * fired; a dedicated describe block covers the Undo action itself.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { toast } from "sonner";

import ButlersPage from "@/pages/ButlersPage";
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board";
import {
  CommandRegistryProvider,
  useCommandMenuActions,
  type PaletteCommand,
} from "@/lib/command-registry";
import { ShortcutRegistryProvider } from "@/hooks/use-register-shortcut";

// ---------------------------------------------------------------------------
// Mocks — same modules as ButlersPage.test.tsx
// ---------------------------------------------------------------------------

// sonner's real export is a CALLABLE function (toast(msg, opts)) that also
// carries .success/.error statics -- the undo-toast path (bu-86c4c.15) calls
// it directly, existing call sites use the statics.
vi.mock("sonner", () => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { toast: toastFn };
});

/** How long a scheduled restore waits before firing (matches ButlersPage.tsx). */
const RESTORE_UNDO_WINDOW_MS = 5_000;

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}));

vi.mock("@/hooks/use-general", () => ({
  useSetEligibility: vi.fn(),
}));

import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useSetEligibility } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Fixture helpers (mirror ButlersPage.test.tsx helpers)
// ---------------------------------------------------------------------------

const NO_OP_REFETCH = vi.fn().mockResolvedValue(undefined);

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 0,
    butlerCount: 0,
    stafferCount: 0,
    active: 0,
    offline: 0,
    quarantined: 0,
    overdue: 0,
    totalSessions24h: 0,
    totalSpendToday: 0,
    avgLoadPct: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: NO_OP_REFETCH,
    heartbeatSourceError: false,
    registrySourceError: false,
    eligibilityUnavailable: 0,
    hasPerEntryErrors: false,
    costSourceError: false,
    sessionsSourceError: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
    unknown: overrides.unknown ?? 0,
  };
}

function makeRow(overrides: Partial<StatusBoardRow> = {}): StatusBoardRow {
  return {
    name: "general",
    type: "butler",
    description: null,
    status: "ok",
    activity: "idle",
    cellTone: "neutral",
    eligibility: "active",
    quarantineReason: null,
    quarantinedAt: null,
    sessions24h: 0,
    costToday: 0,
    loadPct: null,
    activeSessionCount: 0,
    lastRunISO: null,
    lastHeartbeatISO: null,
    heartbeatAgeSeconds: null,
    hourlyStripe: Array(24).fill(0),
    hourlyTotal: 0,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    cadenceSeconds: null,
    cadenceLabel: null,
    silenceSeconds: null,
    cadenceStatus: "unknown",
    ...overrides,
  };
}

const NEEDS_YOU_ACTIVITIES = new Set(["offline", "quarantined", "overdue"]);

function setHookState(rows: StatusBoardRow[], aggregates: StatusBoardAggregates) {
  const needsYou = rows.filter((r) => NEEDS_YOU_ACTIVITIES.has(r.activity));
  vi.mocked(useButlerStatusBoard).mockReturnValue({ rows, aggregates, needsYou });
}

// ---------------------------------------------------------------------------
// window.location stub — jsdom doesn't allow direct assignment of href in tests
// ---------------------------------------------------------------------------

let locationHref = "http://localhost/";

beforeEach(() => {
  locationHref = "http://localhost/";
  Object.defineProperty(window, "location", {
    writable: true,
    value: {
      ...window.location,
      get href() {
        return locationHref;
      },
      set href(v: string) {
        locationHref = v;
      },
    },
  });
});

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

const mockMutate = vi.fn();

beforeEach(() => {
  vi.useFakeTimers();

  vi.mocked(useSetEligibility).mockReturnValue({
    mutate: mockMutate,
    mutateAsync: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
    isIdle: true,
    error: null,
    data: undefined,
    reset: vi.fn(),
    context: undefined,
    failureCount: 0,
    failureReason: null,
    status: "idle",
    submittedAt: 0,
    variables: undefined,
  } as unknown as ReturnType<typeof useSetEligibility>);

  setHookState([], makeAggregates());
});

afterEach(() => {
  // ButlersPage's scheduled-restore store is module-scoped (bu-86c4c.15,
  // mirroring ApprovalsPage's scheduledDecisions), so any restore left
  // scheduled by a test (e.g. one that only asserts the immediate toast and
  // never advances past the undo window) would otherwise leak into the next
  // test in this file and make its "already scheduled" guard block a fresh
  // click. Flush it here, before tearing down fake timers, so every test
  // starts from an empty store.
  act(() => {
    vi.advanceTimersByTime(RESTORE_UNDO_WINDOW_MS);
  });
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

/** Click a restore chip, then advance past the undo window so the scheduled restore fires. */
function clickAndCommitRestore(chip: HTMLElement) {
  fireEvent.click(chip);
  act(() => {
    vi.advanceTimersByTime(RESTORE_UNDO_WINDOW_MS);
  });
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPage() {
  return render(
    <MemoryRouter>
      <ButlersPage />
    </MemoryRouter>,
  );
}

function renderKeyboardPage() {
  return render(
    <CommandRegistryProvider>
      <ShortcutRegistryProvider>
        <MemoryRouter>
          <ButlersPage />
        </MemoryRouter>
      </ShortcutRegistryProvider>
    </CommandRegistryProvider>,
  );
}

function CommandReader({ onRead }: { onRead: (commands: PaletteCommand[]) => void }) {
  onRead(useCommandMenuActions());
  return null;
}

describe("ButlersPage — keyboard board cursor", () => {
  it("moves across board tiles and exposes every butler as a palette destination", () => {
    const rows = [makeRow({ name: "general" }), makeRow({ name: "health" })];
    let commands: PaletteCommand[] = [];
    setHookState(rows, makeAggregates({ total: 2, butlerCount: 2 }));

    render(
      <CommandRegistryProvider>
        <ShortcutRegistryProvider>
          <MemoryRouter>
            <ButlersPage />
            <CommandReader onRead={(next) => (commands = next)} />
          </MemoryRouter>
        </ShortcutRegistryProvider>
      </CommandRegistryProvider>,
    );

    fireEvent.keyDown(window, { key: "ArrowRight" });
    const firstSelected = document.querySelector<HTMLElement>('[data-butler-name="general"]');
    expect(document.activeElement).toBe(firstSelected);
    fireEvent.keyDown(firstSelected!, { key: "ArrowRight" });

    const selected = document.querySelector('[data-butler-name="health"]');
    expect(selected?.getAttribute("data-board-cursor")).toBe("true");
    expect(document.activeElement).toBe(selected);
    expect(commands).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "open-butler-health",
          label: "Open health",
          keywords: expect.arrayContaining(["butler", "health"]),
        }),
      ]),
    );
  });

  it("preserves native Enter activation for a focused Restore button", async () => {
    vi.useRealTimers();
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));
    try {
      renderKeyboardPage();

      fireEvent.keyDown(window, { key: "ArrowRight" });
      const chip = screen.getByRole("button", { name: /quarantined/i });
      chip.focus();
      await userEvent.setup().keyboard("{Enter}");

      expect(toast).toHaveBeenCalledWith(
        "Restoring quarant",
        expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }),
      );
      const toastCall = vi.mocked(toast).mock.calls[0];
      (toastCall[1] as unknown as { action: { onClick: () => void } }).action.onClick();
    } finally {
      vi.useFakeTimers();
    }
  });
});

// ---------------------------------------------------------------------------
// Quarantined restore chip — click interaction
// ---------------------------------------------------------------------------

describe("ButlersPage — quarantine restore chip (interaction)", () => {
  it("calls setEligibility.mutate with { name, state: 'active' } once the undo window elapses", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    renderPage();

    // The restore chip is a <button> with text QUARANTINED
    const chip = screen.getByRole("button", { name: /quarantined/i });
    expect(chip).toBeDefined();

    // A click alone does not fire the mutation immediately -- it schedules it
    // (restore-with-reason-and-undo, bu-86c4c.15).
    fireEvent.click(chip);
    expect(mockMutate).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(RESTORE_UNDO_WINDOW_MS);
    });

    expect(mockMutate).toHaveBeenCalledOnce();
    expect(mockMutate).toHaveBeenCalledWith(
      { name: "quarant", state: "active" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("does not navigate when the quarantined restore chip is clicked (stopPropagation)", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    renderPage();

    const chip = screen.getByRole("button", { name: /quarantined/i });
    fireEvent.click(chip);

    // The button's onClick calls e.stopPropagation() before calling onRestore.
    // The outer div[role="link"]'s onClick sets window.location.href.
    // Since stopPropagation prevents the event bubbling, href must remain unchanged.
    expect(locationHref).toBe("http://localhost/");
  });
});

// ---------------------------------------------------------------------------
// Stale eligibility restore chip — click interaction
// ---------------------------------------------------------------------------

describe("ButlersPage — stale eligibility restore chip (interaction)", () => {
  it("calls setEligibility.mutate with { name, state: 'active' } once the undo window elapses", () => {
    const rows = [
      makeRow({ name: "stale-butler", activity: "idle", eligibility: "stale" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));

    renderPage();

    // Stale row: chip label is STALE (eligibility takes precedence over activity label).
    const chip = screen.getByRole("button", { name: "STALE" });
    expect(chip).toBeDefined();

    clickAndCommitRestore(chip);

    expect(mockMutate).toHaveBeenCalledOnce();
    expect(mockMutate).toHaveBeenCalledWith(
      { name: "stale-butler", state: "active" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("does not navigate when the stale restore chip is clicked (stopPropagation)", () => {
    const rows = [
      makeRow({ name: "stale-butler", activity: "idle", eligibility: "stale" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));

    renderPage();

    const chip = screen.getByRole("button", { name: "STALE" });
    fireEvent.click(chip);

    expect(locationHref).toBe("http://localhost/");
  });
});

// ---------------------------------------------------------------------------
// Toast feedback — success and error (bu-klxx6)
// ---------------------------------------------------------------------------

describe("ButlersPage — restore toast feedback", () => {
  it("shows a success toast when the mutate onSuccess callback fires", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    mockMutate.mockImplementation((_vars: unknown, callbacks: { onSuccess?: () => void }) => {
      callbacks?.onSuccess?.();
    });

    renderPage();
    clickAndCommitRestore(screen.getByRole("button", { name: /quarantined/i }));

    expect(toast.success).toHaveBeenCalledWith("quarant restored");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("shows an error toast when the mutate onError callback fires", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    mockMutate.mockImplementation((_vars: unknown, callbacks: { onError?: (err: Error) => void }) => {
      callbacks?.onError?.(new Error("server unavailable"));
    });

    renderPage();
    clickAndCommitRestore(screen.getByRole("button", { name: /quarantined/i }));

    expect(toast.error).toHaveBeenCalledWith(
      "Failed to restore quarant",
      expect.objectContaining({ description: "server unavailable" }),
    );
    expect(toast.success).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Undo action (JARVIS audit move 6, bu-86c4c.15)
// ---------------------------------------------------------------------------

describe("ButlersPage — restore undo action", () => {
  it("shows an Undo toast action immediately on click", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /quarantined/i }));

    expect(toast).toHaveBeenCalledWith(
      "Restoring quarant",
      expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }),
    );
  });

  it("cancels the restore entirely when Undo is clicked before the window elapses", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /quarantined/i }));

    const toastCall = vi.mocked(toast).mock.calls[0];
    const onUndoClick = (
      toastCall[1] as unknown as { action: { onClick: () => void } }
    ).action.onClick;
    act(() => {
      onUndoClick();
    });
    act(() => {
      vi.advanceTimersByTime(RESTORE_UNDO_WINDOW_MS * 2);
    });

    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("shows the RESTORING pending label for the whole scheduled window, before the mutation even fires", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /quarantined/i }));

    expect(mockMutate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /restoring/i })).toBeDefined();
  });

  it("does not double-fire the restore when the page unmounts and remounts mid-window", () => {
    // Regression test: an earlier version tracked the scheduled restore in a
    // plain `useState` on the page component. Unmounting (e.g. navigating
    // away) mid-window discarded that state without cancelling the pending
    // `window.setTimeout`, so a remount within the window saw an empty map,
    // let the chip be clicked again, and scheduled a SECOND independent
    // restore -- both timers eventually fired and setEligibility.mutate was
    // called twice for the same butler. The module-scoped store fixes this
    // by surviving the unmount.
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    const { unmount } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: /quarantined/i }));
    expect(mockMutate).not.toHaveBeenCalled();

    // Navigate away, then back, before the undo window elapses.
    unmount();
    renderPage();

    // The remounted page must see the restore as already scheduled -- its
    // chip should read "Restoring…", not the clickable quarantined chip, so
    // a second click cannot even be attempted.
    expect(screen.getByRole("button", { name: /restoring/i })).toBeDefined();

    act(() => {
      vi.advanceTimersByTime(RESTORE_UNDO_WINDOW_MS);
    });

    expect(mockMutate).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// Pending state — chip disabled while mutation is in flight (bu-klxx6)
// ---------------------------------------------------------------------------

describe("ButlersPage — restore chip pending/disabled state", () => {
  it("disables the restore chip for the specific butler whose mutation is pending", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    vi.mocked(useSetEligibility).mockReturnValue({
      mutate: mockMutate,
      mutateAsync: vi.fn(),
      isPending: true,
      isSuccess: false,
      isError: false,
      isIdle: false,
      error: null,
      data: undefined,
      reset: vi.fn(),
      context: undefined,
      failureCount: 0,
      failureReason: null,
      status: "pending",
      submittedAt: Date.now(),
      variables: { name: "quarant", state: "active" },
    } as unknown as ReturnType<typeof useSetEligibility>);

    renderPage();

    // The chip label changes to RESTORING… while pending; find it by that text.
    const chip = screen.getByRole("button", { name: /restoring/i });
    expect(chip).toBeDefined();
    // HTMLButtonElement.disabled is true when the disabled attribute is present.
    expect((chip as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not disable the chip for a different butler while another is pending", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
      makeRow({ name: "other", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 2, butlerCount: 2, quarantined: 2 }));

    vi.mocked(useSetEligibility).mockReturnValue({
      mutate: mockMutate,
      mutateAsync: vi.fn(),
      isPending: true,
      isSuccess: false,
      isError: false,
      isIdle: false,
      error: null,
      data: undefined,
      reset: vi.fn(),
      context: undefined,
      failureCount: 0,
      failureReason: null,
      status: "pending",
      submittedAt: Date.now(),
      variables: { name: "quarant", state: "active" },
    } as unknown as ReturnType<typeof useSetEligibility>);

    renderPage();

    // "quarant" chip is pending → disabled.
    const pendingChip = screen.getByRole("button", { name: /restoring/i });
    expect((pendingChip as HTMLButtonElement).disabled).toBe(true);

    // "other" chip is still enabled with its normal label.
    const otherChip = screen.getByRole("button", { name: /quarantined/i });
    expect((otherChip as HTMLButtonElement).disabled).toBe(false);
  });
});

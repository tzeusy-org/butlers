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
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { toast } from "sonner";

import ButlersPage from "@/pages/ButlersPage";
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board";

// ---------------------------------------------------------------------------
// Mocks — same modules as ButlersPage.test.tsx
// ---------------------------------------------------------------------------

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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
    sourcesPartiallyDegraded: false,
    ...overrides,
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
    sessions24h: 0,
    costToday: 0,
    loadPct: null,
    lastRunISO: null,
    hourlyStripe: Array(24).fill(0),
    hourlyTotal: 0,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    ...overrides,
  };
}

function setHookState(rows: StatusBoardRow[], aggregates: StatusBoardAggregates) {
  vi.mocked(useButlerStatusBoard).mockReturnValue({ rows, aggregates });
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
  cleanup();
  vi.resetAllMocks();
});

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

// ---------------------------------------------------------------------------
// Quarantined restore chip — click interaction
// ---------------------------------------------------------------------------

describe("ButlersPage — quarantine restore chip (interaction)", () => {
  it("calls setEligibility.mutate with { name, state: 'active' } when quarantined chip is clicked", () => {
    const rows = [
      makeRow({ name: "quarant", activity: "quarantined", eligibility: "quarantined", cellTone: "red" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1, quarantined: 1 }));

    renderPage();

    // The restore chip is a <button> with text QUARANTINED
    const chip = screen.getByRole("button", { name: /quarantined/i });
    expect(chip).toBeDefined();

    fireEvent.click(chip);

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
  it("calls setEligibility.mutate with { name, state: 'active' } when stale chip is clicked", () => {
    const rows = [
      makeRow({ name: "stale-butler", activity: "idle", eligibility: "stale" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));

    renderPage();

    // Stale row: chip label is STALE (eligibility takes precedence over activity label).
    const chip = screen.getByRole("button", { name: /stale/i });
    expect(chip).toBeDefined();

    fireEvent.click(chip);

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

    const chip = screen.getByRole("button", { name: /stale/i });
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
    fireEvent.click(screen.getByRole("button", { name: /quarantined/i }));

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
    fireEvent.click(screen.getByRole("button", { name: /quarantined/i }));

    expect(toast.error).toHaveBeenCalledWith(
      "Failed to restore quarant",
      expect.objectContaining({ description: "server unavailable" }),
    );
    expect(toast.success).not.toHaveBeenCalled();
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

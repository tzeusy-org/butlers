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

import ButlersPage from "@/pages/ButlersPage";
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board";

// ---------------------------------------------------------------------------
// Mocks — same modules as ButlersPage.test.tsx
// ---------------------------------------------------------------------------

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
    paused: 0,
    awaiting: 0,
    quarantined: 0,
    totalSessions24h: 0,
    totalSpendToday: 0,
    avgLoadPct: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: NO_OP_REFETCH,
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
    expect(mockMutate).toHaveBeenCalledWith({ name: "quarant", state: "active" });
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

    // Stale row: activity is 'idle' so the chip label is IDLE
    const chip = screen.getByRole("button", { name: /idle/i });
    expect(chip).toBeDefined();

    fireEvent.click(chip);

    expect(mockMutate).toHaveBeenCalledOnce();
    expect(mockMutate).toHaveBeenCalledWith({ name: "stale-butler", state: "active" });
  });

  it("does not navigate when the stale restore chip is clicked (stopPropagation)", () => {
    const rows = [
      makeRow({ name: "stale-butler", activity: "idle", eligibility: "stale" }),
    ];
    setHookState(rows, makeAggregates({ total: 1, butlerCount: 1 }));

    renderPage();

    const chip = screen.getByRole("button", { name: /idle/i });
    fireEvent.click(chip);

    expect(locationHref).toBe("http://localhost/");
  });
});

// @vitest-environment jsdom
/**
 * ButlerHeartbeatTile — Trigger tick remedy interaction (JARVIS audit move 6,
 * bu-86c4c.15).
 *
 * The static-markup suite (ButlerHeartbeatTile.test.tsx) covers rendering;
 * this file exercises the actual click -> real POST /api/butlers/{name}/tick
 * mutation wiring, disabled/pending state, and toast feedback.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/use-butler-status-board", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-butler-status-board")>();
  return { ...actual, useButlerStatusBoard: vi.fn() };
});

vi.mock("@/hooks/use-butlers", () => ({
  useForceButlerTick: vi.fn(),
}));

vi.mock("sonner", () => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { toast: toastFn };
});

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}));

import { ButlerHeartbeatTile } from "./ButlerHeartbeatTile";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useForceButlerTick } from "@/hooks/use-butlers";
import { toast } from "sonner";
import type { StatusBoardAggregates, StatusBoardRow } from "@/hooks/use-butler-status-board";

function makeRow(overrides: Partial<StatusBoardRow> = {}): StatusBoardRow {
  return {
    name: "overdue-butler",
    type: "butler",
    description: null,
    status: "ok",
    activity: "overdue",
    cellTone: "amber",
    eligibility: "active",
    quarantineReason: null,
    quarantinedAt: null,
    sessions24h: 0,
    costToday: null,
    loadPct: null,
    activeSessionCount: 0,
    lastRunISO: null,
    lastHeartbeatISO: "2026-05-03T10:00:00Z",
    heartbeatAgeSeconds: 30,
    hourlyStripe: Array(24).fill(0),
    hourlyTotal: 0,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    cadenceSeconds: 86400,
    cadenceLabel: "daily",
    silenceSeconds: 5 * 86400,
    cadenceStatus: "overdue",
    ...overrides,
  };
}

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 1,
    butlerCount: 1,
    stafferCount: 0,
    active: 0,
    offline: 0,
    quarantined: 0,
    overdue: 1,
    totalSessions24h: 0,
    totalSpendToday: 0,
    avgLoadPct: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    heartbeatSourceError: false,
    registrySourceError: false,
    eligibilityUnavailable: 0,
    hasPerEntryErrors: false,
    costSourceError: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
  };
}

const mockMutate = vi.fn();

function renderTile() {
  return render(
    <MemoryRouter>
      <ButlerHeartbeatTile />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockMutate.mockClear();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();

  vi.mocked(useButlerStatusBoard).mockReturnValue({
    rows: [makeRow()],
    aggregates: makeAggregates(),
    needsYou: [makeRow()],
  });

  vi.mocked(useForceButlerTick).mockReturnValue({
    mutate: mockMutate,
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof useForceButlerTick>);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ButlerHeartbeatTile — Trigger tick click wiring", () => {
  it("calls the real force-tick mutation with the butler name on click", () => {
    renderTile();

    fireEvent.click(screen.getByRole("button", { name: "Trigger tick" }));

    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(mockMutate).toHaveBeenCalledWith(
      "overdue-butler",
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("shows a success toast using the backend's message on success", () => {
    renderTile();
    fireEvent.click(screen.getByRole("button", { name: "Trigger tick" }));

    const onSuccess = mockMutate.mock.calls[0][1].onSuccess;
    onSuccess({ data: { success: true, message: "ran 2 due schedules" } });

    expect(toast.success).toHaveBeenCalledWith("overdue-butler: ran 2 due schedules");
  });

  it("shows an error toast when the tick call rejects", () => {
    renderTile();
    fireEvent.click(screen.getByRole("button", { name: "Trigger tick" }));

    const onError = mockMutate.mock.calls[0][1].onError;
    onError(new Error("butler unreachable"));

    expect(toast.error).toHaveBeenCalledWith(
      "Failed to trigger tick for overdue-butler",
      expect.objectContaining({ description: "butler unreachable" }),
    );
  });

  it("disables the control and shows a pending label while the tick is in flight", () => {
    vi.mocked(useForceButlerTick).mockReturnValue({
      mutate: mockMutate,
      isPending: true,
      variables: "overdue-butler",
    } as unknown as ReturnType<typeof useForceButlerTick>);

    renderTile();

    const button = screen.getByRole("button", { name: "Triggering…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

});

// @vitest-environment jsdom
/**
 * ButlerHeartbeatTile: a forced tick the API answers 202 `running` (it outlasted the accept window)
 * reads as running, not as triggered or failed (bu-o5jqmh). Real failures stay errors.
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
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() });
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
    sessionsSourceError: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
    unknown: overrides.unknown ?? 0,
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
  vi.mocked(toast.warning).mockClear();
  vi.mocked(toast.info).mockClear();

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

describe("ButlerHeartbeatTile running forced tick", () => {
  function clickAndGetOnSuccess() {
    renderTile();
    fireEvent.click(screen.getByRole("button", { name: "Trigger tick" }));
    return mockMutate.mock.calls[0][1].onSuccess;
  }

  it("shows an info toast, not success, when the tick is still running", () => {
    const onSuccess = clickAndGetOnSuccess();

    onSuccess({ data: { success: true, running: true, message: null } });

    expect(toast.info).toHaveBeenCalledWith("overdue-butler: tick is running; it may take a few minutes");
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("still shows success when the tick ran", () => {
    const onSuccess = clickAndGetOnSuccess();

    onSuccess({ data: { success: true, running: false, message: "ran 2 due schedules" } });

    expect(toast.success).toHaveBeenCalledWith("overdue-butler: ran 2 due schedules");
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("still shows a red error for a failed tick", () => {
    const onSuccess = clickAndGetOnSuccess();

    onSuccess({ data: { success: false, message: "boom" } });

    expect(toast.error).toHaveBeenCalledWith("overdue-butler: tick did not complete successfully");
    expect(toast.info).not.toHaveBeenCalled();
  });
});

// @vitest-environment jsdom
/**
 * ButlerHeartbeatTile: a busy forced tick reads as "already running", not as a failure
 * (bu-0w76ze). POST /api/butlers/{name}/tick answers `success: false, busy: true` with the
 * daemon's message when a tick is already running; a real failure is `success: false`
 * without `busy`.
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
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn() });
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

describe("ButlerHeartbeatTile busy forced tick", () => {
  function clickAndGetOnSuccess() {
    renderTile();
    fireEvent.click(screen.getByRole("button", { name: "Trigger tick" }));
    return mockMutate.mock.calls[0][1].onSuccess;
  }

  it("says a tick is already running, using the API's message, when the tick reports busy", () => {
    const onSuccess = clickAndGetOnSuccess();

    onSuccess({
      data: {
        success: false,
        busy: true,
        message: "a scheduler tick is already running; try again when it finishes",
      },
    });

    expect(toast.warning).toHaveBeenCalledWith(
      "overdue-butler: a scheduler tick is already running; try again when it finishes",
    );
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("falls back to its own already-running copy when a busy reply has no message", () => {
    const onSuccess = clickAndGetOnSuccess();

    onSuccess({ data: { success: false, busy: true, message: null } });

    expect(toast.warning).toHaveBeenCalledWith("overdue-butler: a tick is already running");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("still reads as a failure when the tick fails without the busy flag", () => {
    const onSuccess = clickAndGetOnSuccess();

    onSuccess({ data: { success: false, message: "boom" } });

    expect(toast.error).toHaveBeenCalledWith("overdue-butler: tick did not complete successfully");
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it("still reads as success when the tick ran", () => {
    const onSuccess = clickAndGetOnSuccess();

    onSuccess({ data: { success: true, busy: false, message: "ran 2 due schedules" } });

    expect(toast.success).toHaveBeenCalledWith("overdue-butler: ran 2 due schedules");
    expect(toast.warning).not.toHaveBeenCalled();
  });
});

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
import { cleanup, fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useButlerHeartbeats } from "@/hooks/use-system";
import { MemoryRouter, useLocation } from "react-router";

vi.mock("@/hooks/use-butler-status-board", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-butler-status-board")>();
  return { ...actual, useButlerStatusBoard: vi.fn() };
});

vi.mock("@/hooks/use-system", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-system")>();
  return {...actual, useButlerHeartbeats: vi.fn()};
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
    sessionsSourceError: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
    unknown: overrides.unknown ?? 0,
  };
}

const mockMutate = vi.fn();

function LocationEcho() { return <output data-testid="current-path">{useLocation().pathname}</output>; }

function renderTile() {
  return render(
    <MemoryRouter initialEntries={["/system"]}>
      <LocationEcho />
      <ButlerHeartbeatTile />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(useButlerHeartbeats).mockReturnValue({ data: {data: {butlers: []}}, isPending: false, isError: false, isFetching: false, refetch: vi.fn() } as unknown as ReturnType<typeof useButlerHeartbeats>);
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
  vi.unstubAllGlobals();
});

describe("ButlerHeartbeatTile — Trigger tick click wiring", () => {
  it("calls the real force-tick mutation with the butler name on click", () => {
    renderTile();

    fireEvent.click(screen.getByRole("button", { name: "Trigger tick" }));

    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("current-path").textContent).toBe("/system");
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
    fireEvent.click(button);
    expect(mockMutate).not.toHaveBeenCalled();
  });

});

describe("ButlerHeartbeatTile -- real heartbeat query refresh", () => {
  it("acknowledges loading and empty data, retains rows on failures and recovers current observation", async () => {
    const actual = await vi.importActual<typeof import("@/hooks/use-system")>("@/hooks/use-system");
    vi.mocked(useButlerHeartbeats).mockImplementation(actual.useButlerHeartbeats);
    let response: Response | null = null;
    let release: ((response: Response) => void) | undefined;
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      return response ? Promise.resolve(response.clone()) : new Promise<Response>((resolve) => {release = resolve;});
    }));
    const client = new QueryClient({defaultOptions: {queries: {retry: false, gcTime: 0}}});
    const known = {name: "overdue-butler", last_heartbeat_at: "2001-01-01T00:00:00Z", last_probe_at: "2080-01-01T00:00:00Z",
      last_session_at: null, active_session_count: 0, heartbeat_age_seconds: 999999,
      observation_state: "healthy", administrative_policy: "paused", route_compatibility: "compatible", effective_eligibility: "paused"};
    const success = (butlers: unknown[]) => new Response(JSON.stringify({data: {butlers}, meta: {}}), {status: 200});
    const values = () => [...screen.getByRole("link", {name: "View overdue-butler"}).querySelectorAll("dd")].map((value) => value.textContent);
    const refresh = async () => { await act(async () => {await client.refetchQueries({queryKey: ["system-butler-heartbeats"]});}); };
    try {
      render(<QueryClientProvider client={client}><MemoryRouter><ButlerHeartbeatTile /></MemoryRouter></QueryClientProvider>);
      expect(screen.getByText("Loading heartbeat facts…").textContent).toContain("Loading");
      expect(values()[0]).toBe("unknown");
      await waitFor(() => expect(release).toBeTypeOf("function"));
      await act(async () => {release!(new Response(JSON.stringify({error: {code: "UNAVAILABLE", message: "hidden server text"}}), {status: 503}));});
      await waitFor(() => expect(screen.getByText(/Heartbeat facts unavailable/).textContent).toContain("unavailable"));
      expect(values()).toEqual(["unknown", "unknown", "unknown", "unknown"]);
      response = success([]); await refresh();
      await waitFor(() => expect(screen.getByText("No heartbeat facts recorded.").textContent).toContain("No heartbeat"));
      expect(values()[0]).toBe("unknown");
      response = success([known]); await refresh();
      await waitFor(() => expect(values()).toEqual(["healthy", "paused", "compatible", "paused"]));
      for (const code of [401, 403, 503]) {
        response = new Response(JSON.stringify({error: {code: "UNAVAILABLE", message: "hidden server text"}}), {status: code});
        await refresh();
        await waitFor(() => expect(values()[0]).toBe("unknown"));
        expect(screen.getByText(/Last-known facts/).textContent).toContain("current observation unknown");
        expect(screen.queryByText("hidden server text")).toBeNull();
        response = success([known]); await refresh();
        await waitFor(() => expect(values()[0]).toBe("healthy"));
      }
      response = success([{...known, observation_state: "unavailable", administrative_policy: "active", effective_eligibility: "ineligible"}]); await refresh();
      await waitFor(() => expect(values()).toEqual(["unavailable", "active", "compatible", "ineligible"]));
      expect(mockMutate).not.toHaveBeenCalled();
      expect(calls.every((entry) => entry === "GET /api/system/butlers/heartbeat")).toBe(true);
    } finally { cleanup(); client.clear(); }
  });
});

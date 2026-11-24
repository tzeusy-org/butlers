// @vitest-environment jsdom
/** Dashboard-shell: failed reads never become zero; cached reads disclose staleness. */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";

import * as api from "@/api/index.ts";
import * as confirms from "@/api/confirms";
import type { NotificationStats } from "@/api/types";
import { CommandRegistryProvider } from "@/lib/command-registry";
import NotificationsPage from "@/pages/NotificationsPage";

vi.mock("@/api/index.ts", async (original) => ({
  ...await original<typeof import("@/api/index.ts")>(),
  getNotifications: vi.fn(),
  getNotificationStats: vi.fn(),
}));
vi.mock("@/api/confirms", async (original) => ({
  ...await original<typeof import("@/api/confirms")>(),
  getConfirms: vi.fn(),
}));
// Polling transport is independent of the read/error/retry identity under test.
vi.mock("@/hooks/use-bus-aware-poll-interval", () => ({ useBusAwarePollInterval: () => 60_000 }));

type StatsResponse = Awaited<ReturnType<typeof api.getNotificationStats>>;
const clients: QueryClient[] = [];
const stats = (overrides: Partial<NotificationStats> = {}): StatsResponse => ({
  data: { total: 10, sent: 8, failed: 2, by_channel: {}, by_butler: {}, ...overrides },
  meta: {},
});
let allTime: () => Promise<StatsResponse>;

function Location() {
  return <output aria-label="Current filters">{useLocation().search}</output>;
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: Infinity } } });
  clients.push(client);
  render(<QueryClientProvider client={client}><MemoryRouter><CommandRegistryProvider><NotificationsPage /><Location /></CommandRegistryProvider></MemoryRouter></QueryClientProvider>);
  return client;
}

const allTimeCalls = () => vi.mocked(api.getNotificationStats).mock.calls.filter(([params]) => params === undefined).length;
const windowCalls = () => vi.mocked(api.getNotificationStats).mock.calls.filter(([params]) => params !== undefined).length;

beforeEach(() => {
  vi.resetAllMocks();
  // Keep the independent confirm source healthy while exercising notification-stat failures.
  vi.mocked(confirms.getConfirms).mockResolvedValue({ data: [], meta: { next_cursor: null } });
  allTime = () => Promise.resolve(stats());
  vi.mocked(api.getNotificationStats).mockImplementation((params) => params === undefined ? allTime() : Promise.resolve(stats({ failed: 0 })));
  vi.mocked(api.getNotifications).mockResolvedValue({ data: [], meta: { total: 0, offset: 0, limit: 20, has_more: false } });
});
afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
});

it("names a failed all-time read without fabricated KPIs and retries only that source", async () => {
  allTime = () => Promise.reject(new Error("private transport detail"));
  mount();
  const error = await screen.findByRole("alert");
  expect(error.textContent).toMatch(/all-time notification stat.*unavailable/i);
  expect(screen.queryByTestId("stat-value-total")).toBeNull();
  expect(screen.queryByTestId("stat-value-failure-rate")).toBeNull();
  expect(screen.queryByText("private transport detail")).toBeNull();
  await screen.findByText("No notifications found.");
  const listReads = vi.mocked(api.getNotifications).mock.calls.length;
  const verdictReads = windowCalls();
  const reads = allTimeCalls();
  let finish!: (value: StatsResponse) => void;
  allTime = () => new Promise((resolve) => { finish = resolve; });
  fireEvent.click(within(error).getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(allTimeCalls()).toBe(reads + 1));
  expect(screen.queryByTestId("stat-value-total")).toBeNull();
  await act(async () => finish(stats({ total: 25, sent: 20, failed: 5 })));
  await waitFor(() => expect(screen.getByTestId("stat-value-total").textContent).toBe("25"));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(vi.mocked(api.getNotifications)).toHaveBeenCalledTimes(listReads);
  expect(windowCalls()).toBe(verdictReads);
});

it("labels cached stats stale and recovers through their own retry without losing filter or acknowledgement controls", async () => {
  const client = mount();
  await waitFor(() => expect(screen.getByTestId("stat-value-total").textContent).toBe("10"));
  allTime = () => Promise.reject(new Error("private refresh detail"));
  await act(() => client.refetchQueries({ queryKey: ["notification-stats", undefined], exact: true }));
  const warning = await screen.findByRole("alert");
  expect(warning.textContent).toMatch(/all-time notification stat.*stale/i);
  expect(screen.getByTestId("stat-value-failed").textContent).toBe("2");
  expect(screen.queryByText("private refresh detail")).toBeNull();
  const listReads = vi.mocked(api.getNotifications).mock.calls.length;
  const verdictReads = windowCalls();
  const reads = allTimeCalls();
  allTime = () => Promise.resolve(stats({ total: 15, sent: 12, failed: 3 }));
  fireEvent.click(within(warning).getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.getByTestId("stat-value-total").textContent).toBe("15"));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(allTimeCalls()).toBe(reads + 1);
  expect(windowCalls()).toBe(verdictReads);
  expect(vi.mocked(api.getNotifications)).toHaveBeenCalledTimes(listReads);
  fireEvent.keyDown(screen.getByTestId("stat-tile-failed"), { key: "Enter" });
  await waitFor(() => expect(screen.getByLabelText("Current filters").textContent).toContain("status=failed"));
  fireEvent.click(screen.getByRole("button", { name: "Acknowledge all failed (3)" }));
  expect(await screen.findByRole("alertdialog")).not.toBeNull();
  expect(screen.getByText("Acknowledge all 3 failed notifications?")).not.toBeNull();
});

it("keeps pending stats unknown then displays genuine successful zero counts", async () => {
  let finish!: (value: StatsResponse) => void;
  allTime = () => new Promise((resolve) => { finish = resolve; });
  mount();
  await waitFor(() => expect(allTimeCalls()).toBe(1));
  expect(screen.queryByTestId("stat-value-total")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  await act(async () => finish(stats({ total: 0, sent: 0, failed: 0, source_available: true })));
  await waitFor(() => expect(screen.getByTestId("stat-value-total").textContent).toBe("0"));
  expect(screen.getByTestId("stat-value-failure-rate").textContent).toBe("0.0%");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: /Acknowledge all failed/ })).toBeNull();
});

it("preserves successful unavailable-source placeholders and labels them stale if their refresh fails", async () => {
  allTime = () => Promise.resolve(stats({ total: 0, sent: 0, failed: 0, source_available: false }));
  const client = mount();
  await waitFor(() => expect(screen.getByTestId("stat-value-total").textContent).toBe("—"));
  expect(screen.getByTestId("stat-value-failure-rate").textContent).toBe("—");
  expect(screen.queryByRole("alert")).toBeNull();
  allTime = () => Promise.reject(new Error("unavailable refresh"));
  await act(() => client.refetchQueries({ queryKey: ["notification-stats", undefined], exact: true }));
  expect((await screen.findByRole("alert")).textContent).toMatch(/stale/i);
  expect(screen.getByTestId("stat-value-total").textContent).toBe("—");
  expect(screen.getByTestId("stat-value-failure-rate").className).not.toContain("var(--green)");
});

// Spec: REQ-dashboard-api-067
// @vitest-environment jsdom

import { act } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";

// Exercise real page, query hooks and apiFetch; only the SSE transport is dormant.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ health: "healthy", status: "open", subscribe: () => () => {} }),
}));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("keeps real HTTP invitation evidence, pagination and details honest through failure/loading/empty", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const invitation = { entry_id: "invitation-1", title: "Provider invitation", start_at: "2026-07-01T09:00:00Z",
    end_at: "2026-07-01T10:00:00Z", timezone: "UTC", organizer: "Organizer", organizer_source: "event",
    butler_name: "general", conflict_issues: [{ kind: "overlap", date: "2026-07-01", summary: "Matched radar overlap",
      severity: "warning", proposal_ids: [], events: [{ entry_id: "invitation-1", title: "Provider invitation",
        start_at: "2026-07-01T09:00:00Z", end_at: "2026-07-01T10:00:00Z", timezone: "UTC", status: "confirmed" }] }] };
  const entry = { ...invitation, view: "user", source_type: "provider_event", source_key: "google:primary",
    event_id: "event-1", all_day: false, calendar_id: "primary", provider_event_id: "provider-1",
    schedule_id: null, reminder_id: null, rrule: null, cron: null, until_at: null, status: "active",
    sync_state: "fresh", editable: false, metadata: {}, linked_people: [] };
  let state: "partial" | "failed" | "empty" | "pending" = "partial";
  let release: (() => void) | undefined;
  const fetch = vi.fn(async (input: string, init?: RequestInit) => {
    expect(init?.method ?? "GET").toBe("GET");
    const url = new URL(input, "http://localhost");
    let data: unknown;
    if (url.pathname.endsWith("/invitations")) {
      expect(url.searchParams.get("start")).toBeTruthy();
      expect(url.searchParams.get("end")).toBeTruthy();
      expect(url.searchParams.get("timezone")).toBe("UTC");
      if (state === "failed") return new Response(JSON.stringify({ error: { code: "unavailable", message: "Unavailable" } }), { status: 503 });
      if (state === "pending") await new Promise<void>((resolve) => { release = resolve; });
      data = state === "empty" || state === "pending"
        ? { entries: [], issues_available: true, conflicts_available: true, sources_degraded: [], has_more: false, next_cursor: null }
        : { entries: url.searchParams.has("cursor") ? [] : [invitation], issues_available: false,
            conflicts_available: false, sources_degraded: ["relationship"], has_more: !url.searchParams.has("cursor"), next_cursor: url.searchParams.has("cursor") ? null : "page2" };
    } else if (url.pathname.includes("/entries/")) data = entry;
    else if (url.pathname.endsWith("/conflicts")) data = { issues: [], issues_available: true, scan_window: { start: "2026-07-01", end: "2026-07-02" } };
    else if (url.pathname.endsWith("/meta")) data = { connected_sources: [], lanes: [], writable_calendars: [], default_timezone: "UTC", sources_available: true };
    else if (url.pathname.endsWith("/accounts")) data = { accounts: [], health_available: true };
    else if (url.pathname.endsWith("/day-briefing")) data = { has_entries: false, has_domain_context: false, entries: [], groups: [] };
    else if (url.pathname.endsWith("/duplicates")) data = { clusters: [], available: true, rules: { match_strategy: "balanced", noisy_threshold: 2 } };
    else data = { entries: [], source_freshness: [], lanes: [], entries_source_available: true, sources_available: true, has_more: false, next_cursor: null, total: 0, offset: 0, limit: 50 };
    return new Response(JSON.stringify({ data }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetch);
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/calendar?view=user&range=week&anchor=2026-07-01&timezone=UTC"]}>
    <CalendarWorkspacePage />
  </MemoryRouter></QueryClientProvider>);
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Provider invitation" });
  expect(screen.getByText("Organizer: Organizer")).toBeTruthy();
  expect(screen.getByText("Conflict availability unknown")).toBeTruthy();
  expect(screen.queryByTestId("calendar-verdict-all-clear")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Show overlap conflict for Provider invitation" }));
  expect(screen.getByRole("region", { name: "Invitation conflict evidence" }).textContent).toContain("Matched radar overlap");
  await user.click(screen.getByRole("button", { name: "Close conflict evidence" }));
  await user.click(screen.getByRole("button", { name: "More invitations" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "More invitations" })).toBeNull());
  await user.click(screen.getByRole("button", { name: "Provider invitation" }));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("/entries/invitation-1"))).toBe(true));
  state = "failed";
  await act(async () => { await qc.invalidateQueries({ queryKey: ["calendar-invitations"] }); });
  await waitFor(() => expect(screen.getByText("calendar invitations unavailable")).toBeTruthy());
  expect(screen.getByText("Organizer: Organizer")).toBeTruthy();
  expect(screen.queryByText("No unanswered invitations.")).toBeNull();
  state = "pending";
  let refresh: Promise<void>;
  await act(async () => { refresh = qc.invalidateQueries({ queryKey: ["calendar-invitations"] }); });
  await screen.findByText("Refreshing invitations…");
  expect(screen.queryByText("No unanswered invitations.")).toBeNull();
  await waitFor(() => expect(release).toBeDefined());
  state = "empty";
  await act(async () => { release?.(); await refresh; });
  await screen.findByText("No unanswered invitations.");
  qc.clear();
});

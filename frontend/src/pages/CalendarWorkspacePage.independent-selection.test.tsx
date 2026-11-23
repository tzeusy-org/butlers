// Spec: REQ-module-calendar-036
// @vitest-environment jsdom

import { useEffect } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router";
import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";

// Real page, hooks and apiFetch; only the SSE transport is dormant.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({
    health: "healthy",
    status: "open",
    subscribe: () => () => {},
  }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Probe() {
  const location = useLocation();
  return (
    <>
      <div data-testid="location">{location.search}</div>
      <div data-testid="pathname">{location.pathname}</div>
    </>
  );
}

let navigateRef: ReturnType<typeof useNavigate> | null = null;
function NavCapture() {
  const navigate = useNavigate();
  useEffect(() => {
    navigateRef = navigate;
  }, [navigate]);
  return null;
}

const entry = {
  entry_id: "entry-1",
  title: "Deep linked meeting",
  start_at: "2026-07-03T09:00:00Z",
  end_at: "2026-07-03T10:00:00Z",
  timezone: "UTC",
  view: "user",
  source_type: "provider_event",
  source_key: "google:primary",
  event_id: "event-1",
  all_day: false,
  calendar_id: "primary",
  provider_event_id: "provider-1",
  butler_name: "general",
  schedule_id: null,
  reminder_id: null,
  rrule: null,
  cron: null,
  until_at: null,
  status: "active",
  sync_state: "fresh",
  editable: false,
  metadata: {},
  linked_people: [],
};

function stubFetch(
  known: boolean,
  gate?: Promise<void>,
  lookup?: (id: string) => Promise<typeof entry>,
  gridEntries: Array<typeof entry> = [],
) {
  const fetch = vi.fn(async (input: string) => {
    const url = new URL(input, "http://localhost");
    let data: unknown;
    if (url.pathname.includes("/entries/")) {
      if (gate) await gate;
      if (!known) {
        return new Response(
          JSON.stringify({ error: { code: "not_found", message: "No" } }),
          {
            status: 404,
          },
        );
      }
      data = lookup ? await lookup(url.pathname.split("/").at(-1) ?? "") : entry;
    } else if (url.pathname.endsWith("/invitations")) {
      data = {
        entries: [],
        issues_available: true,
        conflicts_available: true,
        sources_degraded: [],
        has_more: false,
        next_cursor: null,
      };
    } else if (url.pathname.endsWith("/conflicts")) {
      data = {
        issues: [],
        issues_available: true,
        scan_window: { start: "2026-07-01", end: "2026-07-02" },
      };
    } else if (url.pathname.endsWith("/meta")) {
      data = {
        connected_sources: [],
        lanes: [],
        writable_calendars: [],
        default_timezone: "UTC",
        sources_available: true,
      };
    } else if (url.pathname.endsWith("/accounts")) {
      data = { accounts: [], health_available: true };
    } else if (url.pathname.endsWith("/day-briefing")) {
      data = {
        has_entries: false,
        has_domain_context: false,
        entries: [],
        groups: [],
      };
    } else if (url.pathname.endsWith("/duplicates")) {
      data = {
        clusters: [],
        available: true,
        rules: { match_strategy: "balanced", noisy_threshold: 2 },
      };
    } else {
      data = {
        entries: gridEntries,
        source_freshness: [],
        lanes: [],
        entries_source_available: true,
        sources_available: true,
        has_more: false,
        next_cursor: null,
        total: 0,
        offset: 0,
        limit: 50,
      };
    }
    return new Response(JSON.stringify({ data }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function mount(search: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/calendar?${search}`]}>
        <CalendarWorkspacePage />
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("opens the entry named by ?entry=, moves to its day, and drops the param", async () => {
  const fetch = stubFetch(true);
  const { container } = mount(
    "view=user&range=week&anchor=2026-07-01&entry=entry-1",
  );

  await waitFor(() =>
    expect(
      container.querySelector('[data-testid="entry-detail-panel"]'),
    ).not.toBeNull(),
  );
  expect(
    fetch.mock.calls.some(([url]) => String(url).includes("/entries/entry-1")),
  ).toBe(true);
  await waitFor(() => {
    const search = new URLSearchParams(
      screen.getByTestId("location").textContent ?? "",
    );
    expect(search.get("entry")).toBeNull();
    expect(search.get("range")).toBe("day");
    expect(search.get("anchor")).toBe("2026-07-03");
  });
});

it("an unknown ?entry= opens nothing and is dropped, never looping", async () => {
  const fetch = stubFetch(false);
  const { container } = mount(
    "view=user&range=week&anchor=2026-07-01&entry=gone",
  );

  await waitFor(() => {
    const search = new URLSearchParams(
      screen.getByTestId("location").textContent ?? "",
    );
    expect(search.get("entry")).toBeNull();
  });
  expect(
    container.querySelector('[data-testid="entry-detail-panel"]'),
  ).toBeNull();
  const lookups = fetch.mock.calls.filter(([url]) =>
    String(url).includes("/entries/gone"),
  );
  expect(lookups.length).toBeLessThanOrEqual(2);
});

it("leaving /calendar before the entry fetch resolves does not navigate back", async () => {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const fetch = stubFetch(true, gate);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={[
          "/calendar?view=user&range=week&anchor=2026-07-01&entry=entry-1",
        ]}
      >
        <NavCapture />
        <Routes>
          <Route path="/calendar" element={<CalendarWorkspacePage />} />
          <Route path="/elsewhere" element={<div>elsewhere</div>} />
        </Routes>
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([url]) =>
        String(url).includes("/entries/entry-1"),
      ),
    ).toBe(true),
  );
  act(() => navigateRef?.("/elsewhere"));
  await waitFor(() =>
    expect(screen.getByTestId("pathname").textContent).toBe("/elsewhere"),
  );

  release();
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
  expect(screen.getByTestId("pathname").textContent).toBe("/elsewhere");
  expect(screen.getByTestId("location").textContent).toBe("");
});

it("keeps the newer entry when an earlier calendar door resolves last", async () => {
  let release: () => void = () => {};
  const firstGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const newer = {
    ...entry,
    entry_id: "entry-2",
    title: "Second door meeting",
    start_at: "2026-07-06T09:00:00Z",
    end_at: "2026-07-06T10:00:00Z",
  };
  stubFetch(true, undefined, async (id) => {
    if (id === entry.entry_id) {
      await firstGate;
      return entry;
    }
    expect(id).toBe(newer.entry_id);
    return newer;
  });
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const { container } = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={["/calendar?view=user&range=week&anchor=2026-07-01&entry=entry-1"]}
      >
        <NavCapture />
        <Routes>
          <Route path="/calendar" element={<CalendarWorkspacePage />} />
        </Routes>
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const olderKey = ["calendar-workspace-entry", entry.entry_id];
  const panel = () => container.querySelector('[data-testid="entry-detail-panel"]');
  await waitFor(() => expect(qc.getQueryState(olderKey)?.fetchStatus).toBe("fetching"));
  act(() => navigateRef?.("/calendar?view=user&range=week&anchor=2026-07-01&entry=entry-2"));
  await waitFor(() => {
    expect(panel()?.textContent).toContain(newer.title);
    expect(screen.getByTestId("location").textContent).toContain("anchor=2026-07-06");
  });

  await act(async () => {
    const pending = qc.fetchQuery({
      queryKey: olderKey,
      queryFn: async () => { throw new Error("Expected to join the original request"); },
    });
    release();
    expect(await pending).toMatchObject({ data: entry });
  });
  expect(panel()?.textContent).toContain(newer.title);
  expect(screen.getByTestId("location").textContent).toContain("anchor=2026-07-06");
});

it("keeps a grid selection when an earlier day sheet lookup finishes", async () => {
  let release = () => {};
  const olderGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const clicked = {
    ...entry,
    entry_id: "clicked-meeting",
    title: "Selected calendar block",
    start_at: "2026-07-01T09:00:00Z",
    end_at: "2026-07-01T10:00:00Z",
  };
  stubFetch(true, undefined, async (id) => {
    if (id === entry.entry_id) {
      await olderGate;
      return entry;
    }
    expect(id).toBe(clicked.entry_id);
    return clicked;
  }, [clicked]);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={["/calendar?view=user&range=week&anchor=2026-07-01&entry=entry-1"]}
      >
        <CalendarWorkspacePage />
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const earlierKey = ["calendar-workspace-entry", entry.entry_id];
  const panel = () => container.querySelector('[data-testid="entry-detail-panel"]');
  await waitFor(() => expect(qc.getQueryState(earlierKey)?.fetchStatus).toBe("fetching"));
  const block = await waitFor(() => {
    const button = container.querySelector('[data-calendar-entry-id="clicked-meeting"]');
    expect(button).not.toBeNull();
    return button!;
  });
  fireEvent.click(block);
  await waitFor(() => expect(panel()?.textContent).toContain(clicked.title));
  await act(async () => {
    const pending = qc.fetchQuery({
      queryKey: earlierKey,
      queryFn: async () => { throw new Error("Expected to join the original request"); },
    });
    release();
    expect(await pending).toMatchObject({ data: entry });
  });
  expect(panel()?.textContent).toContain(clicked.title);
  expect(screen.getByTestId("location").textContent).toContain("anchor=2026-07-01");
});

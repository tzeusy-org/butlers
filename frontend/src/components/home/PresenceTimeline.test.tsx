// @vitest-environment jsdom
/**
 * <PresenceTimeline> through the real hooks and API module: confirmed leave/arrive rows with
 * place labels resolved from the owner's places, undone and late rows kept but muted, an empty
 * week, a quiet "not switched on" state for the 503, and a plain error for any other failure.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PresenceTimeline } from "@/components/home/PresenceTimeline.tsx";

const HOME = "33333333-3333-4333-8333-333333333333";

const server = vi.hoisted(() => ({
  transitions: [] as unknown[],
  nextCursor: null as string | null,
  paths: [] as string[],
  failure: null as null | { code: string; status: number },
}));

vi.mock("@/api/client.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  return {
    ...actual,
    apiFetch: vi.fn(async (path: string) => {
      if (path === "/home/places") {
        return { data: [{ id: "33333333-3333-4333-8333-333333333333", label: "Flat" }] };
      }
      server.paths.push(path);
      if (server.failure) {
        throw new actual.ApiError(server.failure.code, "no", server.failure.status);
      }
      if (path.includes("cursor=")) {
        return { items: [edge(40, { direction: "enter" })], next_cursor: null, limit: 20 };
      }
      return { items: server.transitions, next_cursor: server.nextCursor, limit: 20 };
    }),
  };
});

function renderTimeline() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PresenceTimeline />
    </QueryClientProvider>,
  );
}

const edge = (n: number, over: Record<string, unknown>) => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
  direction: "leave",
  place_ref: HOME,
  at: `2026-11-13T09:${String(n).padStart(2, "0")}:00Z`,
  confirmed_at: `2026-11-13T09:${String(n).padStart(2, "0")}:30Z`,
  late: false,
  retracted: false,
  retraction_event_id: null,
  ...over,
});

describe("PresenceTimeline", () => {
  beforeEach(() => {
    server.transitions = [];
    server.nextCursor = null;
    server.paths = [];
    server.failure = null;
  });
  afterEach(cleanup);

  it("lists confirmed edges, keeping undone and late ones muted rather than hidden", async () => {
    server.transitions = [
      edge(30, { direction: "enter" }),
      edge(20, { retracted: true }),
      edge(10, { late: true }),
      edge(5, { place_ref: null }),
    ];
    renderTimeline();
    const list = await screen.findByTestId("presence-timeline-list");
    const rows = within(list).getAllByTestId("presence-transition");
    expect(rows.map((row) => row.getAttribute("data-direction"))).toEqual([
      "enter",
      "leave",
      "leave",
      "leave",
    ]);
    expect(rows[0].textContent).toContain("Arrived at Flat");
    expect(rows[1].textContent).toContain("Left Flat");
    expect(rows[1].textContent).toContain("undone: you came straight back");
    expect(rows[2].textContent).toContain("confirmed late, so not acted on");
    expect(rows[3].textContent).toContain("Left a saved place");
    expect(rows[0].textContent).not.toContain("undone");
    // Only the undone row is struck through; the late one is muted, a live edge is neither.
    expect(within(rows[1]).getByText("Left Flat").className).toContain("line-through");
    const lateText = within(rows[2]).getByText("Left Flat");
    expect(lateText.className).toContain("text-muted-foreground");
    expect(lateText.className).not.toContain("line-through");
    expect(within(rows[0]).getByText("Arrived at Flat").className).not.toContain("line-through");
  });

  it("says so quietly when there is nothing, and when the reader is not switched on", async () => {
    renderTimeline();
    expect(await screen.findByTestId("presence-timeline-empty")).toBeTruthy();
    cleanup();
    server.failure = { code: "PRESENCE_TIMELINE_UNAVAILABLE", status: 503 };
    renderTimeline();
    expect((await screen.findByTestId("presence-timeline-inactive")).textContent).toMatch(
      /isn't switched on yet/,
    );
    expect(screen.queryByTestId("presence-timeline-error")).toBeNull();
  });

  it("does not claim an empty week when the scan cap hit; it offers to look further back", async () => {
    server.nextCursor = "opaque-cursor";
    renderTimeline();
    const more = await screen.findByTestId("presence-timeline-more");
    expect(more.textContent).toContain("No arrivals or departures found in recent history");
    expect(screen.queryByTestId("presence-timeline-empty")).toBeNull();
    fireEvent.click(within(more).getByText("Look further back"));
    const list = await screen.findByTestId("presence-timeline-list");
    expect(within(list).getAllByTestId("presence-transition")).toHaveLength(1);
    expect(server.paths.some((path) => path.includes("cursor=opaque-cursor"))).toBe(true);
  });

  it("shows a plain error for any other failure", async () => {
    server.failure = { code: "INTERNAL", status: 500 };
    renderTimeline();
    expect(await screen.findByTestId("presence-timeline-error")).toBeTruthy();
    expect(screen.queryByTestId("presence-timeline-inactive")).toBeNull();
  });
});

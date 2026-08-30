// @vitest-environment jsdom
/**
 * Tests for TimelineTab shared-link filter state (bu-vgoh3).
 *
 * The address bar is this tab's only share affordance, so a link has to
 * reproduce the sender's whole VISIBLE filter state. Two pieces used not to
 * travel: the status-chip selection (component state) and the active saved
 * view (localStorage). These tests pin both directions plus the edge that
 * makes a round-trip test worth writing: a link naming a saved view the
 * receiver does not have.
 *
 * Deliberately NOT written as `deserialize(serialize(x)) === x` — that closes
 * happily over a pair of inverse bugs. Every serialization assertion names the
 * exact expected param value; every deserialization assertion reads rendered
 * DOM (aria-pressed on the real chips/pills), not internal state.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { IngestionEventSummary } from "@/api/index.ts";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    replayIngestionEvent: vi.fn(),
    bulkRetryEvents: vi.fn(),
  };
});

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/hooks/use-ingestion-events", () => ({
  useIngestionEvents: vi.fn(),
  useIngestionEventLineage: vi.fn(),
  useIngestionEventRollup: vi.fn(),
  useIngestionEventSenderContact: vi.fn(),
  useIngestionEventReplays: vi.fn(),
  useIngestionEventPayload: vi.fn(),
  useIngestionWindowRollup: vi.fn(),
  useIngestionEventsHistogram: vi.fn(),
}));

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(),
}));

vi.mock("@/hooks/use-timeline-saved-views", () => ({
  useTimelineSavedViews: vi.fn(),
  useCreateTimelineSavedView: vi.fn(),
  useUpdateTimelineSavedView: vi.fn(),
  useDeleteTimelineSavedView: vi.fn(),
}));

import {
  useIngestionEvents,
  useIngestionEventLineage,
  useIngestionEventRollup,
  useIngestionEventSenderContact,
  useIngestionEventReplays,
  useIngestionEventPayload,
  useIngestionWindowRollup,
  useIngestionEventsHistogram,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useUpdateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";
import type { TimelineSavedViewEntry } from "@/api/index.ts";

import { TimelineTab } from "./TimelineTab";

// ---------------------------------------------------------------------------
// Fixtures / helpers
// ---------------------------------------------------------------------------

const STORAGE_KEY = "ingestion-saved-views";

/** Synthetic, generated here; never a real identifier. */
const CUSTOM_VIEW_ID = "11111111-2222-4333-8444-555555555555";
const ABSENT_VIEW_ID = "99999999-8888-4777-8666-555555555555";

/** The exact `statuses` value the "errors" built-in must serialize to. */
const ERRORS_STATUSES_PARAM = "error,failed,replay_pending,replay_failed";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function makeEvent(overrides: Partial<IngestionEventSummary> = {}): IngestionEventSummary {
  return {
    id: "aabbccdd-0000-0000-0000-000000000001",
    received_at: "2026-01-01T10:00:00Z",
    source_channel: "gmail",
    source_provider: null,
    source_endpoint_identity: null,
    source_sender_identity: "alice@example.com",
    source_thread_identity: null,
    external_event_id: null,
    dedupe_key: null,
    dedupe_strategy: null,
    ingestion_tier: null,
    policy_tier: "standard",
    triage_decision: null,
    triage_target: null,
    status: "ingested",
    filter_reason: null,
    error_detail: null,
    cost_usd: null,
    tokens_in: null,
    tokens_out: null,
    session_count: 0,
    sessions: [],
    sender_display: null,
    ...overrides,
  };
}

function makeInfiniteEventsResult(events: IngestionEventSummary[]) {
  return {
    data: {
      pages: [{ data: events, meta: { next_cursor: null, has_more: false } }],
      pageParams: [null],
    },
    isLoading: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  };
}

function makeSavedViewEntry(overrides: Partial<TimelineSavedViewEntry> = {}): TimelineSavedViewEntry {
  return {
    id: CUSTOM_VIEW_ID,
    name: "Alice errors",
    filter_spec: { statuses: ["error", "replay_failed"], range: "1h" },
    created_at: "2026-06-01T10:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
    ...overrides,
  };
}

function setupDefaultMocks() {
  vi.mocked(useIngestionEvents).mockReturnValue(
    makeInfiniteEventsResult([makeEvent()]) as unknown as ReturnType<typeof useIngestionEvents>,
  );
  vi.mocked(useIngestionEventRollup).mockReturnValue({
    data: undefined, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventRollup>);
  vi.mocked(useIngestionEventSenderContact).mockReturnValue({
    data: undefined, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventSenderContact>);
  vi.mocked(useIngestionEventReplays).mockReturnValue({
    data: { data: [] }, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventReplays>);
  vi.mocked(useIngestionEventPayload).mockReturnValue({
    data: undefined, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventPayload>);
  vi.mocked(useIngestionEventLineage).mockReturnValue({
    sessions: { data: { data: [] }, isLoading: false, isError: false } as never,
    rollup: { data: undefined, isLoading: false, isError: false } as never,
  });
  vi.mocked(useConnectorSummaries).mockReturnValue({
    data: { data: [] }, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useConnectorSummaries>);
  vi.mocked(useIngestionWindowRollup).mockReturnValue({
    data: { events: 0, sessions: 0, cost: null, window: { from: null, to: null } },
    isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionWindowRollup>);
  vi.mocked(useIngestionEventsHistogram).mockReturnValue({
    data: { buckets: [], bucket: "1m" },
    isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventsHistogram>);
  vi.mocked(useTimelineSavedViews).mockReturnValue({
    data: { data: [], meta: {} }, isPending: false, isError: false,
  } as unknown as ReturnType<typeof useTimelineSavedViews>);
  vi.mocked(useCreateTimelineSavedView).mockReturnValue({
    mutate: vi.fn(), isPending: false,
  } as unknown as ReturnType<typeof useCreateTimelineSavedView>);
  vi.mocked(useUpdateTimelineSavedView).mockReturnValue({
    mutate: vi.fn(), isPending: false,
  } as unknown as ReturnType<typeof useUpdateTimelineSavedView>);
  vi.mocked(useDeleteTimelineSavedView).mockReturnValue({
    mutate: vi.fn(), isPending: false,
  } as unknown as ReturnType<typeof useDeleteTimelineSavedView>);
}

function mockSavedViews(views: TimelineSavedViewEntry[], opts: { isError?: boolean } = {}) {
  vi.mocked(useTimelineSavedViews).mockReturnValue({
    data: opts.isError ? undefined : { data: views, meta: {} },
    isPending: false,
    isError: opts.isError ?? false,
  } as unknown as ReturnType<typeof useTimelineSavedViews>);
}

/**
 * Renders the live location's query string into the DOM, so URL assertions
 * read what a user would copy out of the address bar rather than poking at
 * router internals. Pure render, no effect.
 */
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-search">{location.search}</span>;
}

// ---------------------------------------------------------------------------
// Shared suite scaffolding
// ---------------------------------------------------------------------------

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

function renderAt(initialUrl: string) {
  act(() => {
    root = createRoot(container);
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialUrl]}>
          <TimelineTab isActive={true} />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

function currentParams(): URLSearchParams {
  const probe = container.querySelector("[data-testid='location-search']");
  if (probe === null) throw new Error("LocationProbe did not render");
  return new URLSearchParams(probe.textContent ?? "");
}

function chipPressed(status: string): string | null {
  const chip = container.querySelector(`[data-testid='status-filter-${status}']`);
  if (chip === null) throw new Error(`status chip missing: ${status}`);
  return chip.getAttribute("aria-pressed");
}

function viewPressed(viewId: string): string | null {
  const pill = container.querySelector(`[data-view='${viewId}']`);
  if (pill === null) throw new Error(`view pill missing: ${viewId}`);
  return pill.getAttribute("aria-pressed");
}

function clickTestId(testId: string) {
  const el = container.querySelector(`[data-testid='${testId}']`) as HTMLElement | null;
  if (el === null) throw new Error(`element missing: ${testId}`);
  act(() => { el.click(); });
}

beforeEach(() => {
  queryClient = makeQueryClient();
  container = document.createElement("div");
  document.body.appendChild(container);
  localStorage.clear();
  vi.resetAllMocks();
  setupDefaultMocks();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  queryClient.clear();
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// Serialization: what the sender's address bar ends up containing
// ---------------------------------------------------------------------------

describe("TimelineTab link state — serialization into the URL", () => {
  it("writes view and statuses with their exact expected values when a non-default built-in view is selected", () => {
    renderAt("/");

    // Positive control: a default toolbar leaves BOTH keys off the URL, so
    // the assertions below cannot pass merely because the URL is untouched.
    expect(currentParams().get("view")).toBeNull();
    expect(currentParams().get("statuses")).toBeNull();

    const errorsPill = container.querySelector("[data-view='errors']") as HTMLElement;
    act(() => { errorsPill.click(); });

    const params = currentParams();
    expect(params.get("view")).toBe("errors");
    expect(params.get("statuses")).toBe(ERRORS_STATUSES_PARAM);
  });

  it("writes the exact status CSV, in ALL_STATUSES order, when a chip is toggled on", () => {
    renderAt("/");

    clickTestId("status-filter-skipped");

    const params = currentParams();
    expect(params.get("statuses")).toBe(
      "ingested,skipped,error,failed,replay_pending,replay_complete,replay_failed",
    );
    // Positive control for the absence assertion: `statuses` really is
    // present in this same URL, so "view is absent" is a fact about `view`,
    // not about an empty or crashed page.
    expect(params.get("view")).toBeNull();
  });

  it("keeps both keys out of the URL at their defaults, and passes the same statuses to the events query", () => {
    renderAt("/?q=hello");

    const params = currentParams();
    // Positive control: an unrelated param that WAS in the link survives.
    expect(params.get("q")).toBe("hello");
    expect(params.get("statuses")).toBeNull();
    expect(params.get("view")).toBeNull();

    const calls = vi.mocked(useIngestionEvents).mock.calls;
    expect(calls[calls.length - 1][0]).toMatchObject({
      statuses: "ingested,error,failed,replay_pending,replay_complete,replay_failed",
    });
    expect(container.querySelector("[data-testid='link-statuses-unrecognized-banner']")).toBeNull();
  });

  it("serializes an all-chips-off selection as an explicit empty value, not an absent key", () => {
    renderAt("/?statuses=error");

    expect(chipPressed("error")).toBe("true");
    clickTestId("status-filter-error");

    const probe = container.querySelector("[data-testid='location-search']");
    expect(probe!.textContent).toContain("statuses=");
    expect(currentParams().get("statuses")).toBe("");
    // Positive control: the page is still alive and rendering chips.
    expect(chipPressed("error")).toBe("false");
  });
});

// ---------------------------------------------------------------------------
// Deserialization: what the receiver's screen ends up showing
// ---------------------------------------------------------------------------

describe("TimelineTab link state — read on mount into rendered state", () => {
  it("lights the status chips named by ?statuses= and leaves the others unlit", () => {
    renderAt(`/?statuses=${ERRORS_STATUSES_PARAM}`);

    expect(chipPressed("error")).toBe("true");
    expect(chipPressed("failed")).toBe("true");
    expect(chipPressed("replay_pending")).toBe("true");
    expect(chipPressed("replay_failed")).toBe("true");
    // Paired negative: statuses NOT in the param must be off, otherwise the
    // test would pass against a component that lights every chip.
    expect(chipPressed("ingested")).toBe("false");
    expect(chipPressed("skipped")).toBe("false");
  });

  it("lights the saved-view pill named by ?view= and leaves the others unlit", () => {
    renderAt("/?view=errors");

    expect(viewPressed("errors")).toBe("true");
    expect(viewPressed("all")).toBe("false");
    expect(viewPressed("spend")).toBe("false");
  });

  it("round-trips the full visible filter state: same chips, same pill, same URL", () => {
    const link = `/?range=7d&q=payment&channels=email,telegram&view=errors&statuses=${ERRORS_STATUSES_PARAM}`;
    renderAt(link);

    // Rendered state matches what the sender was looking at.
    expect(viewPressed("errors")).toBe("true");
    expect(viewPressed("all")).toBe("false");
    expect(chipPressed("error")).toBe("true");
    expect(chipPressed("ingested")).toBe("false");

    // And the link itself is a fixed point: nothing was rewritten or dropped
    // on arrival.
    const params = currentParams();
    expect(params.get("range")).toBe("7d");
    expect(params.get("q")).toBe("payment");
    expect(params.get("channels")).toBe("email,telegram");
    expect(params.get("view")).toBe("errors");
    expect(params.get("statuses")).toBe(ERRORS_STATUSES_PARAM);

    // The status selection reached the query layer too, not just the chips.
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    expect(calls[calls.length - 1][0]).toMatchObject({ statuses: ERRORS_STATUSES_PARAM });
  });

  it("treats an explicit empty ?statuses= as 'nothing enabled', not as absent", () => {
    renderAt("/?statuses=");

    expect(chipPressed("ingested")).toBe("false");
    expect(chipPressed("error")).toBe("false");
    // Positive control: the toolbar rendered, so the falses above are real
    // chips rather than a blank page.
    expect(viewPressed("all")).toBe("true");
    expect(container.querySelector("[data-testid='link-statuses-unrecognized-banner']")).toBeNull();
  });

  it("lets a link's ?view= win over this browser's remembered view, without overwriting it", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ activeView: "spend" }));

    renderAt("/?view=errors");

    expect(viewPressed("errors")).toBe("true");
    expect(viewPressed("spend")).toBe("false");
    // The receiver's own persisted preference is untouched: following a link
    // is not a decision about what their next unlinked visit opens.
    expect(localStorage.getItem(STORAGE_KEY)).toBe(JSON.stringify({ activeView: "spend" }));
  });

  it("falls back to the remembered view only when the link names none", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ activeView: "spend" }));

    renderAt("/");

    expect(viewPressed("spend")).toBe("true");
    expect(viewPressed("all")).toBe("false");
  });

  it("does not let a resolvable custom view's stored filter_spec stomp the link's own filters", () => {
    mockSavedViews([makeSavedViewEntry()]);

    // The sender had diverged from "Alice errors" (spec says 1h/error+replay_failed)
    // before copying the link. That divergence is the thing being shared.
    renderAt(`/?view=${CUSTOM_VIEW_ID}&range=7d&statuses=ingested`);

    expect(chipPressed("ingested")).toBe("true");
    expect(chipPressed("error")).toBe("false");
    const params = currentParams();
    expect(params.get("range")).toBe("7d");
    expect(params.get("statuses")).toBe("ingested");
    // Positive control: the view really did resolve, so this is a statement
    // about precedence and not about a missing view.
    expect(container.querySelector(`[data-testid='custom-view-${CUSTOM_VIEW_ID}']`)).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Unknown URL statuses are dropped strictly, then explained
// ---------------------------------------------------------------------------

describe("TimelineTab link state — unrecognized statuses", () => {
  it("keeps an unknown-only selection empty and names every dropped value", () => {
    renderAt("/?q=receipt&statuses=retired,removed");

    const note = container.querySelector("[data-testid='link-statuses-unrecognized-banner']");
    expect(note).not.toBeNull();
    expect(note!.getAttribute("role")).toBe("status");
    expect(note!.textContent).toContain("retired");
    expect(note!.textContent).toContain("removed");
    expect(note!.textContent).toContain("Remaining link filters apply.");
    expect(note!.textContent).not.toContain("Saved views");

    expect(chipPressed("ingested")).toBe("false");
    expect(chipPressed("error")).toBe("false");
    expect(currentParams().get("q")).toBe("receipt");
    expect(currentParams().get("statuses")).toBe("");
  });

  it("keeps known statuses from a mixed selection while naming the unknown value", () => {
    renderAt("/?channels=email&statuses=error,retired");

    const note = container.querySelector("[data-testid='link-statuses-unrecognized-banner']");
    expect(note).not.toBeNull();
    expect(note!.textContent).toContain("retired");
    expect(note!.textContent).toContain("Remaining link filters apply.");

    expect(chipPressed("error")).toBe("true");
    expect(chipPressed("ingested")).toBe("false");
    expect(currentParams().get("channels")).toBe("email");
    expect(currentParams().get("statuses")).toBe("error");
  });
});

// ---------------------------------------------------------------------------
// The load-bearing edge: a link naming a view the receiver does not have
// ---------------------------------------------------------------------------

describe("TimelineTab link state — unresolvable saved view", () => {
  it("says so instead of silently rendering a clean default", () => {
    mockSavedViews([makeSavedViewEntry()]); // a DIFFERENT view exists

    renderAt(`/?view=${ABSENT_VIEW_ID}&statuses=${ERRORS_STATUSES_PARAM}`);

    const banner = container.querySelector("[data-testid='link-view-unresolved-banner']");
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain(ABSENT_VIEW_ID);

    // No view is fabricated to stand in for the missing one.
    expect(container.querySelector(`[data-testid='custom-view-${ABSENT_VIEW_ID}']`)).toBeNull();
    expect(viewPressed("all")).toBe("false");

    // And the rest of the link is still honoured, which is exactly why the
    // banner says the filters applied but the name did not.
    expect(chipPressed("error")).toBe("true");
    expect(chipPressed("ingested")).toBe("false");
    expect(currentParams().get("view")).toBe(ABSENT_VIEW_ID);
  });

  it("distinguishes a failed saved-views fetch from a genuinely absent view", () => {
    mockSavedViews([], { isError: true });

    renderAt(`/?view=${ABSENT_VIEW_ID}`);

    const banner = container.querySelector("[data-testid='link-view-unresolved-banner']");
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain("could not be loaded");
    expect(banner!.textContent).not.toContain("not saved in this browser");
  });

  it("shows no banner while the saved-views list is still loading", () => {
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: undefined, isPending: true, isError: false,
    } as unknown as ReturnType<typeof useTimelineSavedViews>);

    renderAt(`/?view=${ABSENT_VIEW_ID}`);

    expect(container.querySelector("[data-testid='link-view-unresolved-banner']")).toBeNull();
    // Positive control: the toolbar rendered, so the absence above is a
    // decision and not a crashed render.
    expect(container.querySelector("[data-testid='custom-views-loading']")).not.toBeNull();
  });

  it("shows no banner for a link whose view resolves", () => {
    mockSavedViews([makeSavedViewEntry()]);

    renderAt(`/?view=${CUSTOM_VIEW_ID}`);

    expect(container.querySelector("[data-testid='link-view-unresolved-banner']")).toBeNull();
    expect(container.querySelector(`[data-testid='custom-view-${CUSTOM_VIEW_ID}']`)).not.toBeNull();
  });

  it("dismissing drops only the unresolved attribution, keeping the link's filters", () => {
    mockSavedViews([]);

    renderAt(`/?view=${ABSENT_VIEW_ID}&statuses=${ERRORS_STATUSES_PARAM}`);
    expect(container.querySelector("[data-testid='link-view-unresolved-banner']")).not.toBeNull();

    clickTestId("link-view-unresolved-dismiss");

    expect(container.querySelector("[data-testid='link-view-unresolved-banner']")).toBeNull();
    const params = currentParams();
    expect(params.get("view")).toBeNull();
    // Positive control paired with that absence: the status filter survived
    // the dismissal untouched.
    expect(params.get("statuses")).toBe(ERRORS_STATUSES_PARAM);
    expect(chipPressed("error")).toBe("true");
    expect(chipPressed("ingested")).toBe("false");
  });
});

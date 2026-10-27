// @vitest-environment jsdom
/**
 * ButlerRelationshipContactsTab — RTL tests for the redesigned 6-panel layout.
 *
 * Tests:
 *  - All six panels render (KPI strip, tier distribution, overdue, watchlist, thread, facts)
 *  - Loading states show loading placeholders, not empty-state text
 *  - Empty states are shown when data is absent
 *  - KPI strip renders computed values (tracked count, T1 warmth avg, cadence ok, overdue count)
 *  - Tier distribution renders rows grouped by tier with warmth bars
 *  - Overdue panel ranks contacts by owed_days desc
 *  - Watchlist renders T1+T2 contacts sorted by warmth desc
 *  - Thread panel shows interaction direction (in / out / draft)
 *  - Known facts panel shows entity channels and roles for the selection
 *  - Selecting a watchlist row switches thread and facts panels
 *
 * The per-person reads (tracked count, interaction thread, entity detail) run
 * through the real hooks and client against a stubbed fetch, so the request
 * URLs themselves are asserted: every read is entity-keyed (bu-lzrpwd).
 *
 * bead: bu-iuol4.21
 */

import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerRelationshipContactsTab from "./ButlerRelationshipContactsTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-contacts", () => ({
  useOverdueContacts: vi.fn(),
}));

vi.mock("@/hooks/use-memory", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/use-memory")>()),
  useDunbarRanking: vi.fn(),
}));

import { useOverdueContacts } from "@/hooks/use-contacts";
import { useDunbarRanking } from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Fixed clock — prevents date-formatting flakes
// ---------------------------------------------------------------------------

const FIXED_NOW_ISO = "2026-05-10T08:00:00.000Z";

beforeAll(() => {
  // Only the clock is faked: the entity reads resolve through real promises and timers.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(FIXED_NOW_ISO));
});

afterAll(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const TRACKED_COUNT_URL = "/api/relationship/entities?has=contact&limit=1";
const ALICE_INTERACTIONS_URL = "/api/relationship/entities/e-1/interactions?limit=4";
const ALICE_DETAIL_URL = "/api/memory/entities/e-1";

const TRACKED_COUNT = { items: [], total: 42, limit: 1, offset: 0 };

const ALICE_DETAIL = {
  data: {
    id: "e-1",
    canonical_name: "Alice Smith",
    entity_type: "person",
    aliases: [],
    roles: ["friend", "neighbour"],
    fact_count: 3,
    unidentified: false,
    source_butler: null,
    source_scope: null,
    archived: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-05-10T00:00:00Z",
    dunbar_tier: 5,
    dunbar_score: 95,
    metadata: {},
    recent_facts: [],
    recent_facts_total: 0,
    recent_facts_offset: 0,
    recent_facts_limit: 20,
    recent_facts_has_more: false,
    entity_info: [
      { id: "i-1", type: "email", value: "alice@example.com", label: null, is_primary: true, secured: false },
      { id: "i-2", type: "phone", value: null, label: null, is_primary: false, secured: true },
    ],
  },
  meta: {},
};

const DUNBAR_DATA = {
  entries: [
    {
      contact_id: "c-1",
      entity_id: "e-1",
      canonical_name: "Alice Smith",
      dunbar_tier: 5,
      dunbar_score: 95.0,
      dunbar_tier_override: false,
      warmth: 0.82,
      avatar_url: null,
      aliases: [],
      last_interaction_at: "2026-05-01T10:00:00Z",
    },
    {
      contact_id: "c-2",
      entity_id: "e-2",
      canonical_name: "Bob Jones",
      dunbar_tier: 15,
      dunbar_score: 70.0,
      dunbar_tier_override: true,
      warmth: 0.35,
      avatar_url: null,
      aliases: [],
      last_interaction_at: null,
    },
    {
      contact_id: "c-3",
      entity_id: "e-3",
      canonical_name: "Carol White",
      dunbar_tier: 5,
      dunbar_score: 60.0,
      dunbar_tier_override: false,
      warmth: 0.55,
      avatar_url: null,
      aliases: [],
      last_interaction_at: "2026-04-20T08:00:00Z",
    },
  ],
  owner_entity_id: null,
  cadence_available: true,
  unmeasurable_count: 0,
};

const OVERDUE_DATA = {
  contacts: [
    {
      contact_id: "c-2",
      name: "Bob Jones",
      tier: 15,
      owed_days: 20,
      last_contact_date: "2026-04-20",
      target_cadence_days: 14,
    },
    {
      contact_id: "c-4",
      name: "Dan Brown",
      tier: 50,
      owed_days: 45,
      last_contact_date: "2026-03-25",
      target_cadence_days: 30,
    },
  ],
  cadence_available: true,
  unmeasurable_count: 0,
};

const ALICE_INTERACTIONS = [
  {
    id: "ix-1",
    type: "message",
    summary: "Hey, how are you doing?",
    occurred_at: "2026-05-01T09:00:00Z",
    direction: "in",
  },
  {
    id: "ix-2",
    type: "message",
    summary: "Doing great, thanks for checking in!",
    occurred_at: "2026-05-01T10:00:00Z",
    direction: "out",
  },
  {
    id: "ix-3",
    type: "message",
    summary: "Draft: following up on our conversation.",
    occurred_at: "2026-05-02T08:00:00Z",
    direction: "drafted",
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <ButlerRelationshipContactsTab />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Client-layer fetch stub for the entity-keyed reads
// ---------------------------------------------------------------------------

type Route = { status: number; body: unknown } | "pending";

let routes: Record<string, Route> = {};
let requested: string[] = [];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  requested = [];
  routes = {
    [TRACKED_COUNT_URL]: { status: 200, body: TRACKED_COUNT },
    [ALICE_INTERACTIONS_URL]: { status: 200, body: [] },
    [ALICE_DETAIL_URL]: { status: 200, body: ALICE_DETAIL },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      requested.push(url);
      const route = routes[url];
      if (route === "pending") return new Promise<Response>(() => {});
      if (!route) return jsonResponse(404, { detail: "Not Found" });
      return jsonResponse(route.status, route.body);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function selectAlice() {
  const rows = await screen.findAllByTestId("watchlist-row");
  const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
  expect(aliceRow).toBeDefined();
  fireEvent.click(aliceRow!);
}

// ---------------------------------------------------------------------------
// Default mock setup: all data loaded
// ---------------------------------------------------------------------------

function mockRanking(
  dunbar: Partial<ReturnType<typeof useDunbarRanking>>,
  overdue: Partial<ReturnType<typeof useOverdueContacts>>,
) {
  vi.mocked(useDunbarRanking).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    ...dunbar,
  } as unknown as ReturnType<typeof useDunbarRanking>);
  vi.mocked(useOverdueContacts).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    ...overdue,
  } as unknown as ReturnType<typeof useOverdueContacts>);
}

function setupWithData() {
  mockRanking({ data: DUNBAR_DATA }, { data: OVERDUE_DATA });
}

function setupWithInteractions() {
  setupWithData();
  routes[ALICE_INTERACTIONS_URL] = { status: 200, body: ALICE_INTERACTIONS };
}

function setupEmpty() {
  mockRanking(
    { data: { entries: [], owner_entity_id: null, cadence_available: true, unmeasurable_count: 0 } },
    { data: { contacts: [], cadence_available: true, unmeasurable_count: 0 } },
  );
  routes[TRACKED_COUNT_URL] = { status: 200, body: { ...TRACKED_COUNT, total: 0 } };
}

function setupLoading() {
  mockRanking({ isLoading: true }, { isLoading: true });
  routes[TRACKED_COUNT_URL] = "pending";
}

function setupWithError() {
  mockRanking({ isError: true }, { isError: true });
  routes[TRACKED_COUNT_URL] = { status: 500, body: { detail: "boom" } };
}

// ---------------------------------------------------------------------------
// Tests: All panels present
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — all panels present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the root tab container", () => {
    renderTab();
    expect(screen.getByTestId("relationship-contacts-tab")).toBeDefined();
  });

  it("renders the KPI strip", () => {
    renderTab();
    expect(screen.getByTestId("kpi-strip")).toBeDefined();
  });

  it("renders the tier distribution card", () => {
    renderTab();
    expect(screen.getByTestId("tier-distribution-card")).toBeDefined();
  });

  it("renders the overdue card", () => {
    renderTab();
    expect(screen.getByTestId("overdue-card")).toBeDefined();
  });

  it("renders the watchlist card", () => {
    renderTab();
    expect(screen.getByTestId("watchlist-card")).toBeDefined();
  });

  it("renders the thread card", () => {
    renderTab();
    expect(screen.getByTestId("thread-card")).toBeDefined();
  });

  it("renders the known facts card", () => {
    renderTab();
    expect(screen.getByTestId("known-facts-card")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI strip rendering
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — KPI strip", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders kpi-item elements", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    // KpiCell renders a div with data-testid="kpi-item" for each of the 4 cells
    expect(items.length).toBeGreaterThanOrEqual(4);
  });

  it("renders the tracked count from the entity index total", async () => {
    renderTab();
    expect(await screen.findByText("42")).toBeDefined();
    expect(requested).toContain(TRACKED_COUNT_URL);
  });

  it("renders overdue count when contacts are overdue", () => {
    renderTab();
    // Overdue panel has 2 contacts
    expect(screen.getByText("2")).toBeDefined();
  });

  it("renders T1 warmth average for tier-5 entries", () => {
    renderTab();
    // T1 entries: Alice (0.82) + Carol (0.55), avg = 0.685 → "0.69"
    expect(screen.getByText("0.69")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Tier distribution
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — tier distribution", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders tier distribution list", () => {
    renderTab();
    expect(screen.getByTestId("tier-distribution-list")).toBeDefined();
  });

  it("renders tier rows for T1 and T2 since both have entries", () => {
    renderTab();
    const rows = screen.getAllByTestId("tier-distribution-row");
    expect(rows.length).toBeGreaterThanOrEqual(2);
  });

  it("shows T1 tier label in tier distribution", () => {
    renderTab();
    const matches = screen.getAllByText("T1 · Support 5");
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("shows T2 tier label in tier distribution", () => {
    renderTab();
    const matches = screen.getAllByText("T2 · Sympathy 15");
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Overdue panel ranking by owed_days desc
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — overdue panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the overdue list", () => {
    renderTab();
    expect(screen.getByTestId("overdue-list")).toBeDefined();
  });

  it("renders overdue rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("overdue-row");
    expect(rows.length).toBe(2);
  });

  it("ranks most overdue contact first (Dan Brown: 45d > Bob Jones: 20d)", () => {
    renderTab();
    const rows = screen.getAllByTestId("overdue-row");
    // First row should be Dan Brown (owed_days=45)
    expect(rows[0].textContent).toContain("Dan Brown");
    // Second row should be Bob Jones (owed_days=20)
    expect(rows[1].textContent).toContain("Bob Jones");
  });

  it("shows destructive badge for contacts overdue > 30d", () => {
    renderTab();
    // Dan Brown at 45d overdue should have destructive badge
    expect(screen.getByText("45d overdue")).toBeDefined();
  });

  it("shows overdue days for each row", () => {
    renderTab();
    expect(screen.getByText("20d overdue")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Watchlist T1+T2
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — watchlist", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the watchlist table", () => {
    renderTab();
    expect(screen.getByTestId("watchlist-table")).toBeDefined();
  });

  it("renders watchlist rows for T1 and T2 entries only", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    // Alice (T1), Carol (T1), Bob (T2) = 3 entries
    expect(rows.length).toBe(3);
  });

  it("sorts watchlist by warmth descending (Alice 0.82 first)", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    // Alice has warmth 0.82 — should be first
    expect(rows[0].textContent).toContain("Alice Smith");
  });

  it("shows warmth values in tabular-nums cells", () => {
    renderTab();
    // Alice warmth 0.82 should appear in the table
    expect(screen.getByText("0.82")).toBeDefined();
  });

  it("marks tier-override entries with a star", () => {
    renderTab();
    // Bob Jones has dunbar_tier_override=true
    const rows = screen.getAllByTestId("watchlist-row");
    const bobRow = rows.find((r) => r.textContent?.includes("Bob Jones"));
    expect(bobRow?.textContent).toContain("★");
  });

  it("renders Last contact column header", () => {
    renderTab();
    expect(screen.getByText("Last contact")).toBeDefined();
  });

  it("shows relative date in Last contact cells for contacts with interactions", () => {
    // Fixed clock is 2026-05-10T08:00:00Z, Alice last seen 2026-05-01T10:00:00Z = 8d ago
    renderTab();
    const cells = screen.getAllByTestId("watchlist-last-contact");
    const aliceCell = cells.find((c) => c.textContent?.includes("d ago") || c.textContent === "today");
    expect(aliceCell).toBeDefined();
  });

  it("preserves the year for owner-time fallback dates", () => {
    vi.mocked(useDunbarRanking).mockReturnValue({
      data: {
        ...DUNBAR_DATA,
        entries: DUNBAR_DATA.entries.map((entry) =>
          entry.contact_id === "c-1"
            ? { ...entry, last_interaction_at: "2024-12-31T17:00:00Z" }
            : entry,
        ),
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useDunbarRanking>);

    renderTab();

    const aliceCell = screen
      .getAllByTestId("watchlist-last-contact")
      .find((cell) => cell.closest("tr")?.textContent?.includes("Alice Smith"));
    expect(aliceCell?.textContent).toContain("Jan 1, 2025");
  });

  it("shows dash in Last contact cell for contacts with no interactions (never-contacted)", () => {
    // Bob Jones has last_interaction_at: null → should render "—"
    renderTab();
    const cells = screen.getAllByTestId("watchlist-last-contact");
    const neverCell = cells.find((c) => c.textContent === "—");
    expect(neverCell).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Selected thread interaction direction display
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — selected thread", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithInteractions();
  });
  afterEach(() => cleanup());

  it("shows prompt when no contact is selected", () => {
    renderTab();
    expect(screen.getByTestId("thread-empty-prompt")).toBeDefined();
  });

  it("fetches the thread by the row's entity_id and renders the mapped rows", async () => {
    renderTab();
    await selectAlice();
    const items = await screen.findAllByTestId("thread-item");
    expect(items.length).toBe(3);
    expect(requested).toContain(ALICE_INTERACTIONS_URL);
    expect(requested.filter((url) => url.includes("/relationship/contacts"))).toEqual([]);
    expect(screen.getByText("Hey, how are you doing?")).toBeDefined();
    for (const label of ["In", "Out", "Draft"]) {
      expect(screen.getByText(label)).toBeDefined();
    }
  });

  it("keeps rows with unknown or absent direction in a neutral style", async () => {
    routes[ALICE_INTERACTIONS_URL] = {
      status: 200,
      body: [
        { ...ALICE_INTERACTIONS[0], id: "ix-call", direction: "call", summary: "Phone call" },
        { ...ALICE_INTERACTIONS[1], id: "ix-null", direction: null, summary: "Met for coffee" },
      ],
    };
    renderTab();
    await selectAlice();
    expect(await screen.findByText("Phone call")).toBeDefined();
    expect(screen.getByText("Met for coffee")).toBeDefined();
    expect(screen.getByText("call").className).toContain("text-muted-foreground");
    expect(screen.getByText("Note").className).toContain("text-muted-foreground");
  });

  it("renders interaction dates in the owner timezone", async () => {
    routes[ALICE_INTERACTIONS_URL] = {
      status: 200,
      body: [
        {
          ...ALICE_INTERACTIONS[0],
          occurred_at: "2025-12-31T17:00:00Z",
          summary: "Boundary interaction",
        },
      ],
    };

    renderTab();
    await selectAlice();

    const boundaryDate = await screen.findByText(/Jan 1, 2026/);
    expect(boundaryDate.tagName).toBe("TIME");
    expect(boundaryDate.getAttribute("datetime")).toBe("2025-12-31T17:00:00.000Z");
  });

  it("clears the selection when the same row is chosen again", async () => {
    renderTab();
    await selectAlice();
    await screen.findAllByTestId("thread-item");
    await selectAlice();
    expect(screen.getByTestId("thread-empty-prompt")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Known facts panel
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — known facts panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("shows prompt when no contact is selected", () => {
    renderTab();
    expect(screen.getByTestId("facts-empty-prompt")).toBeDefined();
  });

  it("renders entity_info channels, roles and last contact from the entity reads", async () => {
    renderTab();
    await selectAlice();
    expect(await screen.findByText("Email: alice@example.com")).toBeDefined();
    expect(screen.getByText("Roles: friend, neighbour")).toBeDefined();
    expect(screen.getByText("Last seen: 8d ago")).toBeDefined();
    // A secured channel with no revealed value is not listed.
    expect(screen.queryByText(/^Phone:/)).toBeNull();
    expect(requested).toContain(ALICE_DETAIL_URL);
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty states
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — empty states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty state for tier distribution when no entries", () => {
    renderTab();
    expect(screen.queryByTestId("tier-distribution-list")).toBeNull();
    expect(screen.getByText("No tier data available.")).toBeDefined();
  });

  it("shows empty state for overdue when all clear", () => {
    renderTab();
    expect(screen.queryByTestId("overdue-list")).toBeNull();
    expect(screen.getByText("No overdue contacts. Cadence all clear.")).toBeDefined();
  });

  it("does not render cadence all-clear when provenance is unavailable", () => {
    vi.mocked(useOverdueContacts).mockReturnValue({
      data: { contacts: [], cadence_available: false, unmeasurable_count: 2 },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useOverdueContacts>);
    renderTab();
    expect(screen.queryByText("No overdue contacts. Cadence all clear.")).toBeNull();
    expect(
      screen.getByText("Cadence instrumentation or provenance unavailable."),
    ).toBeDefined();
  });

  it("shows empty state for watchlist when no T1/T2 entries", () => {
    renderTab();
    expect(screen.queryByTestId("watchlist-table")).toBeNull();
    expect(screen.getByText("No T1 or T2 contacts yet.")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Error states [bu-mnnoo]
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — error states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithError();
  });
  afterEach(() => cleanup());

  it("shows error state for KPI strip when all reads error", async () => {
    renderTab();
    expect(await screen.findByText("Could not load relationship overview.")).toBeDefined();
  });

  it("shows error state for tier distribution when dunbar hook errors", () => {
    renderTab();
    expect(screen.getByText("Could not load tier distribution.")).toBeDefined();
  });

  it("shows error state for overdue panel when overdue hook errors", () => {
    renderTab();
    expect(screen.getByText("Could not load overdue contacts.")).toBeDefined();
  });

  it("shows error state for watchlist when dunbar hook errors", () => {
    renderTab();
    expect(screen.getByText("Could not load watchlist.")).toBeDefined();
  });

  it("renders error-state-line elements (not empty-state or data)", async () => {
    renderTab();
    await waitFor(() =>
      expect(screen.getAllByTestId("error-state-line").length).toBeGreaterThanOrEqual(4),
    );
    expect(screen.queryByTestId("tier-distribution-list")).toBeNull();
    expect(screen.queryByTestId("overdue-list")).toBeNull();
    expect(screen.queryByTestId("watchlist-table")).toBeNull();
  });
});

describe("ButlerRelationshipContactsTab — thread panel error state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it.each([404, 500])("renders the error line when interactions return %i", async (status) => {
    routes[ALICE_INTERACTIONS_URL] = { status, body: { detail: "unavailable" } };
    renderTab();
    await selectAlice();
    expect(await screen.findByText("Could not load thread.")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading states
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — loading states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading placeholders while loading", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty-state text while loading", () => {
    renderTab();
    expect(screen.queryByTestId("empty-state-line")).toBeNull();
  });

  it("does not render tier-distribution-list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("tier-distribution-list")).toBeNull();
  });

  it("does not render overdue-list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("overdue-list")).toBeNull();
  });

  it("does not render watchlist-table while loading", () => {
    renderTab();
    expect(screen.queryByTestId("watchlist-table")).toBeNull();
  });
});

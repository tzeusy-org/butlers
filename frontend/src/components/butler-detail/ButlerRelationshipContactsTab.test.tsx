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
 *  - Known facts panel shows contact details for selected contact
 *  - Selecting a watchlist row switches thread and facts panels
 *
 * bead: bu-iuol4.21
 */

import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerRelationshipContactsTab from "./ButlerRelationshipContactsTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-contacts", () => ({
  useContacts: vi.fn(),
  useContact: vi.fn(),
  useContactInteractions: vi.fn(),
  useOverdueContacts: vi.fn(),
}));

vi.mock("@/hooks/use-memory", () => ({
  useDunbarRanking: vi.fn(),
}));

import { useContacts, useContact, useContactInteractions, useOverdueContacts } from "@/hooks/use-contacts";
import { useDunbarRanking } from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Fixed clock — prevents date-formatting flakes
// ---------------------------------------------------------------------------

const FIXED_NOW_ISO = "2026-05-10T08:00:00.000Z";

beforeAll(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(FIXED_NOW_ISO));
});

afterAll(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const CONTACTS_DATA = {
  contacts: [
    {
      id: "c-1",
      full_name: "Alice Smith",
      first_name: "Alice",
      last_name: "Smith",
      nickname: null,
      email: "alice@example.com",
      phone: null,
      labels: [{ id: "l-1", name: "friend", color: null }],
      last_interaction_at: "2026-05-01T10:00:00Z",
      warmth: 0.82,
    },
    {
      id: "c-2",
      full_name: "Bob Jones",
      first_name: "Bob",
      last_name: "Jones",
      nickname: null,
      email: null,
      phone: null,
      labels: [],
      last_interaction_at: null,
      warmth: 0.35,
    },
  ],
  total: 42,
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
};

const INTERACTIONS_DATA = {
  contact_id: "c-1",
  interactions: [
    {
      ts: "2026-05-01T09:00:00Z",
      direction: "in" as const,
      text: "Hey, how are you doing?",
    },
    {
      ts: "2026-05-01T10:00:00Z",
      direction: "out" as const,
      text: "Doing great, thanks for checking in!",
    },
    {
      ts: "2026-05-02T08:00:00Z",
      direction: "drafted" as const,
      text: "Draft: following up on our conversation.",
    },
  ],
};

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
// Default mock setup: all data loaded
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useContacts).mockReturnValue({
    data: CONTACTS_DATA,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useContacts>);

  vi.mocked(useContact).mockReturnValue({
    data: {
      ...CONTACTS_DATA.contacts[0],
      notes: null,
      birthday: null,
      company: null,
      job_title: null,
      address: null,
      metadata: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-05-10T00:00:00Z",
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useContact>);

  vi.mocked(useDunbarRanking).mockReturnValue({
    data: DUNBAR_DATA,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useDunbarRanking>);

  vi.mocked(useOverdueContacts).mockReturnValue({
    data: OVERDUE_DATA,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useOverdueContacts>);

  vi.mocked(useContactInteractions).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useContactInteractions>);
}

function setupWithInteractions() {
  setupWithData();
  vi.mocked(useContactInteractions).mockReturnValue({
    data: INTERACTIONS_DATA,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useContactInteractions>);
}

function setupEmpty() {
  vi.mocked(useContacts).mockReturnValue({
    data: { contacts: [], total: 0 },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useContacts>);

  vi.mocked(useContact).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useContact>);

  vi.mocked(useDunbarRanking).mockReturnValue({
    data: { entries: [], owner_entity_id: null },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useDunbarRanking>);

  vi.mocked(useOverdueContacts).mockReturnValue({
    data: { contacts: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useOverdueContacts>);

  vi.mocked(useContactInteractions).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useContactInteractions>);
}

function setupLoading() {
  vi.mocked(useContacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useContacts>);

  vi.mocked(useContact).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useContact>);

  vi.mocked(useDunbarRanking).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useDunbarRanking>);

  vi.mocked(useOverdueContacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useOverdueContacts>);

  vi.mocked(useContactInteractions).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useContactInteractions>);
}

function setupWithError() {
  vi.mocked(useContacts).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useContacts>);

  vi.mocked(useContact).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useContact>);

  vi.mocked(useDunbarRanking).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useDunbarRanking>);

  vi.mocked(useOverdueContacts).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useOverdueContacts>);

  vi.mocked(useContactInteractions).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as ReturnType<typeof useContactInteractions>);
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

  it("renders total tracked contacts count", () => {
    renderTab();
    expect(screen.getByText("42")).toBeDefined();
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

  it("renders thread items after selecting a contact", () => {
    renderTab();
    // Click a watchlist row to select Alice
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    expect(aliceRow).toBeDefined();
    fireEvent.click(aliceRow!);
    const items = screen.getAllByTestId("thread-item");
    expect(items.length).toBe(3);
  });

  it("shows inbound interaction direction label", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    fireEvent.click(aliceRow!);
    // "In" direction label should appear
    expect(screen.getByText("In")).toBeDefined();
  });

  it("shows outbound interaction direction label", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    fireEvent.click(aliceRow!);
    expect(screen.getByText("Out")).toBeDefined();
  });

  it("shows drafted interaction direction label", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    fireEvent.click(aliceRow!);
    expect(screen.getByText("Draft")).toBeDefined();
  });

  it("shows thread interaction text", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    fireEvent.click(aliceRow!);
    expect(screen.getByText("Hey, how are you doing?")).toBeDefined();
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

  it("renders facts list after selecting a contact with email", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    fireEvent.click(aliceRow!);
    expect(screen.getByTestId("known-facts-list")).toBeDefined();
  });

  it("shows email fact for selected contact", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    fireEvent.click(aliceRow!);
    // Alice has email alice@example.com
    expect(screen.getByText("Email: alice@example.com")).toBeDefined();
  });

  it("shows labels fact for selected contact", () => {
    renderTab();
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    fireEvent.click(aliceRow!);
    expect(screen.getByText("Labels: friend")).toBeDefined();
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

  it("shows error state for KPI strip when all hooks error", () => {
    renderTab();
    expect(screen.getByText("Could not load relationship overview.")).toBeDefined();
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

  it("renders error-state-line elements (not empty-state or data)", () => {
    renderTab();
    const errorLines = screen.getAllByTestId("error-state-line");
    expect(errorLines.length).toBeGreaterThanOrEqual(4);
    expect(screen.queryByTestId("tier-distribution-list")).toBeNull();
    expect(screen.queryByTestId("overdue-list")).toBeNull();
    expect(screen.queryByTestId("watchlist-table")).toBeNull();
  });
});

describe("ButlerRelationshipContactsTab — thread panel error state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Use normal data for dunbar (watchlist visible) but error for interactions
    vi.mocked(useContacts).mockReturnValue({
      data: CONTACTS_DATA,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useContacts>);

    vi.mocked(useContact).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useContact>);

    vi.mocked(useDunbarRanking).mockReturnValue({
      data: DUNBAR_DATA,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useDunbarRanking>);

    vi.mocked(useOverdueContacts).mockReturnValue({
      data: OVERDUE_DATA,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useOverdueContacts>);

    vi.mocked(useContactInteractions).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as ReturnType<typeof useContactInteractions>);
  });
  afterEach(() => cleanup());

  it("shows error message in thread panel when interactions hook errors (after contact selected)", () => {
    renderTab();
    // Select a contact so the thread panel becomes active
    const rows = screen.getAllByTestId("watchlist-row");
    const aliceRow = rows.find((r) => r.textContent?.includes("Alice Smith"));
    expect(aliceRow).toBeDefined();
    fireEvent.click(aliceRow!);
    expect(screen.getByText("Could not load thread.")).toBeDefined();
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

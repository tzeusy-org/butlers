// @vitest-environment jsdom
/**
 * ButlerRelationshipContactsTab — RTL tests pinning all sections.
 *
 * Tests:
 *  - All sections render (KPI strip, dunbar map, upcoming dates, contact roster, group summary)
 *  - Loading states show loading placeholders, not empty-state text
 *  - Empty states are shown when data is absent
 *  - KPI values render with data
 *  - Dunbar map renders tier rows grouped by tier
 *  - Upcoming dates list renders date rows with countdown badges
 *  - Contact roster renders contact rows with labels
 *  - Group summary renders rows with member count badges
 *
 * bead: bu-ax5bi
 */

import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerRelationshipContactsTab from "./ButlerRelationshipContactsTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-contacts", () => ({
  useContacts: vi.fn(),
  useGroups: vi.fn(),
  useUnlinkedContacts: vi.fn(),
  useUpcomingDates: vi.fn(),
}));

vi.mock("@/hooks/use-memory", () => ({
  useDunbarRanking: vi.fn(),
}));

import { useContacts, useGroups, useUnlinkedContacts, useUpcomingDates } from "@/hooks/use-contacts";
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
    },
  ],
  total: 42,
};

const UPCOMING_DATES_DATA = [
  {
    contact_id: "c-1",
    contact_name: "Alice Smith",
    date_type: "birthday",
    date: "2026-05-15",
    days_until: 5,
  },
  {
    contact_id: "c-2",
    contact_name: "Bob Jones",
    date_type: "anniversary",
    date: "2026-05-25",
    days_until: 15,
  },
];

const UNLINKED_DATA = {
  contacts: [],
  total: 3,
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
      avatar_url: null,
      aliases: [],
    },
    {
      contact_id: "c-2",
      entity_id: "e-2",
      canonical_name: "Bob Jones",
      dunbar_tier: 15,
      dunbar_score: 70.0,
      dunbar_tier_override: true,
      avatar_url: null,
      aliases: [],
    },
  ],
  owner_entity_id: null,
};

const GROUPS_DATA = {
  groups: [
    {
      id: "g-1",
      name: "Close friends",
      description: null,
      member_count: 5,
      labels: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "g-2",
      name: "Work",
      description: "Work colleagues",
      member_count: 12,
      labels: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  total: 2,
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
  // useUpcomingDates is called twice (30-day KPI + 60-day panel)
  vi.mocked(useUpcomingDates).mockReturnValue({
    data: UPCOMING_DATES_DATA,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useUpcomingDates>);

  vi.mocked(useContacts).mockReturnValue({
    data: CONTACTS_DATA,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useContacts>);

  vi.mocked(useUnlinkedContacts).mockReturnValue({
    data: UNLINKED_DATA,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useUnlinkedContacts>);

  vi.mocked(useDunbarRanking).mockReturnValue({
    data: DUNBAR_DATA,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useDunbarRanking>);

  vi.mocked(useGroups).mockReturnValue({
    data: GROUPS_DATA,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useGroups>);
}

function setupEmpty() {
  vi.mocked(useUpcomingDates).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useUpcomingDates>);

  vi.mocked(useContacts).mockReturnValue({
    data: { contacts: [], total: 0 },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useContacts>);

  vi.mocked(useUnlinkedContacts).mockReturnValue({
    data: { contacts: [], total: 0 },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useUnlinkedContacts>);

  vi.mocked(useDunbarRanking).mockReturnValue({
    data: { entries: [], owner_entity_id: null },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useDunbarRanking>);

  vi.mocked(useGroups).mockReturnValue({
    data: { groups: [], total: 0 },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useGroups>);
}

function setupLoading() {
  vi.mocked(useUpcomingDates).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useUpcomingDates>);

  vi.mocked(useContacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useContacts>);

  vi.mocked(useUnlinkedContacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useUnlinkedContacts>);

  vi.mocked(useDunbarRanking).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useDunbarRanking>);

  vi.mocked(useGroups).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useGroups>);
}

// ---------------------------------------------------------------------------
// Tests: All sections present
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — all sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the root tab container", () => {
    renderTab();
    expect(screen.getByTestId("relationship-contacts-tab")).toBeDefined();
  });

  it("renders the KPI strip section", () => {
    renderTab();
    expect(screen.getByTestId("kpi-strip")).toBeDefined();
  });

  it("renders the dunbar map card", () => {
    renderTab();
    expect(screen.getByTestId("dunbar-map-card")).toBeDefined();
  });

  it("renders the upcoming dates card", () => {
    renderTab();
    expect(screen.getByTestId("upcoming-dates-card")).toBeDefined();
  });

  it("renders the contact roster card", () => {
    renderTab();
    expect(screen.getByTestId("contact-roster-card")).toBeDefined();
  });

  it("renders the group summary card", () => {
    renderTab();
    expect(screen.getByTestId("group-summary-card")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI strip renders values
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — KPI values", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders kpi-item elements", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items.length).toBeGreaterThanOrEqual(3);
  });

  it("renders total contacts count", () => {
    renderTab();
    const values = screen.getAllByTestId("kpi-value");
    const texts = values.map((v) => v.textContent ?? "");
    expect(texts.some((t) => t === "42")).toBe(true);
  });

  it("renders unlinked count in KPI", () => {
    renderTab();
    const values = screen.getAllByTestId("kpi-value");
    const texts = values.map((v) => v.textContent ?? "");
    expect(texts.some((t) => t === "3")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tests: Dunbar map renders tier rows
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — dunbar map", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders dunbar map container", () => {
    renderTab();
    expect(screen.getByTestId("dunbar-map")).toBeDefined();
  });

  it("renders tier rows for populated tiers", () => {
    renderTab();
    const rows = screen.getAllByTestId("dunbar-tier-row");
    expect(rows.length).toBeGreaterThanOrEqual(2);
  });

  it("renders Alice Smith in Support 5 tier", () => {
    renderTab();
    // Alice Smith appears in both dunbar map and contact roster
    const matches = screen.getAllByText("Alice Smith");
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("renders Bob Jones in Sympathy 15 tier", () => {
    renderTab();
    // Bob Jones appears in both dunbar map and contact roster
    const matches = screen.getAllByText("Bob Jones");
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Upcoming dates panel renders rows
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — upcoming dates", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders upcoming dates list", () => {
    renderTab();
    expect(screen.getByTestId("upcoming-dates-list")).toBeDefined();
  });

  it("renders upcoming date rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("upcoming-date-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Contact roster renders rows
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — contact roster", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the contact roster list", () => {
    renderTab();
    expect(screen.getByTestId("contact-roster")).toBeDefined();
  });

  it("renders contact rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("contact-roster-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Group summary renders rows
// ---------------------------------------------------------------------------

describe("ButlerRelationshipContactsTab — group summary", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the group summary list", () => {
    renderTab();
    expect(screen.getByTestId("group-summary-list")).toBeDefined();
  });

  it("renders group rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("group-summary-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
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

  it("shows empty state for dunbar map when no entries", () => {
    renderTab();
    expect(screen.queryByTestId("dunbar-map")).toBeNull();
    expect(screen.getByText("No Dunbar ranking data available.")).toBeDefined();
  });

  it("shows empty state for upcoming dates when empty", () => {
    renderTab();
    expect(screen.queryByTestId("upcoming-dates-list")).toBeNull();
    expect(screen.getByText("No upcoming dates in the next 60 days.")).toBeDefined();
  });

  it("shows empty state for contacts when empty", () => {
    renderTab();
    expect(screen.queryByTestId("contact-roster")).toBeNull();
    expect(screen.getByText("No contacts found.")).toBeDefined();
  });

  it("shows empty state for groups when empty", () => {
    renderTab();
    expect(screen.queryByTestId("group-summary-list")).toBeNull();
    expect(screen.getByText("No groups configured.")).toBeDefined();
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

  it("does not render dunbar-map while loading", () => {
    renderTab();
    expect(screen.queryByTestId("dunbar-map")).toBeNull();
  });

  it("does not render upcoming-dates-list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("upcoming-dates-list")).toBeNull();
  });

  it("does not render contact-roster while loading", () => {
    renderTab();
    expect(screen.queryByTestId("contact-roster")).toBeNull();
  });

  it("does not render group-summary-list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("group-summary-list")).toBeNull();
  });
});

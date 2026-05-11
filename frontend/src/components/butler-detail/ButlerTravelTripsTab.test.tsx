// @vitest-environment jsdom
/**
 * ButlerTravelTripsTab — RTL tests pinning the redesigned 4-col panel grid.
 *
 * Tests:
 *  - Renders three rows (KPI strip, week ahead + checklist, trips roster; no drawer by default)
 *  - Loading state shows placeholders, not empty-state text
 *  - Empty states are shown explicitly (no infinite spinner)
 *  - KPI values reflect data
 *  - Week ahead schedule renders legs and check-ins within 7 days
 *  - Week ahead schedule shows empty state when no near-term legs
 *  - Upcoming checklist renders pre-trip actions ranked by urgency
 *  - Trip roster rows render with status badges
 *  - Trip detail drawer opens on row-click
 *  - Trip detail drawer closes on close button
 *
 * bead: bu-iuol4.36 (redesign)
 * original bead: bu-0eac9
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerTravelTripsTab from "./ButlerTravelTripsTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-travel", () => ({
  useUpcomingTravel: vi.fn(),
  useTravelTrips: vi.fn(),
  useTravelTripSummary: vi.fn(),
  useExpiringDocuments: vi.fn(),
}));

import {
  useUpcomingTravel,
  useTravelTrips,
  useTravelTripSummary,
  useExpiringDocuments,
} from "@/hooks/use-travel";

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

// Departure in 2 days from "now" so it appears in week-ahead. Test-local date
// string computed relative to a fixed offset from today is not needed since
// we control the hook return value directly.
const NEAR_DEPARTURE_AT = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
const FAR_DEPARTURE_AT = "2026-06-10T11:00:00Z"; // Fixed far-future date — outside 7d window

const UPCOMING_DATA = {
  upcoming_trips: [
    {
      trip: {
        id: "trip-1",
        name: "Tokyo Adventure",
        destination: "Tokyo, Japan",
        start_date: "2026-06-10",
        end_date: "2026-06-24",
        status: "planned",
        metadata: {},
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
      legs: [
        {
          id: "leg-near",
          trip_id: "trip-1",
          type: "flight",
          carrier: "ANA",
          departure_airport_station: "SFO",
          departure_city: "San Francisco",
          departure_at: NEAR_DEPARTURE_AT,
          arrival_airport_station: "NRT",
          arrival_city: "Tokyo",
          arrival_at: new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString(),
          confirmation_number: "ABC123",
          pnr: "XYZ",
          seat: null,
          metadata: {},
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      accommodations: [],
      days_until_departure: 31,
    },
    {
      trip: {
        id: "trip-2",
        name: "Paris Weekend",
        destination: "Paris, France",
        start_date: "2026-08-01",
        end_date: "2026-08-05",
        status: "active",
        metadata: {},
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
      legs: [],
      accommodations: [],
      days_until_departure: 83,
    },
  ],
  actions: [
    {
      trip_id: "trip-1",
      trip_name: "Tokyo Adventure",
      type: "missing_boarding_pass",
      message: "No boarding pass attached — upload or link boarding pass before departure",
      severity: "high",
      urgency_rank: 1,
    },
    {
      trip_id: "trip-1",
      trip_name: "Tokyo Adventure",
      type: "unassigned_seat",
      message: "1 flight leg(s) have no seat assigned — consider selecting seats",
      severity: "low",
      urgency_rank: 2,
    },
  ],
  window_start: "2026-05-10",
  window_end: "2026-08-07",
};

const TRIPS_PAGE = {
  data: [
    {
      id: "trip-1",
      name: "Tokyo Adventure",
      destination: "Tokyo, Japan",
      start_date: "2026-06-10",
      end_date: "2026-06-24",
      status: "planned",
      metadata: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "trip-2",
      name: "Paris Weekend",
      destination: "Paris, France",
      start_date: "2026-08-01",
      end_date: "2026-08-05",
      status: "active",
      metadata: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  meta: { total: 2, offset: 0, limit: 10, has_more: false },
};

const TRIP_SUMMARY = {
  trip: {
    id: "trip-1",
    name: "Tokyo Adventure",
    destination: "Tokyo, Japan",
    start_date: "2026-06-10",
    end_date: "2026-06-24",
    status: "planned",
    metadata: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  legs: [
    {
      id: "leg-1",
      trip_id: "trip-1",
      type: "flight",
      carrier: "ANA",
      departure_airport_station: "SFO",
      departure_city: "San Francisco",
      departure_at: FAR_DEPARTURE_AT,
      arrival_airport_station: "NRT",
      arrival_city: "Tokyo",
      arrival_at: "2026-06-11T15:00:00Z",
      confirmation_number: "ABC123",
      pnr: "XYZ",
      seat: null,
      metadata: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  accommodations: [
    {
      id: "acc-1",
      trip_id: "trip-1",
      type: "hotel",
      name: "Shinjuku Prince Hotel",
      address: "Shinjuku, Tokyo",
      check_in: "2026-06-11",
      check_out: "2026-06-24",
      confirmation_number: "HTL-999",
      metadata: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  reservations: [],
  documents: [],
  timeline: [
    {
      entity_type: "leg",
      entity_id: "leg-1",
      sort_key: FAR_DEPARTURE_AT,
      summary: "Flight San Francisco → Tokyo (ANA)",
    },
    {
      entity_type: "accommodation",
      entity_id: "acc-1",
      sort_key: "2026-06-11",
      summary: "Hotel Shinjuku Prince Hotel",
    },
  ],
  alerts: [
    {
      type: "missing_boarding_pass",
      message: "No boarding pass attached — upload or link boarding pass before departure",
      severity: "high",
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
      <MemoryRouter>
        <ButlerTravelTripsTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function setupWithData() {
  vi.mocked(useUpcomingTravel).mockReturnValue(
    { data: UPCOMING_DATA, isLoading: false } as unknown as ReturnType<typeof useUpcomingTravel>,
  );

  vi.mocked(useTravelTrips).mockReturnValue(
    { data: TRIPS_PAGE, isLoading: false } as unknown as ReturnType<typeof useTravelTrips>,
  );

  vi.mocked(useTravelTripSummary).mockReturnValue(
    { data: TRIP_SUMMARY, isLoading: false } as unknown as ReturnType<typeof useTravelTripSummary>,
  );

  // Default: no expiring documents (banner hidden)
  vi.mocked(useExpiringDocuments).mockReturnValue(
    { data: { documents: [] }, isLoading: false } as unknown as ReturnType<typeof useExpiringDocuments>,
  );
}

function setupEmpty() {
  vi.mocked(useUpcomingTravel).mockReturnValue(
    {
      data: { upcoming_trips: [], actions: [], window_start: "2026-05-10", window_end: "2026-08-07" },
      isLoading: false,
    } as unknown as ReturnType<typeof useUpcomingTravel>,
  );

  vi.mocked(useTravelTrips).mockReturnValue(
    {
      data: { data: [], meta: { total: 0, offset: 0, limit: 10, has_more: false } },
      isLoading: false,
    } as unknown as ReturnType<typeof useTravelTrips>,
  );

  vi.mocked(useTravelTripSummary).mockReturnValue(
    { data: undefined, isLoading: false } as unknown as ReturnType<typeof useTravelTripSummary>,
  );

  vi.mocked(useExpiringDocuments).mockReturnValue(
    { data: { documents: [] }, isLoading: false } as unknown as ReturnType<typeof useExpiringDocuments>,
  );
}

function setupLoading() {
  vi.mocked(useUpcomingTravel).mockReturnValue(
    { data: undefined, isLoading: true } as unknown as ReturnType<typeof useUpcomingTravel>,
  );

  vi.mocked(useTravelTrips).mockReturnValue(
    { data: undefined, isLoading: true } as unknown as ReturnType<typeof useTravelTrips>,
  );

  vi.mocked(useTravelTripSummary).mockReturnValue(
    { data: undefined, isLoading: false } as unknown as ReturnType<typeof useTravelTripSummary>,
  );

  vi.mocked(useExpiringDocuments).mockReturnValue(
    { data: undefined, isLoading: true } as unknown as ReturnType<typeof useExpiringDocuments>,
  );
}

// ---------------------------------------------------------------------------
// Tests: sections present
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders the top-level tab container", () => {
    renderTab();
    expect(screen.getByTestId("travel-trips-tab")).toBeDefined();
  });

  it("renders the KPI strip", () => {
    renderTab();
    expect(screen.getByTestId("travel-kpi-strip")).toBeDefined();
  });

  it("renders the pre-trip actions list (checklist panel)", () => {
    renderTab();
    expect(screen.getByTestId("pre-trip-actions-list")).toBeDefined();
  });

  it("renders the trip roster", () => {
    renderTab();
    expect(screen.getByTestId("trip-roster-list")).toBeDefined();
  });

  it("does not render the trip detail drawer by default", () => {
    renderTab();
    expect(screen.queryByTestId("trip-detail-drawer")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI values
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — KPI values", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders four KPI cells", () => {
    renderTab();
    expect(screen.getByTestId("kpi-next-departure")).toBeDefined();
    expect(screen.getByTestId("kpi-active-count")).toBeDefined();
    expect(screen.getByTestId("kpi-planned-count")).toBeDefined();
    expect(screen.getByTestId("kpi-open-actions")).toBeDefined();
  });

  it("shows next departure trip name", () => {
    renderTab();
    const kpi = screen.getByTestId("kpi-next-departure");
    expect(kpi.textContent).toContain("Tokyo Adventure");
  });

  it("shows active trip count", () => {
    renderTab();
    expect(screen.getByTestId("kpi-active-count").textContent).toBe("1");
  });

  it("shows planned trip count", () => {
    renderTab();
    expect(screen.getByTestId("kpi-planned-count").textContent).toBe("1");
  });

  it("shows open actions count", () => {
    renderTab();
    expect(screen.getByTestId("kpi-open-actions").textContent).toBe("2");
  });

  it("shows '—' for next departure KPI when no upcoming trips", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    expect(screen.getByTestId("kpi-next-departure").textContent).toBe("—");
  });
});

// ---------------------------------------------------------------------------
// Tests: Week ahead schedule
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — week ahead schedule", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders week-ahead entries when legs are within 7 days", () => {
    renderTab();
    const entries = screen.getAllByTestId("week-ahead-entry");
    // UPCOMING_DATA has one leg with departure NEAR_DEPARTURE_AT (2 days away)
    expect(entries.length).toBeGreaterThanOrEqual(1);
  });

  it("shows the route label for a near-term leg", () => {
    renderTab();
    const list = screen.getByTestId("week-ahead-list");
    // Route includes "San Francisco → Tokyo" from departure_city and arrival_city
    expect(list.textContent).toContain("San Francisco");
    expect(list.textContent).toContain("Tokyo");
  });

  it("shows empty state when no legs or check-ins are within 7 days", () => {
    // Use empty data (no legs at all)
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    // At least one empty-state-line should appear (from week-ahead and/or checklist)
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
    // The week-ahead specific message
    const found = emptyLines.some((el) =>
      el.textContent?.includes("No legs or check-ins in the next 7 days"),
    );
    expect(found).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tests: Upcoming checklist (pre-trip actions)
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — upcoming checklist", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders pre-trip action items ranked by urgency", () => {
    renderTab();
    const items = screen.getAllByTestId("pre-trip-action-item");
    expect(items.length).toBe(2);
  });

  it("shows the high-severity action first (urgency_rank 1)", () => {
    renderTab();
    const items = screen.getAllByTestId("pre-trip-action-item");
    // First item should contain the high-severity message
    expect(items[0].textContent).toContain("No boarding pass attached");
  });

  it("shows the low-severity action second (urgency_rank 2)", () => {
    renderTab();
    const items = screen.getAllByTestId("pre-trip-action-item");
    expect(items[1].textContent).toContain("no seat assigned");
  });

  it("shows empty state when no actions", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    expect(screen.queryByTestId("pre-trip-actions-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    const found = emptyLines.some((el) =>
      el.textContent?.includes("All clear"),
    );
    expect(found).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tests: Trip roster
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — trip roster", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders trip roster rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("trip-roster-row");
    expect(rows.length).toBe(2);
  });

  it("shows trip names in the roster", () => {
    renderTab();
    // Use getAllByText since "Tokyo Adventure" also appears in other panels
    expect(screen.getAllByText("Tokyo Adventure").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Paris Weekend")).toBeDefined();
  });

  it("shows empty state for trip roster when no trips", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    expect(screen.queryByTestId("trip-roster-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Trip detail drawer — open/close
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — trip detail drawer", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("opens the trip detail drawer on row click", () => {
    renderTab();
    const rows = screen.getAllByTestId("trip-roster-row");
    fireEvent.click(rows[0]);
    expect(screen.getByTestId("trip-detail-drawer")).toBeDefined();
  });

  it("shows the trip name in the drawer header", () => {
    renderTab();
    fireEvent.click(screen.getAllByTestId("trip-roster-row")[0]);
    expect(screen.getByTestId("trip-detail-drawer")).toBeDefined();
    const drawer = screen.getByTestId("trip-detail-drawer");
    expect(drawer.textContent).toContain("Tokyo Adventure");
  });

  it("renders timeline entries in the drawer", () => {
    renderTab();
    fireEvent.click(screen.getAllByTestId("trip-roster-row")[0]);
    const entries = screen.getAllByTestId("timeline-entry");
    expect(entries.length).toBeGreaterThanOrEqual(1);
  });

  it("closes the drawer on close button click", () => {
    renderTab();
    fireEvent.click(screen.getAllByTestId("trip-roster-row")[0]);
    expect(screen.getByTestId("trip-detail-drawer")).toBeDefined();
    fireEvent.click(screen.getByTestId("trip-drawer-close"));
    expect(screen.queryByTestId("trip-detail-drawer")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });

  afterEach(() => cleanup());

  it("shows loading placeholders while fetching upcoming travel", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty-state lines while loading", () => {
    renderTab();
    expect(screen.queryByTestId("empty-state-line")).toBeNull();
  });

  it("shows '…' for KPI values while loading", () => {
    renderTab();
    for (const testId of ["kpi-next-departure", "kpi-active-count", "kpi-planned-count", "kpi-open-actions"]) {
      expect(screen.getByTestId(testId).textContent).toBe("…");
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Expiring docs banner
// ---------------------------------------------------------------------------

const EXPIRING_DOC_SOON = {
  id: "doc-1",
  trip_id: "trip-1",
  type: "passport",
  name: "US Passport",
  expiry_date: "2026-06-01",
  days_until_expiry: 21,
};

const EXPIRING_DOC_MEDIUM = {
  id: "doc-2",
  trip_id: "trip-2",
  type: "visa",
  name: null,
  expiry_date: "2026-10-01",
  days_until_expiry: 143,
};

describe("ButlerTravelTripsTab — expiring docs banner", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("does not render banner when no expiring documents", () => {
    // setupWithData already sets empty expiring docs
    renderTab();
    expect(screen.queryByTestId("expiring-docs-banner")).toBeNull();
  });

  it("renders banner when documents are expiring", () => {
    vi.mocked(useExpiringDocuments).mockReturnValue(
      {
        data: { documents: [EXPIRING_DOC_MEDIUM] },
        isLoading: false,
      } as unknown as ReturnType<typeof useExpiringDocuments>,
    );
    renderTab();
    expect(screen.getByTestId("expiring-docs-banner")).toBeDefined();
  });

  it("shows the count of expiring documents", () => {
    vi.mocked(useExpiringDocuments).mockReturnValue(
      {
        data: { documents: [EXPIRING_DOC_SOON, EXPIRING_DOC_MEDIUM] },
        isLoading: false,
      } as unknown as ReturnType<typeof useExpiringDocuments>,
    );
    renderTab();
    const count = screen.getByTestId("expiring-docs-count");
    expect(count.textContent).toBe("2");
  });

  it("applies destructive tone when urgent document is expiring within 30 days", () => {
    vi.mocked(useExpiringDocuments).mockReturnValue(
      {
        data: { documents: [EXPIRING_DOC_SOON] },
        isLoading: false,
      } as unknown as ReturnType<typeof useExpiringDocuments>,
    );
    renderTab();
    const count = screen.getByTestId("expiring-docs-count");
    expect(count.className).toContain("text-destructive");
  });

  it("applies amber tone when no document is expiring within 30 days", () => {
    vi.mocked(useExpiringDocuments).mockReturnValue(
      {
        data: { documents: [EXPIRING_DOC_MEDIUM] },
        isLoading: false,
      } as unknown as ReturnType<typeof useExpiringDocuments>,
    );
    renderTab();
    const count = screen.getByTestId("expiring-docs-count");
    expect(count.className).toContain("text-amber-500");
  });

  it("banner has role=alert for accessibility", () => {
    vi.mocked(useExpiringDocuments).mockReturnValue(
      {
        data: { documents: [EXPIRING_DOC_MEDIUM] },
        isLoading: false,
      } as unknown as ReturnType<typeof useExpiringDocuments>,
    );
    renderTab();
    const banner = screen.getByTestId("expiring-docs-banner");
    expect(banner.getAttribute("role")).toBe("alert");
  });
});

// ---------------------------------------------------------------------------
// Tests: ButlerDetailPage integration — travel tab in getAllTabs
// ---------------------------------------------------------------------------

import { getAllTabs, isValidTab } from "@/pages/butler-detail-tabs";

describe("ButlerDetailPage — travel trips tab in getAllTabs", () => {
  it("travel butler has 'trips' tab in operator mode", () => {
    expect(getAllTabs("travel", "operator")).toContain("trips");
  });

  it("travel butler has 'trips' tab in resident mode", () => {
    expect(getAllTabs("travel", "resident")).toContain("trips");
  });

  it("'trips' is a valid tab for travel butler in both modes", () => {
    expect(isValidTab("trips", "travel", "operator")).toBe(true);
    expect(isValidTab("trips", "travel", "resident")).toBe(true);
  });

  it("'trips' is NOT a valid tab for non-travel butlers", () => {
    expect(isValidTab("trips", "general", "operator")).toBe(false);
    expect(isValidTab("trips", "health", "resident")).toBe(false);
  });

  it("non-travel butlers do not include 'trips' tab", () => {
    expect(getAllTabs("general", "operator")).not.toContain("trips");
    expect(getAllTabs("health", "resident")).not.toContain("trips");
  });
});

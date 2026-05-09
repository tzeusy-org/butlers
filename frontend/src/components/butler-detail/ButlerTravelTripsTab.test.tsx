// @vitest-environment jsdom
/**
 * ButlerTravelTripsTab — RTL tests pinning four sections.
 *
 * Tests:
 *  - Renders four sections (KPI strip, pre-trip actions, trip roster, no drawer by default)
 *  - Loading state shows placeholders, not empty-state text
 *  - Empty states are shown explicitly (no infinite spinner)
 *  - KPI values reflect data
 *  - Pre-trip actions list renders with severity badges
 *  - Trip roster rows render with status badges
 *  - Trip detail drawer opens on row-click
 *  - Trip detail drawer closes on close button
 *
 * bead: bu-0eac9
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
}));

import {
  useUpcomingTravel,
  useTravelTrips,
  useTravelTripSummary,
} from "@/hooks/use-travel";

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

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
      legs: [],
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
      departure_at: "2026-06-10T11:00:00Z",
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
      sort_key: "2026-06-10T11:00:00Z",
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
}

// ---------------------------------------------------------------------------
// Tests: four sections are rendered
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

  it("renders the pre-trip actions panel", () => {
    renderTab();
    expect(screen.getByTestId("pre-trip-actions-panel")).toBeDefined();
  });

  it("renders the trip roster", () => {
    renderTab();
    expect(screen.getByTestId("trip-roster")).toBeDefined();
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

  it("renders four KPI cards", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    expect(kpiValues.length).toBe(4);
  });

  it("shows next departure trip name with days until", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // First KPI is "Next departure"
    expect(kpiValues[0].textContent).toContain("Tokyo Adventure");
    expect(kpiValues[0].textContent).toContain("31d");
  });

  it("shows active trip count", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // Second KPI is "Active trips" = 1 (Paris is active)
    expect(kpiValues[1].textContent).toBe("1");
  });

  it("shows planned trip count", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // Third KPI is "Planned trips" = 1 (Tokyo is planned)
    expect(kpiValues[2].textContent).toBe("1");
  });

  it("shows open actions count", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // Fourth KPI is "Open actions" = 2
    expect(kpiValues[3].textContent).toBe("2");
  });
});

// ---------------------------------------------------------------------------
// Tests: Pre-trip actions
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — pre-trip actions", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders pre-trip action items", () => {
    renderTab();
    const items = screen.getAllByTestId("pre-trip-action-item");
    expect(items.length).toBe(2);
  });

  it("shows high-severity action message", () => {
    renderTab();
    expect(
      screen.getByText("No boarding pass attached — upload or link boarding pass before departure"),
    ).toBeDefined();
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
    // Use getAllByText since "Tokyo Adventure" also appears in pre-trip actions panel
    expect(screen.getAllByText("Tokyo Adventure").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Paris Weekend")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Trip detail drawer
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
    // "Tokyo Adventure" appears in roster, actions, and drawer header — just confirm drawer is there
    expect(screen.getByTestId("trip-detail-drawer")).toBeDefined();
    // The drawer header shows the trip name
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

  it("closes the drawer on backdrop click", () => {
    renderTab();
    fireEvent.click(screen.getAllByTestId("trip-roster-row")[0]);
    fireEvent.click(screen.getByTestId("drawer-backdrop"));
    expect(screen.queryByTestId("trip-detail-drawer")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty states
// ---------------------------------------------------------------------------

describe("ButlerTravelTripsTab — empty states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });

  afterEach(() => cleanup());

  it("shows empty state for pre-trip actions when no actions", () => {
    renderTab();
    expect(screen.queryByTestId("pre-trip-actions-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state for trip roster when no trips", () => {
    renderTab();
    expect(screen.queryByTestId("trip-roster-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows '—' for next departure KPI when no upcoming trips", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    expect(kpiValues[0].textContent).toBe("—");
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
    const kpiValues = screen.getAllByTestId("kpi-value");
    for (const kpi of kpiValues) {
      expect(kpi.textContent).toBe("…");
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: ButlerDetailPage integration — travel tab in getAllTabs
// ---------------------------------------------------------------------------

import { getAllTabs, isValidTab } from "@/pages/ButlerDetailPage";

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

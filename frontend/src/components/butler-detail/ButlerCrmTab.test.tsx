// @vitest-environment jsdom
/**
 * ButlerCrmTab — RTL tests.
 *
 * Tests cover:
 *  - Root container renders with correct testid
 *  - Non-relationship butler: shows empty state inside Panel, no Card wrapper
 *  - Relationship butler: renders upcoming-dates panel and quick-links panel
 *  - Loading state: skeleton rendered, no rows shown
 *  - Empty dates state: empty-state-line rendered
 *  - Upcoming dates rows rendered when data present
 *  - Quick links: Contacts and Groups links present
 *  - No pid field, no em-dash, no hex/oklch literals in markup
 *
 * bead: bu-j7b5n (follow-up from epic bu-hdavr)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";

import ButlerCrmTab from "./ButlerCrmTab";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-contacts", () => ({
  useUpcomingDates: vi.fn(),
}));

import { useUpcomingDates } from "@/hooks/use-contacts";
import type { UpcomingDate } from "@/api/types";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const UPCOMING_DATES: UpcomingDate[] = [
  {
    contact_id: "c-00000001-aaaa-bbbb-cccc-ddddeeeeff01",
    contact_name: "Alice Smith",
    date_type: "birthday",
    date: "2026-05-15",
    days_until: 2,
  },
  {
    contact_id: "c-00000002-aaaa-bbbb-cccc-ddddeeeeff02",
    contact_name: "Bob Jones",
    date_type: "anniversary",
    date: "2026-05-13",
    days_until: 0,
  },
  {
    contact_id: "c-00000003-aaaa-bbbb-cccc-ddddeeeeff03",
    contact_name: "Carol White",
    date_type: "birthday",
    date: "2026-05-23",
    days_until: 10,
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab(butlerName = "relationship") {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerCrmTab butlerName={butlerName} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function renderTabStatic(butlerName = "relationship") {
  const queryClient = makeQueryClient();
  return renderToStaticMarkup(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ButlerCrmTab butlerName={butlerName} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithDates() {
  vi.mocked(useUpcomingDates).mockReturnValue({
    data: UPCOMING_DATES,
    isLoading: false,
  } as unknown as ReturnType<typeof useUpcomingDates>);
}

function setupEmpty() {
  vi.mocked(useUpcomingDates).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof useUpcomingDates>);
}

function setupLoading() {
  vi.mocked(useUpcomingDates).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as unknown as ReturnType<typeof useUpcomingDates>);
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Tests — Non-relationship butler
// ---------------------------------------------------------------------------

describe("ButlerCrmTab — non-relationship butler", () => {
  it("renders the root container with correct testid", () => {
    setupEmpty();
    renderTab("general");
    expect(screen.getByTestId("butler-crm-tab")).toBeDefined();
  });

  it("renders NO legacy Card wrappers (no data-slot=card)", () => {
    setupEmpty();
    const html = renderTabStatic("general");
    expect(html).not.toContain('data-slot="card"');
  });

  it("renders the panel-grid outer frame", () => {
    setupEmpty();
    const html = renderTabStatic("general");
    expect(html).toContain("border-t");
    expect(html).toContain("border-l");
    expect(html).toContain("grid-cols-1");
  });

  it("renders the unavailable panel", () => {
    setupEmpty();
    const html = renderTabStatic("general");
    expect(html).toContain('data-testid="panel-crm-unavailable"');
  });

  it("shows empty-state line for non-relationship butler", () => {
    setupEmpty();
    renderTab("general");
    expect(screen.getByTestId("empty-state-line")).toBeDefined();
  });

  it("empty state text mentions relationship butler", () => {
    setupEmpty();
    renderTab("general");
    const line = screen.getByTestId("empty-state-line");
    expect(line.textContent).toContain("relationship butler");
  });
});

// ---------------------------------------------------------------------------
// Tests — Relationship butler: structure
// ---------------------------------------------------------------------------

describe("ButlerCrmTab — relationship butler structure", () => {
  it("renders the root container with correct testid", () => {
    setupWithDates();
    renderTab();
    expect(screen.getByTestId("butler-crm-tab")).toBeDefined();
  });

  it("renders NO legacy Card wrappers (no data-slot=card)", () => {
    setupWithDates();
    const html = renderTabStatic();
    expect(html).not.toContain('data-slot="card"');
  });

  it("renders the panel-grid outer frame", () => {
    setupWithDates();
    const html = renderTabStatic();
    expect(html).toContain("border-t");
    expect(html).toContain("border-l");
    expect(html).toContain("grid-cols-1");
  });

  it("renders the upcoming-dates panel", () => {
    setupWithDates();
    const html = renderTabStatic();
    expect(html).toContain('data-testid="panel-upcoming-dates"');
  });

  it("renders the quick-links panel", () => {
    setupWithDates();
    const html = renderTabStatic();
    expect(html).toContain('data-testid="panel-quick-links"');
  });

  it("renders both Contacts and Groups quick links", () => {
    setupWithDates();
    renderTab();
    expect(screen.getByText("Contacts")).toBeDefined();
    expect(screen.getByText("Groups")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests — Relationship butler: upcoming dates data
// ---------------------------------------------------------------------------

describe("ButlerCrmTab — relationship butler: upcoming dates", () => {
  it("renders a row for each upcoming date", () => {
    setupWithDates();
    renderTab();
    const rows = screen.getAllByTestId("upcoming-date-row");
    expect(rows).toHaveLength(3);
  });

  it("renders the contact names in each row", () => {
    setupWithDates();
    renderTab();
    expect(screen.getByText("Alice Smith")).toBeDefined();
    expect(screen.getByText("Bob Jones")).toBeDefined();
    expect(screen.getByText("Carol White")).toBeDefined();
  });

  it("renders the date_type badge in each row", () => {
    setupWithDates();
    renderTab();
    // Two birthdays and one anniversary
    const birthdayBadges = screen.getAllByText("birthday");
    expect(birthdayBadges).toHaveLength(2);
    expect(screen.getByText("anniversary")).toBeDefined();
  });

  it("renders 'Today' badge for days_until === 0", () => {
    setupWithDates();
    renderTab();
    expect(screen.getByText("Today")).toBeDefined();
  });

  it("renders Nd badge for days_until > 1 (not tomorrow)", () => {
    setupWithDates();
    renderTab();
    // Carol White has days_until=10 => "10d"
    expect(screen.getByText("10d")).toBeDefined();
  });

  it("renders contact links pointing to /contacts/:id", () => {
    setupWithDates();
    renderTab();
    const aliceLink = screen.getByText("Alice Smith").closest("a");
    expect(aliceLink?.getAttribute("href")).toContain(UPCOMING_DATES[0].contact_id);
  });
});

// ---------------------------------------------------------------------------
// Tests — Relationship butler: loading state
// ---------------------------------------------------------------------------

describe("ButlerCrmTab — loading state", () => {
  it("renders loading skeletons when isLoading=true", () => {
    setupLoading();
    renderTab();
    expect(screen.getByTestId("upcoming-dates-loading")).toBeDefined();
  });

  it("does NOT render date rows when loading", () => {
    setupLoading();
    renderTab();
    expect(screen.queryAllByTestId("upcoming-date-row")).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Tests — Relationship butler: empty dates state
// ---------------------------------------------------------------------------

describe("ButlerCrmTab — empty dates state", () => {
  it("renders empty-state-line when no upcoming dates", () => {
    setupEmpty();
    renderTab("relationship");
    expect(screen.getByTestId("empty-state-line")).toBeDefined();
  });

  it("empty state mentions 30 days", () => {
    setupEmpty();
    renderTab("relationship");
    const line = screen.getByTestId("empty-state-line");
    expect(line.textContent).toContain("30 days");
  });
});

// ---------------------------------------------------------------------------
// Tests — Doctrine gates
// ---------------------------------------------------------------------------

describe("ButlerCrmTab — doctrine gates", () => {
  it("does NOT render a pid field", () => {
    setupWithDates();
    const html = renderTabStatic();
    expect(html).not.toMatch(/\bpid\b/);
  });

  it("does NOT contain raw hex color literals", () => {
    setupWithDates();
    const html = renderTabStatic();
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  });

  it("does NOT contain raw oklch/rgb color literals", () => {
    setupWithDates();
    const html = renderTabStatic();
    expect(html).not.toMatch(/oklch\s*\(|rgb\s*\(/);
  });

  it("does NOT contain user-visible em-dashes", () => {
    setupWithDates();
    const html = renderTabStatic();
    expect(html).not.toContain("&mdash;");
    expect(html).not.toContain("—");
  });
});

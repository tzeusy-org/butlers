// @vitest-environment jsdom
/**
 * ButlerFinanceFinancesTab — RTL tests pinning the five sections.
 *
 * Tests:
 *  - Renders five sections (KPI strip, transactions, upcoming bills,
 *    subscriptions, category chart)
 *  - Empty states shown when data is empty
 *  - Loading state shows placeholders instead of empty-state text
 *  - KPI values render with data
 *  - Transaction rows render correctly
 *  - Upcoming bills urgency chips render
 *  - Subscription rows render
 *
 * bead: bu-nqepq
 */

import { createElement } from "react";
import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock recharts — avoids SVG/canvas complexity in jsdom
// ---------------------------------------------------------------------------

vi.mock("recharts", () => {
  const BarChart = ({
    children,
  }: {
    data?: Array<Record<string, unknown>>;
    children?: ReactNode;
  }) => createElement("div", { "data-testid": "recharts-bar-chart" }, children);

  const Bar = ({ dataKey }: { dataKey: string }) =>
    createElement("div", { "data-testid": `recharts-bar-${dataKey}` });

  const XAxis = () => null;
  const YAxis = () => null;
  const Tooltip = () => null;
  const Legend = () => null;
  const ResponsiveContainer = ({ children }: { children?: ReactNode }) =>
    createElement(
      "div",
      { "data-testid": "recharts-responsive-container" },
      children,
    );

  return { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer };
});

// ---------------------------------------------------------------------------
// Mock finance hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-finance", () => ({
  useFinanceTransactions: vi.fn(),
  useFinanceSubscriptions: vi.fn(),
  useFinanceUpcomingBills: vi.fn(),
  useFinanceSpendingSummary: vi.fn(),
}));

import {
  useFinanceTransactions,
  useFinanceSubscriptions,
  useFinanceUpcomingBills,
  useFinanceSpendingSummary,
} from "@/hooks/use-finance";

import ButlerFinanceFinancesTab from "./ButlerFinanceFinancesTab";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const TRANSACTIONS = [
  {
    id: "tx-1",
    posted_at: "2026-05-08T10:00:00Z",
    merchant: "Whole Foods",
    normalized_merchant: "Whole Foods Market",
    description: null,
    amount: "45.32",
    currency: "USD",
    direction: "debit",
    category: "groceries",
    inferred_category: null,
    payment_method: null,
    account_id: null,
    receipt_url: null,
    external_ref: null,
    source_message_id: null,
    metadata: {},
    created_at: "2026-05-08T10:01:00Z",
    updated_at: "2026-05-08T10:01:00Z",
  },
  {
    id: "tx-2",
    posted_at: "2026-05-07T14:30:00Z",
    merchant: "Netflix",
    normalized_merchant: null,
    description: "Monthly subscription",
    amount: "15.49",
    currency: "USD",
    direction: "debit",
    category: "subscriptions",
    inferred_category: null,
    payment_method: null,
    account_id: null,
    receipt_url: null,
    external_ref: null,
    source_message_id: null,
    metadata: {},
    created_at: "2026-05-07T14:31:00Z",
    updated_at: "2026-05-07T14:31:00Z",
  },
];

const SUBSCRIPTIONS = [
  {
    id: "sub-1",
    service: "Netflix",
    amount: "15.49",
    currency: "USD",
    frequency: "monthly",
    next_renewal: "2026-06-07",
    status: "active",
    auto_renew: true,
    payment_method: null,
    account_id: null,
    source_message_id: null,
    metadata: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-05-07T14:31:00Z",
  },
  {
    id: "sub-2",
    service: "Spotify",
    amount: "9.99",
    currency: "USD",
    frequency: "monthly",
    next_renewal: "2026-06-15",
    status: "active",
    auto_renew: true,
    payment_method: null,
    account_id: null,
    source_message_id: null,
    metadata: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-05-15T00:00:00Z",
  },
  {
    id: "sub-3",
    service: "Adobe Creative Cloud",
    amount: "54.99",
    currency: "USD",
    frequency: "monthly",
    next_renewal: "2026-06-20",
    status: "cancelled",
    auto_renew: false,
    payment_method: null,
    account_id: null,
    source_message_id: null,
    metadata: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  },
];

const UPCOMING_BILLS = [
  {
    bill: {
      id: "bill-1",
      payee: "Electric Company",
      amount: "84.00",
      currency: "USD",
      due_date: "2026-05-12",
      frequency: "monthly",
      status: "pending",
      payment_method: null,
      account_id: null,
      source_message_id: null,
      statement_period_start: null,
      statement_period_end: null,
      paid_at: null,
      metadata: {},
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
    },
    urgency: "due_soon",
    days_until_due: 2,
  },
  {
    bill: {
      id: "bill-2",
      payee: "Rent",
      amount: "1500.00",
      currency: "USD",
      due_date: "2026-05-01",
      frequency: "monthly",
      status: "overdue",
      payment_method: null,
      account_id: null,
      source_message_id: null,
      statement_period_start: null,
      statement_period_end: null,
      paid_at: null,
      metadata: {},
      created_at: "2026-04-01T00:00:00Z",
      updated_at: "2026-04-01T00:00:00Z",
    },
    urgency: "overdue",
    days_until_due: -9,
  },
];

const MONTHLY_SUMMARY = {
  start_date: "2026-05-01",
  end_date: "2026-05-10",
  currency: "USD",
  total_spend: "1243.60",
  groups: [
    { key: "groceries", amount: "380.00", count: 8 },
    { key: "dining", amount: "210.00", count: 12 },
    { key: "subscriptions", amount: "87.00", count: 4 },
  ],
};

const CATEGORY_SUMMARY = {
  start_date: "2026-04-10",
  end_date: "2026-05-10",
  currency: "USD",
  total_spend: "2800.00",
  groups: [
    { key: "groceries", amount: "760.00", count: 18 },
    { key: "dining", amount: "430.00", count: 24 },
    { key: "subscriptions", amount: "174.00", count: 8 },
  ],
};

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <ButlerFinanceFinancesTab />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Mock setup helpers
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useFinanceTransactions).mockReturnValue({
    data: { data: TRANSACTIONS, meta: { total: 2, offset: 0, limit: 15 } },
    isLoading: false,
  } as ReturnType<typeof useFinanceTransactions>);

  vi.mocked(useFinanceSubscriptions).mockReturnValue({
    data: { data: SUBSCRIPTIONS, meta: { total: 3, offset: 0, limit: 50 } },
    isLoading: false,
  } as ReturnType<typeof useFinanceSubscriptions>);

  vi.mocked(useFinanceUpcomingBills).mockReturnValue({
    data: {
      items: UPCOMING_BILLS,
      total_amount: "1584.00",
      count: 2,
      days_ahead: 30,
      include_overdue: true,
    },
    isLoading: false,
  } as ReturnType<typeof useFinanceUpcomingBills>);

  // useFinanceSpendingSummary is called twice per render: once for monthly KPI,
  // then once for the 30-day category chart. Do not key this off dates: on
  // month-end, the rolling 30-day window can also start on YYYY-MM-01.
  let spendingSummaryCall = 0;
  vi.mocked(useFinanceSpendingSummary).mockImplementation(() => {
    const data = spendingSummaryCall % 2 === 0 ? MONTHLY_SUMMARY : CATEGORY_SUMMARY;
    spendingSummaryCall += 1;
    return {
      data,
      isLoading: false,
    } as ReturnType<typeof useFinanceSpendingSummary>;
  });
}

function setupEmpty() {
  vi.mocked(useFinanceTransactions).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 15 } },
    isLoading: false,
  } as unknown as ReturnType<typeof useFinanceTransactions>);

  vi.mocked(useFinanceSubscriptions).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 50 } },
    isLoading: false,
  } as unknown as ReturnType<typeof useFinanceSubscriptions>);

  vi.mocked(useFinanceUpcomingBills).mockReturnValue({
    data: {
      items: [],
      total_amount: "0",
      count: 0,
      days_ahead: 30,
      include_overdue: true,
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useFinanceUpcomingBills>);

  vi.mocked(useFinanceSpendingSummary).mockReturnValue({
    data: {
      start_date: "2026-05-01",
      end_date: "2026-05-10",
      currency: "USD",
      total_spend: "0",
      groups: [],
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useFinanceSpendingSummary>);
}

function setupLoading() {
  vi.mocked(useFinanceTransactions).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as ReturnType<typeof useFinanceTransactions>);

  vi.mocked(useFinanceSubscriptions).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as ReturnType<typeof useFinanceSubscriptions>);

  vi.mocked(useFinanceUpcomingBills).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as ReturnType<typeof useFinanceUpcomingBills>);

  vi.mocked(useFinanceSpendingSummary).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as ReturnType<typeof useFinanceSpendingSummary>);
}

// ---------------------------------------------------------------------------
// Tests: five sections are rendered
// ---------------------------------------------------------------------------

describe("ButlerFinanceFinancesTab — five sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders the KPI strip section", () => {
    renderTab();
    expect(screen.getByTestId("finance-kpi-strip")).toBeDefined();
  });

  it("renders the transactions section", () => {
    renderTab();
    expect(screen.getByTestId("finance-transactions-section")).toBeDefined();
  });

  it("renders the upcoming bills section", () => {
    renderTab();
    expect(screen.getByTestId("finance-upcoming-bills-section")).toBeDefined();
  });

  it("renders the subscriptions section", () => {
    renderTab();
    expect(screen.getByTestId("finance-subscriptions-section")).toBeDefined();
  });

  it("renders the category spend chart section", () => {
    renderTab();
    expect(screen.getByTestId("finance-category-chart-section")).toBeDefined();
  });

  it("renders the outer finances tab container", () => {
    renderTab();
    expect(screen.getByTestId("finance-finances-tab")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI strip values
// ---------------------------------------------------------------------------

describe("ButlerFinanceFinancesTab — KPI strip", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders four KPI value cells (redesigned 4-col strip)", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    expect(kpiValues.length).toBeGreaterThanOrEqual(4);
  });

  it("renders 'Monthly spend' label", () => {
    renderTab();
    expect(screen.getByText("Monthly spend")).toBeDefined();
  });

  it("renders 'Active subscriptions' label", () => {
    renderTab();
    expect(screen.getByText("Active subscriptions")).toBeDefined();
  });

  it("renders 'Next bill' label", () => {
    renderTab();
    expect(screen.getByText("Next bill")).toBeDefined();
  });

  it("renders 'Top category · 30d' label as the 4th KPI cell", () => {
    renderTab();
    // MonoLabel renders text in DOM; CSS uppercase does not change DOM text content.
    expect(screen.getByText("Top category · 30d")).toBeDefined();
  });

  it("shows top category value when spend data is available", () => {
    renderTab();
    // CATEGORY_SUMMARY has groceries as the top category ($760). The KPI cell
    // sub-label should contain the category name; the value shows the amount.
    const kpiStrip = screen.getByTestId("finance-kpi-strip");
    expect(kpiStrip.textContent).toContain("Groceries");
    // KPI value: $760.00 formatted by Intl.NumberFormat
    expect(kpiStrip.textContent).toContain("$760.00");
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI strip honesty — bu-t5w6w
//   Next bill must skip $0 / amount_known:false placeholders.
//   Active subscriptions must exclude $0 and dummy test subs.
// ---------------------------------------------------------------------------

// A $0 / amount-unknown placeholder bill that sorts FIRST by due_date, plus a
// real bill behind it. The KPI must skip the placeholder and surface the real one.
const NEXT_BILL_PLACEHOLDER = {
  bill: {
    id: "bill-zero",
    payee: "Arta Finance",
    amount: "0.00",
    currency: "USD",
    due_date: "2026-05-09",
    frequency: "monthly",
    status: "pending",
    payment_method: null,
    account_id: null,
    source_message_id: null,
    statement_period_start: null,
    statement_period_end: null,
    paid_at: null,
    metadata: { amount_known: false },
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  },
  urgency: "due_soon",
  days_until_due: 1,
};

const NEXT_BILL_REAL = {
  bill: {
    id: "bill-real",
    payee: "Electric Company",
    amount: "84.00",
    currency: "USD",
    due_date: "2026-05-12",
    frequency: "monthly",
    status: "pending",
    payment_method: null,
    account_id: null,
    source_message_id: null,
    statement_period_start: null,
    statement_period_end: null,
    paid_at: null,
    metadata: {},
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  },
  urgency: "due_soon",
  days_until_due: 4,
};

// Active subs including a $0 placeholder and a literal dummy test record. Only
// the two real active subs (Netflix, Spotify) should be counted.
const SUBS_WITH_NOISE = [
  ...SUBSCRIPTIONS, // Netflix (active), Spotify (active), Adobe (cancelled)
  {
    id: "sub-dummy",
    service: "dummy",
    amount: "0.00",
    currency: "USD",
    frequency: "monthly",
    next_renewal: "2026-06-30",
    status: "active",
    auto_renew: true,
    payment_method: null,
    account_id: null,
    source_message_id: null,
    metadata: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  },
  {
    id: "sub-zero",
    service: "Mystery $0 Sub",
    amount: "0.00",
    currency: "USD",
    frequency: "monthly",
    next_renewal: "2026-06-25",
    status: "active",
    auto_renew: true,
    payment_method: null,
    account_id: null,
    source_message_id: null,
    metadata: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  },
];

function setupKpiNoise() {
  vi.mocked(useFinanceTransactions).mockReturnValue({
    data: { data: TRANSACTIONS, meta: { total: 2, offset: 0, limit: 15 } },
    isLoading: false,
  } as ReturnType<typeof useFinanceTransactions>);

  vi.mocked(useFinanceSubscriptions).mockReturnValue({
    data: { data: SUBS_WITH_NOISE, meta: { total: 5, offset: 0, limit: 50 } },
    isLoading: false,
  } as ReturnType<typeof useFinanceSubscriptions>);

  vi.mocked(useFinanceUpcomingBills).mockReturnValue({
    data: {
      items: [NEXT_BILL_PLACEHOLDER, NEXT_BILL_REAL],
      total_amount: "84.00",
      count: 2,
      days_ahead: 30,
      include_overdue: true,
    },
    isLoading: false,
  } as ReturnType<typeof useFinanceUpcomingBills>);

  let spendingSummaryCall = 0;
  vi.mocked(useFinanceSpendingSummary).mockImplementation(() => {
    const data = spendingSummaryCall % 2 === 0 ? MONTHLY_SUMMARY : CATEGORY_SUMMARY;
    spendingSummaryCall += 1;
    return {
      data,
      isLoading: false,
    } as ReturnType<typeof useFinanceSpendingSummary>;
  });
}

describe("ButlerFinanceFinancesTab — KPI strip honesty (bu-t5w6w)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupKpiNoise();
  });

  afterEach(() => cleanup());

  it("Next bill skips the $0 / amount_known:false placeholder and shows the real bill", () => {
    renderTab();
    const kpiStrip = screen.getByTestId("finance-kpi-strip");
    // Must NOT surface the $0 Arta Finance placeholder as the next bill.
    expect(kpiStrip.textContent).not.toContain("$0.00");
    expect(kpiStrip.textContent).not.toContain("Arta Finance");
    // Must surface the real $84.00 Electric Company bill instead.
    expect(kpiStrip.textContent).toContain("$84.00");
    expect(kpiStrip.textContent).toContain("Electric Company");
  });

  it("Active subscriptions counts only real billable active subs (excludes $0 + dummy)", () => {
    renderTab();
    const cells = screen.getAllByTestId("kpi-value");
    const activeCell = cells.find((c) => c.textContent?.includes("Active subscriptions"));
    expect(activeCell).toBeDefined();
    // Netflix + Spotify are active & non-zero & non-dummy → count = 2.
    // Adobe is cancelled; sub-dummy is service:'dummy'; sub-zero is $0 → all excluded.
    expect(activeCell?.textContent).toContain("2");
    expect(activeCell?.textContent).not.toContain("4");
  });
});

// ---------------------------------------------------------------------------
// Tests: Transaction rows
// ---------------------------------------------------------------------------

describe("ButlerFinanceFinancesTab — transactions table", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders transaction rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("transaction-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });

  it("shows merchant name in a transaction row", () => {
    renderTab();
    // Normalized merchant "Whole Foods Market" should appear
    expect(screen.getByText("Whole Foods Market")).toBeDefined();
  });

  it("renders the transactions table", () => {
    renderTab();
    expect(screen.getByTestId("transactions-table")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Upcoming bills
// ---------------------------------------------------------------------------

describe("ButlerFinanceFinancesTab — upcoming bills", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders upcoming bill items", () => {
    renderTab();
    const items = screen.getAllByTestId("upcoming-bill-item");
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("shows Electric Company in the bills list", () => {
    renderTab();
    // "Electric Company" may appear in the KPI next-bill sub-label and in the bills list;
    // verify it appears at least once inside the bills list container.
    const billsList = screen.getByTestId("upcoming-bills-list");
    expect(billsList.textContent).toContain("Electric Company");
  });

  it("renders the upcoming bills list", () => {
    renderTab();
    expect(screen.getByTestId("upcoming-bills-list")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Subscriptions roster
// ---------------------------------------------------------------------------

describe("ButlerFinanceFinancesTab — subscriptions", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders subscription rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("subscription-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });

  it("shows Spotify in the subscriptions list", () => {
    renderTab();
    // Spotify only appears in subscriptions, not in transactions
    expect(screen.getByText("Spotify")).toBeDefined();
  });

  it("renders the subscriptions list", () => {
    renderTab();
    expect(screen.getByTestId("subscriptions-list")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: category chart renders
// ---------------------------------------------------------------------------

describe("ButlerFinanceFinancesTab — category chart", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders the recharts bar chart when data is present", () => {
    renderTab();
    expect(screen.getByTestId("category-spend-chart")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: empty states
// ---------------------------------------------------------------------------

describe("ButlerFinanceFinancesTab — explicit empty states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });

  afterEach(() => cleanup());

  it("shows empty state for transactions when none exist", () => {
    renderTab();
    expect(screen.queryByTestId("transactions-table")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state for upcoming bills when none exist", () => {
    renderTab();
    expect(screen.queryByTestId("upcoming-bills-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state for subscriptions when none exist", () => {
    renderTab();
    expect(screen.queryByTestId("subscriptions-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state for chart when no groups exist", () => {
    renderTab();
    expect(screen.queryByTestId("category-spend-chart")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: loading state
// ---------------------------------------------------------------------------

describe("ButlerFinanceFinancesTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });

  afterEach(() => cleanup());

  it("shows loading placeholders while queries are pending", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty-state lines while loading", () => {
    renderTab();
    expect(screen.queryByTestId("empty-state-line")).toBeNull();
  });

  it("does not render transactions table while loading", () => {
    renderTab();
    expect(screen.queryByTestId("transactions-table")).toBeNull();
  });

  it("does not render upcoming-bills list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("upcoming-bills-list")).toBeNull();
  });

  it("does not render subscriptions list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("subscriptions-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: getAllTabs includes finance finances tab
// ---------------------------------------------------------------------------

import { getAllTabs, isValidTab } from "@/pages/butler-detail-tabs";

describe("ButlerDetailPage — finance finances tab in getAllTabs", () => {
  it("finance butler has 'finances' tab in operator mode", () => {
    expect(getAllTabs("finance", "operator")).toContain("finances");
  });

  it("finance butler has 'finances' tab in resident mode", () => {
    expect(getAllTabs("finance", "resident")).toContain("finances");
  });

  it("'finances' is a valid tab for finance butler in both modes", () => {
    expect(isValidTab("finances", "finance", "operator")).toBe(true);
    expect(isValidTab("finances", "finance", "resident")).toBe(true);
  });

  it("'finances' is NOT a valid tab for non-finance butlers", () => {
    expect(isValidTab("finances", "general", "operator")).toBe(false);
    expect(isValidTab("finances", "education", "resident")).toBe(false);
  });

  it("non-finance butlers do not include 'finances' tab", () => {
    expect(getAllTabs("general", "operator")).not.toContain("finances");
    expect(getAllTabs("education", "resident")).not.toContain("finances");
  });
});

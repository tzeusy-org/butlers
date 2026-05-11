// @vitest-environment jsdom
/**
 * ButlerMessengerConversationsTab — RTL tests.
 *
 * Tests cover:
 *  - Root container renders
 *  - All 4 panels render (KPI quartet, active channels, recent failures, delivery pipeline)
 *  - KPI rendering: deliveries, success rate, dead-letter count
 *  - Channel rows with various circuit states (closed/open/half_open)
 *  - DB-approximation note displayed when source === 'db_approximation'
 *  - Dead-letter rows with timestamps and error messages
 *  - Empty states for each panel
 *  - Loading state shows skeletons, no empty-state text
 *  - Error banner when any query fails
 *  - isError paths in individual panels (ErrorLine pattern)
 *  - Queue depth by channel + priority
 *
 * bead: bu-iuol4.34
 */

import {
  afterEach,
  beforeAll,
  afterAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerMessengerConversationsTab from "./ButlerMessengerConversationsTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-messenger", () => ({
  useMessengerDeliveryStats: vi.fn(),
  useMessengerCircuitStatus: vi.fn(),
  useMessengerQueueDepth: vi.fn(),
  useMessengerDeadLetters: vi.fn(),
}));

// Stub <Time> to avoid date-formatting complexity
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => (
    <time dateTime={value}>{value}</time>
  ),
}));

import {
  useMessengerDeliveryStats,
  useMessengerCircuitStatus,
  useMessengerQueueDepth,
  useMessengerDeadLetters,
} from "@/hooks/use-messenger";

// ---------------------------------------------------------------------------
// Fixed clock
// ---------------------------------------------------------------------------

const FIXED_NOW_ISO = "2026-05-11T12:00:00.000Z";

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

const NOW = new Date(FIXED_NOW_ISO).getTime();
const H1_AGO = new Date(NOW - 1 * 60 * 60 * 1000).toISOString();
const H2_AGO = new Date(NOW - 2 * 60 * 60 * 1000).toISOString();

const DELIVERY_STATS = {
  window_hours: 24,
  delivered: 120,
  failed: 8,
  pending: 3,
  retried: 4,
  dead_letter: 2,
  dispatched_at: H1_AGO,
};

const CIRCUIT_STATUS_DB_APPROX = {
  channels: [
    {
      name: "telegram",
      state: "closed",
      last_state_change: H2_AGO,
      failure_rate_15m: 0.0,
    },
    {
      name: "email",
      state: "open",
      last_state_change: H1_AGO,
      failure_rate_15m: 1.0,
    },
    {
      name: "sms",
      state: "half_open",
      last_state_change: H1_AGO,
      failure_rate_15m: 0.5,
    },
  ],
  source: "db_approximation",
};

const QUEUE_DEPTH = {
  total: 5,
  by_channel: { telegram: 3, email: 2 },
  by_priority: { normal: 4, high: 1 },
};

const DEAD_LETTERS = {
  letters: [
    {
      id: "dl-00000001-aaaa-bbbb-cccc-ddddeeee0001",
      channel: "telegram",
      recipient_id: "user-123",
      error_message: "Connection timeout",
      attempted_at: H1_AGO,
      retry_count: 3,
    },
    {
      id: "dl-00000002-aaaa-bbbb-cccc-ddddeeee0002",
      channel: "email",
      recipient_id: "user-456",
      error_message: "SMTP auth failed",
      attempted_at: H2_AGO,
      retry_count: 5,
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
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerMessengerConversationsTab />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useMessengerDeliveryStats).mockReturnValue({
    data: DELIVERY_STATS,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerDeliveryStats>);

  vi.mocked(useMessengerCircuitStatus).mockReturnValue({
    data: CIRCUIT_STATUS_DB_APPROX,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerCircuitStatus>);

  vi.mocked(useMessengerQueueDepth).mockReturnValue({
    data: QUEUE_DEPTH,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerQueueDepth>);

  vi.mocked(useMessengerDeadLetters).mockReturnValue({
    data: DEAD_LETTERS,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerDeadLetters>);
}

function setupEmpty() {
  vi.mocked(useMessengerDeliveryStats).mockReturnValue({
    data: {
      window_hours: 24,
      delivered: 0,
      failed: 0,
      pending: 0,
      retried: 0,
      dead_letter: 0,
      dispatched_at: null,
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerDeliveryStats>);

  vi.mocked(useMessengerCircuitStatus).mockReturnValue({
    data: { channels: [], source: "db_approximation" },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerCircuitStatus>);

  vi.mocked(useMessengerQueueDepth).mockReturnValue({
    data: { total: 0, by_channel: {}, by_priority: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerQueueDepth>);

  vi.mocked(useMessengerDeadLetters).mockReturnValue({
    data: { letters: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerDeadLetters>);
}

function setupLoading() {
  vi.mocked(useMessengerDeliveryStats).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerDeliveryStats>);

  vi.mocked(useMessengerCircuitStatus).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerCircuitStatus>);

  vi.mocked(useMessengerQueueDepth).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerQueueDepth>);

  vi.mocked(useMessengerDeadLetters).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useMessengerDeadLetters>);
}

function setupError() {
  vi.mocked(useMessengerDeliveryStats).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useMessengerDeliveryStats>);

  vi.mocked(useMessengerCircuitStatus).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useMessengerCircuitStatus>);

  vi.mocked(useMessengerQueueDepth).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useMessengerQueueDepth>);

  vi.mocked(useMessengerDeadLetters).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useMessengerDeadLetters>);
}

// ---------------------------------------------------------------------------
// Tests: Root container + panel presence
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — all panels present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the root tab container", () => {
    renderTab();
    expect(screen.getByTestId("messenger-conversations-tab")).toBeDefined();
  });

  it("renders the KPI quartet panel", () => {
    renderTab();
    expect(screen.getByTestId("kpi-quartet")).toBeDefined();
  });

  it("renders the active channels card", () => {
    renderTab();
    expect(screen.getByTestId("active-channels-card")).toBeDefined();
  });

  it("renders the recent failures card", () => {
    renderTab();
    expect(screen.getByTestId("recent-failures-card")).toBeDefined();
  });

  it("renders the delivery pipeline card", () => {
    renderTab();
    expect(screen.getByTestId("delivery-pipeline-card")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI rendering
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — KPI rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders 4 KPI items in the quartet", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items.length).toBe(4);
  });

  it("shows delivered count from delivery stats", () => {
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[0].textContent).toContain("120");
  });

  it("shows success rate percentage", () => {
    renderTab();
    // 120 delivered / (120 + 8 failed) = 93.75% → rounded to 94%
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[1].textContent).toContain("94%");
  });

  it("shows dead-letter count", () => {
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[2].textContent).toContain("2");
  });

  it("shows dash for avg latency (not tracked)", () => {
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[3].textContent).toContain("—");
  });
});

// ---------------------------------------------------------------------------
// Tests: Channel circuit states
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — channel circuit states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders channel list", () => {
    renderTab();
    expect(screen.getByTestId("channel-list")).toBeDefined();
  });

  it("renders 3 channel rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("channel-row");
    expect(rows.length).toBe(3);
  });

  it("renders a 'closed' badge for telegram channel", () => {
    renderTab();
    const badges = screen.getAllByTestId("circuit-state-badge");
    // telegram is first
    expect(badges[0].textContent).toBe("closed");
  });

  it("renders an 'open' badge for email channel", () => {
    renderTab();
    const badges = screen.getAllByTestId("circuit-state-badge");
    expect(badges[1].textContent).toBe("open");
  });

  it("renders a 'half open' badge for sms channel", () => {
    renderTab();
    const badges = screen.getAllByTestId("circuit-state-badge");
    expect(badges[2].textContent).toBe("half open");
  });

  it("shows DB-approximation note when source is db_approximation", () => {
    renderTab();
    expect(screen.getByTestId("db-approximation-note")).toBeDefined();
  });

  it("shows channel names in the list", () => {
    renderTab();
    const list = screen.getByTestId("channel-list");
    expect(list.textContent).toContain("telegram");
    expect(list.textContent).toContain("email");
    expect(list.textContent).toContain("sms");
  });
});

// ---------------------------------------------------------------------------
// Tests: No DB-approximation note when source is not db_approximation
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — non-approximation source", () => {
  afterEach(() => cleanup());

  it("does not show DB-approximation note when source is live", () => {
    vi.resetAllMocks();
    vi.mocked(useMessengerDeliveryStats).mockReturnValue({
      data: DELIVERY_STATS,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessengerDeliveryStats>);
    vi.mocked(useMessengerCircuitStatus).mockReturnValue({
      data: { ...CIRCUIT_STATUS_DB_APPROX, source: "live" },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessengerCircuitStatus>);
    vi.mocked(useMessengerQueueDepth).mockReturnValue({
      data: QUEUE_DEPTH,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessengerQueueDepth>);
    vi.mocked(useMessengerDeadLetters).mockReturnValue({
      data: DEAD_LETTERS,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessengerDeadLetters>);
    renderTab();
    expect(screen.queryByTestId("db-approximation-note")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Dead-letter rows with timestamps
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — dead-letter rows", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders dead-letter list", () => {
    renderTab();
    expect(screen.getByTestId("dead-letter-list")).toBeDefined();
  });

  it("renders 2 dead-letter rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("dead-letter-row");
    expect(rows.length).toBe(2);
  });

  it("shows error message for the first dead letter", () => {
    renderTab();
    const list = screen.getByTestId("dead-letter-list");
    expect(list.textContent).toContain("Connection timeout");
  });

  it("shows error message for the second dead letter", () => {
    renderTab();
    const list = screen.getByTestId("dead-letter-list");
    expect(list.textContent).toContain("SMTP auth failed");
  });

  it("shows timestamp for dead letters using <Time>", () => {
    renderTab();
    const timeTags = document.querySelectorAll("time");
    expect(timeTags.length).toBeGreaterThanOrEqual(1);
  });

  it("shows retry count in dead-letter rows", () => {
    renderTab();
    const list = screen.getByTestId("dead-letter-list");
    expect(list.textContent).toContain("3 attempts");
    expect(list.textContent).toContain("5 attempts");
  });
});

// ---------------------------------------------------------------------------
// Tests: Queue depth (delivery pipeline panel)
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — queue depth", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the pipeline panel", () => {
    renderTab();
    expect(screen.getByTestId("pipeline-panel")).toBeDefined();
  });

  it("shows total queue depth", () => {
    renderTab();
    expect(screen.getByTestId("pipeline-total").textContent).toContain("5");
  });

  it("renders queue by channel", () => {
    renderTab();
    const byChannel = screen.getByTestId("queue-by-channel");
    expect(byChannel.textContent).toContain("telegram");
    expect(byChannel.textContent).toContain("email");
  });

  it("renders queue by priority", () => {
    renderTab();
    const byPriority = screen.getByTestId("queue-by-priority");
    expect(byPriority.textContent).toContain("normal");
    expect(byPriority.textContent).toContain("high");
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty state
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty state for channels", () => {
    renderTab();
    expect(screen.getByText("No channel activity in the last 15 min.")).toBeDefined();
  });

  it("shows empty state for dead letters", () => {
    renderTab();
    expect(screen.getByText("No dead letters.")).toBeDefined();
  });

  it("shows success rate dash when no deliveries", () => {
    renderTab();
    // 0 delivered + 0 failed = null success rate → "—"
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[1].textContent).toContain("—");
  });

  it("shows queue empty text in pipeline panel when no queue data", () => {
    renderTab();
    expect(screen.getByText("Queue empty.")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading skeletons", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty-state text while loading", () => {
    renderTab();
    expect(screen.queryByText("No channel activity in the last 15 min.")).toBeNull();
    expect(screen.queryByText("No dead letters.")).toBeNull();
  });

  it("does not show error banner while loading", () => {
    renderTab();
    expect(screen.queryByTestId("messenger-load-error")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: isError paths
// ---------------------------------------------------------------------------

describe("ButlerMessengerConversationsTab — isError paths", () => {
  afterEach(() => cleanup());

  it("shows error banner when all queries fail", () => {
    vi.resetAllMocks();
    setupError();
    renderTab();
    expect(screen.getByTestId("messenger-load-error")).toBeDefined();
  });

  it("shows error lines in panels when queries fail", () => {
    vi.resetAllMocks();
    setupError();
    renderTab();
    const errorLines = screen.getAllByTestId("error-state-line");
    // At least delivery stats + circuit status + dead-letters error lines
    expect(errorLines.length).toBeGreaterThanOrEqual(3);
  });

  it("shows only circuit error when only circuit status fails", () => {
    vi.resetAllMocks();
    vi.mocked(useMessengerDeliveryStats).mockReturnValue({
      data: DELIVERY_STATS,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessengerDeliveryStats>);
    vi.mocked(useMessengerCircuitStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useMessengerCircuitStatus>);
    vi.mocked(useMessengerQueueDepth).mockReturnValue({
      data: QUEUE_DEPTH,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessengerQueueDepth>);
    vi.mocked(useMessengerDeadLetters).mockReturnValue({
      data: DEAD_LETTERS,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessengerDeadLetters>);
    renderTab();
    // Error banner shows (any error triggers it)
    expect(screen.getByTestId("messenger-load-error")).toBeDefined();
    // Only the circuit panel shows ErrorLine
    const errorLines = screen.getAllByTestId("error-state-line");
    expect(errorLines.length).toBe(1);
  });
});

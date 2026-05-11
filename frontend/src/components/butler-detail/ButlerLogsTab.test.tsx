// @vitest-environment jsdom
/**
 * ButlerLogsTab — RTL unit tests.
 *
 * Tests cover:
 *  - Root container renders
 *  - Filter chips render for all levels (ALL / DEBUG / INFO / WARN / ERROR)
 *  - Clicking a filter chip marks it active and changes the active chip
 *  - Log line list renders with ts / level / msg columns
 *  - Level colours: DEBUG=muted, INFO=primary, WARN=amber, ERROR=destructive
 *  - Empty state when no log lines returned
 *  - Loading skeletons during initial load; no list or empty state shown
 *  - Error banner shown when the query errors
 *  - Auto-scroll toggle renders and is pressable
 *  - Empty butler (different name) — still renders without error
 *  - Large list: 200 lines render correctly (no trimming in component layer)
 *
 * Virtualization note: v1 renders all received lines directly; the backend
 * is responsible for trimming to the requested `limit` (default 200).
 * We document this here rather than implementing client-side trimming.
 *
 * bead: bu-iuol4.17
 */

import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerLogsTab from "./ButlerLogsTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-logs", () => ({
  useButlerLogs: vi.fn(),
}));

// Stub <Time> to avoid date-formatting complexity in unit tests
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => (
    <time dateTime={value}>{value}</time>
  ),
}));

import { useButlerLogs } from "@/hooks/use-butler-logs";

// ---------------------------------------------------------------------------
// Fixed clock
// ---------------------------------------------------------------------------

const FIXED_NOW_ISO = "2026-05-11T10:30:00.000Z";

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

const LOG_LINE_DEBUG = {
  ts: "2026-05-11T10:29:58.123Z",
  level: "DEBUG",
  msg: "Loading config from disk",
  source: "butler.core",
  request_id: null,
  metadata: null,
};

const LOG_LINE_INFO = {
  ts: "2026-05-11T10:29:59.456Z",
  level: "INFO",
  msg: "Session started",
  source: "butler.session",
  request_id: "req-abc-123",
  metadata: null,
};

const LOG_LINE_WARN = {
  ts: "2026-05-11T10:30:00.000Z",
  level: "WARN",
  msg: "Slow response detected",
  source: "butler.health",
  request_id: null,
  metadata: { latency_ms: 2500 },
};

const LOG_LINE_ERROR = {
  ts: "2026-05-11T10:30:00.789Z",
  level: "ERROR",
  msg: "Unhandled exception in tick handler",
  source: "butler.tick",
  request_id: null,
  metadata: null,
};

const ALL_LINES = [LOG_LINE_DEBUG, LOG_LINE_INFO, LOG_LINE_WARN, LOG_LINE_ERROR];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab(butlerName = "test-butler") {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <ButlerLogsTab butlerName={butlerName} />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithData(lines = ALL_LINES) {
  vi.mocked(useButlerLogs).mockReturnValue({
    data: { lines },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerLogs>);
}

function setupEmpty() {
  vi.mocked(useButlerLogs).mockReturnValue({
    data: { lines: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerLogs>);
}

function setupLoading() {
  vi.mocked(useButlerLogs).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useButlerLogs>);
}

function setupError() {
  vi.mocked(useButlerLogs).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useButlerLogs>);
}

// ---------------------------------------------------------------------------
// Tests: Root container
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — root", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the root tab container", () => {
    renderTab();
    expect(screen.getByTestId("butler-logs-tab")).toBeDefined();
  });

  it("renders the filter chips row", () => {
    renderTab();
    expect(screen.getByTestId("filter-chips")).toBeDefined();
  });

  it("renders five filter chips (All, Info, Debug, Warn, Error)", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    expect(chips.length).toBe(5);
    const labels = chips.map((c) => c.textContent ?? "");
    expect(labels).toContain("All");
    expect(labels).toContain("Debug");
    expect(labels).toContain("Info");
    expect(labels).toContain("Warn");
    expect(labels).toContain("Error");
  });

  it("'All' chip is active by default", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const allChip = chips.find((c) => c.textContent === "All");
    expect(allChip?.getAttribute("aria-pressed")).toBe("true");
  });
});

// ---------------------------------------------------------------------------
// Tests: Filter chip interaction
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — filter chips", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("clicking 'Info' chip marks it active and deactivates 'All'", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const infoChip = chips.find((c) => c.textContent === "Info")!;
    const allChip = chips.find((c) => c.textContent === "All")!;

    fireEvent.click(infoChip);

    expect(infoChip.getAttribute("aria-pressed")).toBe("true");
    expect(allChip.getAttribute("aria-pressed")).toBe("false");
  });

  it("clicking 'Debug' chip marks it active", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const debugChip = chips.find((c) => c.textContent === "Debug")!;
    fireEvent.click(debugChip);
    expect(debugChip.getAttribute("aria-pressed")).toBe("true");
  });

  it("clicking 'Warn' chip marks it active", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const warnChip = chips.find((c) => c.textContent === "Warn")!;
    fireEvent.click(warnChip);
    expect(warnChip.getAttribute("aria-pressed")).toBe("true");
  });

  it("clicking 'Error' chip marks it active", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const errorChip = chips.find((c) => c.textContent === "Error")!;
    fireEvent.click(errorChip);
    expect(errorChip.getAttribute("aria-pressed")).toBe("true");
  });

  it("only one chip is active at a time", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const warnChip = chips.find((c) => c.textContent === "Warn")!;
    fireEvent.click(warnChip);

    const active = chips.filter((c) => c.getAttribute("aria-pressed") === "true");
    expect(active.length).toBe(1);
  });

  it("'All' chip passes level=undefined to useButlerLogs (returns all levels)", () => {
    renderTab();
    // Default state — All is active. Hook should be called without a level filter.
    const calls = vi.mocked(useButlerLogs).mock.calls;
    const lastCall = calls[calls.length - 1];
    expect(lastCall[1]?.level).toBeUndefined();
  });

  it("clicking 'Info' chip passes level='INFO' to useButlerLogs", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const infoChip = chips.find((c) => c.textContent === "Info")!;
    fireEvent.click(infoChip);

    const calls = vi.mocked(useButlerLogs).mock.calls;
    const lastCall = calls[calls.length - 1];
    expect(lastCall[1]?.level).toBe("INFO");
  });

  it("clicking 'Error' chip passes level='ERROR' to useButlerLogs", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const errorChip = chips.find((c) => c.textContent === "Error")!;
    fireEvent.click(errorChip);

    const calls = vi.mocked(useButlerLogs).mock.calls;
    const lastCall = calls[calls.length - 1];
    expect(lastCall[1]?.level).toBe("ERROR");
  });

  it("switching back to 'All' passes level=undefined to useButlerLogs", () => {
    renderTab();
    const chips = screen.getAllByTestId("filter-chip");
    const warnChip = chips.find((c) => c.textContent === "Warn")!;
    const allChip = chips.find((c) => c.textContent === "All")!;

    fireEvent.click(warnChip);
    fireEvent.click(allChip);

    const calls = vi.mocked(useButlerLogs).mock.calls;
    const lastCall = calls[calls.length - 1];
    expect(lastCall[1]?.level).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Log line list rendering
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — log line list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the log line list", () => {
    renderTab();
    expect(screen.getByTestId("log-line-list")).toBeDefined();
  });

  it("renders a row for each log line", () => {
    renderTab();
    const rows = screen.getAllByTestId("log-line-row");
    expect(rows.length).toBe(ALL_LINES.length);
  });

  it("renders timestamps", () => {
    renderTab();
    const tsCells = screen.getAllByTestId("log-ts");
    expect(tsCells.length).toBeGreaterThanOrEqual(1);
  });

  it("renders level cells", () => {
    renderTab();
    const levelCells = screen.getAllByTestId("log-level");
    const texts = levelCells.map((c) => c.textContent ?? "");
    expect(texts).toContain("DEBUG");
    expect(texts).toContain("INFO");
    expect(texts).toContain("WARN");
    expect(texts).toContain("ERROR");
  });

  it("renders message content", () => {
    renderTab();
    expect(screen.getByText("Session started")).toBeDefined();
    expect(screen.getByText("Slow response detected")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Level tone classes
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — level tone classes", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("DEBUG level cell has muted class", () => {
    renderTab();
    const levelCells = screen.getAllByTestId("log-level");
    const debugCell = levelCells.find((c) => c.textContent === "DEBUG");
    expect(debugCell?.className).toContain("text-muted-foreground");
  });

  it("INFO level cell has primary class", () => {
    renderTab();
    const levelCells = screen.getAllByTestId("log-level");
    const infoCell = levelCells.find((c) => c.textContent === "INFO");
    expect(infoCell?.className).toContain("text-primary");
  });

  it("WARN level cell has amber class", () => {
    renderTab();
    const levelCells = screen.getAllByTestId("log-level");
    const warnCell = levelCells.find((c) => c.textContent === "WARN");
    expect(warnCell?.className).toContain("text-amber-500");
  });

  it("ERROR level cell has destructive class", () => {
    renderTab();
    const levelCells = screen.getAllByTestId("log-level");
    const errorCell = levelCells.find((c) => c.textContent === "ERROR");
    expect(errorCell?.className).toContain("text-destructive");
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty state
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows 'No logs yet.' when there are no lines", () => {
    renderTab();
    expect(screen.getByTestId("empty-state-line")).toBeDefined();
    expect(screen.getByText("No logs yet.")).toBeDefined();
  });

  it("does not render the log line list when empty", () => {
    renderTab();
    expect(screen.queryByTestId("log-line-list")).toBeNull();
  });

  it("renders for an 'empty butler' (different name)", () => {
    renderTab("empty-butler");
    expect(screen.getByTestId("butler-logs-tab")).toBeDefined();
    expect(screen.getByText("No logs yet.")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading skeletons during initial load", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show the log line list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("log-line-list")).toBeNull();
  });

  it("does not show empty-state text while loading", () => {
    renderTab();
    expect(screen.queryByTestId("empty-state-line")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Error state
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — error state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupError();
  });
  afterEach(() => cleanup());

  it("shows an error banner when the query fails", () => {
    renderTab();
    expect(screen.getByTestId("logs-load-error")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Auto-scroll toggle
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — auto-scroll toggle", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the auto-scroll toggle", () => {
    renderTab();
    expect(screen.getByTestId("auto-scroll-toggle")).toBeDefined();
  });

  it("auto-scroll is 'on' by default", () => {
    renderTab();
    const toggle = screen.getByTestId("auto-scroll-toggle");
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(toggle.textContent).toBe("on");
  });

  it("clicking the toggle pauses auto-scroll", () => {
    renderTab();
    const toggle = screen.getByTestId("auto-scroll-toggle");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(toggle.textContent).toBe("paused");
  });

  it("clicking again re-enables auto-scroll", () => {
    renderTab();
    const toggle = screen.getByTestId("auto-scroll-toggle");
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(toggle.textContent).toBe("on");
  });
});

// ---------------------------------------------------------------------------
// Tests: Large list (v1 no-virtualization contract)
// ---------------------------------------------------------------------------

describe("ButlerLogsTab — large list rendering", () => {
  afterEach(() => cleanup());

  it("renders 200 log lines without trimming (v1: backend is responsible for limit)", () => {
    vi.resetAllMocks();
    const largeList = Array.from({ length: 200 }, (_, i) => ({
      ts: `2026-05-11T10:00:00.${String(i).padStart(3, "0")}Z`,
      level: (["DEBUG", "INFO", "WARN", "ERROR"] as const)[i % 4],
      msg: `Log line ${i}`,
      source: null,
      request_id: null,
      metadata: null,
    }));
    vi.mocked(useButlerLogs).mockReturnValue({
      data: { lines: largeList },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerLogs>);

    renderTab();
    const rows = screen.getAllByTestId("log-line-row");
    // All 200 lines are rendered; client-side trimming is not applied in v1.
    expect(rows.length).toBe(200);
  });
});

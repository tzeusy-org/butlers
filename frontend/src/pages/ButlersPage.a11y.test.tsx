/**
 * ButlersPage — axe-core accessibility test, run against the REAL routed
 * page (bu-86c4c.16).
 *
 * The previous version of this file (bu-hb7dh.8) ran axe against hand-
 * written stub components that merely "mirrored" ButlersPage's DOM shape —
 * a change to the real StatusBoardCell/BoardHeader/BoardFooter/NeedsYouStrip
 * markup could regress accessibility without this suite ever noticing
 * (JARVIS audit move 11, critical finding: "the a11y gate is theater").
 *
 * This version mocks only the data layer (useButlerStatusBoard) — following
 * the same vi.mock pattern already used by TimelineTab.test.tsx — and
 * drives the actual `<ButlersPage />` component through each of its five
 * real states (loading / empty / error / populated / quarantined-restore).
 *
 * Colour-contrast is disabled because jsdom cannot compute computed styles;
 * that gap is covered separately by src/lib/contrast.test.ts, which checks
 * the real oklch token literals via pure math (no DOM needed).
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { axe, toHaveNoViolations } from "jest-axe";

expect.extend(toHaveNoViolations);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Mock the data hooks — same pattern as TimelineTab.test.tsx. ButlersPage
// itself, StatusBoardCell, BoardHeader, BoardFooter, and NeedsYouStrip all
// render for real.
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-status-board", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-butler-status-board")>();
  return {
    ...actual,
    useButlerStatusBoard: vi.fn(),
  };
});

vi.mock("@/hooks/use-general", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-general")>();
  return {
    ...actual,
    useSetEligibility: vi.fn(() => ({
      mutate: vi.fn(),
      isPending: false,
      variables: undefined,
    })),
  };
});

import ButlersPage from "./ButlersPage";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import type { StatusBoardAggregates, StatusBoardRow } from "@/hooks/use-butler-status-board";

const mockUseButlerStatusBoard = vi.mocked(useButlerStatusBoard);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeRow(overrides: Partial<StatusBoardRow> = {}): StatusBoardRow {
  return {
    name: "general",
    type: "butler",
    description: "Default household assistant",
    status: "ok",
    activity: "idle",
    cellTone: "neutral",
    eligibility: "active",
    quarantineReason: null,
    quarantinedAt: null,
    sessions24h: 3,
    costToday: 0.42,
    loadPct: 10,
    activeSessionCount: 0,
    lastRunISO: "2026-07-04T09:00:00Z",
    lastHeartbeatISO: "2026-07-04T09:55:00Z",
    heartbeatAgeSeconds: 60,
    hourlyStripe: Array.from({ length: 24 }, () => 0),
    hourlyTotal: 3,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    cadenceSeconds: 3600,
    cadenceLabel: "hourly",
    silenceSeconds: 60,
    cadenceStatus: "on_schedule",
    ...overrides,
  };
}

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 0,
    butlerCount: 0,
    stafferCount: 0,
    active: 0,
    offline: 0,
    quarantined: 0,
    overdue: 0,
    totalSessions24h: 0,
    totalSpendToday: 0,
    avgLoadPct: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    heartbeatSourceError: false,
    registrySourceError: false,
    eligibilityUnavailable: 0,
    hasPerEntryErrors: false,
    costSourceError: false,
    sessionsSourceError: false,
    sourcesPartiallyDegraded: false,
    ...overrides,
    unknown: overrides.unknown ?? 0,
  };
}

async function checkA11y(): Promise<void> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlersPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const results = await axe(container, {
    rules: {
      "color-contrast": { enabled: false },
    },
  });
  expect(results).toHaveNoViolations();
}

// ---------------------------------------------------------------------------
// Story 1: Loading state
// ---------------------------------------------------------------------------

describe("a11y (real page): Loading state", () => {
  it("has zero axe violations", async () => {
    mockUseButlerStatusBoard.mockReturnValue({
      rows: [],
      needsYou: [],
      aggregates: makeAggregates({ isLoading: true }),
    });
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 2: Empty state (no rows, no error)
// ---------------------------------------------------------------------------

describe("a11y (real page): Empty state", () => {
  it("has zero axe violations", async () => {
    mockUseButlerStatusBoard.mockReturnValue({
      rows: [],
      needsYou: [],
      aggregates: makeAggregates(),
    });
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 3: Error state (full-page, no cached rows)
// ---------------------------------------------------------------------------

describe("a11y (real page): Error state", () => {
  it("has zero axe violations", async () => {
    mockUseButlerStatusBoard.mockReturnValue({
      rows: [],
      needsYou: [],
      aggregates: makeAggregates({
        isError: true,
        error: new Error("Failed to fetch butler list."),
      }),
    });
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 4: Populated (header banner, grid group, cells as links, footer)
// ---------------------------------------------------------------------------

describe("a11y (real page): Populated state", () => {
  it("has zero axe violations", async () => {
    const rows = [
      makeRow({ name: "general", activity: "idle" }),
      makeRow({ name: "health", activity: "running" }),
      makeRow({ name: "finance", activity: "idle" }),
    ];
    mockUseButlerStatusBoard.mockReturnValue({
      rows,
      needsYou: [],
      aggregates: makeAggregates({
        total: 3,
        butlerCount: 3,
        active: 1,
        totalSessions24h: 15,
      }),
    });
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 5: Quarantined cell (restore button inside div[role=link])
// ---------------------------------------------------------------------------

describe("a11y (real page): Quarantined cell (restore chip)", () => {
  it("has zero axe violations", async () => {
    const rows = [
      makeRow({
        name: "quarant",
        activity: "quarantined",
        eligibility: "quarantined",
        quarantineReason: "Repeated tool failures",
      }),
      makeRow({ name: "general", activity: "idle" }),
    ];
    mockUseButlerStatusBoard.mockReturnValue({
      rows,
      needsYou: rows.filter((r) => r.activity === "quarantined"),
      aggregates: makeAggregates({ total: 2, butlerCount: 2, quarantined: 1 }),
    });
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 6: Needs-you rows remain real keyboard-reachable route doors
// ---------------------------------------------------------------------------

describe("a11y (real page): Needs-you route doors", () => {
  it("keeps an attention row reachable by keyboard as a native detail link", async () => {
    const row = makeRow({
      name: "down-butler",
      activity: "offline",
      cellTone: "red",
      status: "down",
    });
    mockUseButlerStatusBoard.mockReturnValue({
      rows: [row],
      needsYou: [row],
      aggregates: makeAggregates({ total: 1, butlerCount: 1, offline: 1 }),
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ButlersPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const needsYou = screen.getByRole("group", { name: "Needs your attention" });
    const link = within(needsYou).getByRole("link", { name: /down-butler/i });
    const user = userEvent.setup();
    for (let tabCount = 0; tabCount < 20 && document.activeElement !== link; tabCount += 1) {
      await user.tab();
    }

    expect(document.activeElement).toBe(link);
    expect(link.getAttribute("href")).toBe("/butlers/down-butler");
  });
});

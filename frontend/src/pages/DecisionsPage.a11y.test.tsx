/**
 * DecisionsPage — axe-core accessibility test, run against the REAL routed
 * page (bu-ckkpz.2, following ButlersPage.a11y.test.tsx's pattern: mock only
 * the data hook, drive the actual `<DecisionsPage />` through its real
 * states).
 *
 * Colour-contrast is disabled because jsdom cannot compute computed styles
 * (see ButlersPage.a11y.test.tsx's identical note; covered separately by
 * src/lib/contrast.test.ts).
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { axe, toHaveNoViolations } from "jest-axe";

expect.extend(toHaveNoViolations);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

vi.mock("@/hooks/use-decisions", () => ({ useDecisions: vi.fn() }));

import DecisionsPage from "./DecisionsPage";
import { useDecisions } from "@/hooks/use-decisions";
import type { DecisionBeadSummary, DecisionsListResponse } from "@/api/index.ts";

const mockUseDecisions = vi.mocked(useDecisions);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function decision(overrides: Partial<DecisionBeadSummary> = {}): DecisionBeadSummary {
  return {
    id: "bu-v4ipc",
    title: "DECISION REQUIRED (owner): connector identity",
    priority: 1,
    created_at: "2026-07-01T00:00:00Z",
    age_hours: 240,
    description: null,
    options: null,
    default: null,
    due_at: null,
    structured_details_available: false,
    structured_details_unavailable_reason: null,
    escalated: false,
    ...overrides,
  };
}

function mockDecisions(
  rows: DecisionBeadSummary[] | undefined,
  meta: DecisionsListResponse["meta"] = { decisions_available: true },
  overrides: Partial<AnyMock> = {},
) {
  mockUseDecisions.mockReturnValue({
    data: rows === undefined ? undefined : { data: rows, meta },
    isLoading: rows === undefined,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as AnyMock);
}

async function checkA11y(initialEntry = "/decisions"): Promise<void> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <DecisionsPage />
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

describe("a11y (real page): Loading state", () => {
  it("has zero axe violations", async () => {
    mockDecisions(undefined);
    await checkA11y();
  });
});

describe("a11y (real page): Genuine empty (all-clear)", () => {
  it("has zero axe violations", async () => {
    mockDecisions([]);
    await checkA11y();
  });
});

describe("a11y (real page): Degraded (beads export unreachable)", () => {
  it("has zero axe violations", async () => {
    mockDecisions([], { decisions_available: false, unavailable_reason: "export_missing" });
    await checkA11y();
  });
});

describe("a11y (real page): Populated with an escalated row", () => {
  it("has zero axe violations", async () => {
    mockDecisions([
      decision({ id: "bu-a", age_hours: 240 }),
      decision({
        id: "bu-b",
        age_hours: 30,
        escalated: true,
        escalated_blocked_id: "bu-wzbu9",
        escalated_blocked_title: "Silent message loss",
        escalated_blocked_kind: "p1_bug",
        escalated_block_hours: 72,
      }),
    ]);
    await checkA11y();
  });
});

describe("a11y (real page): Structured decision context", () => {
  it("has zero axe violations for a known deep link with ordered options", async () => {
    mockDecisions([
      decision({
        id: "bu-context",
        description: "Choose the safest recovery posture.",
        options: ["Keep paused", "Resume safely"],
        default: "Keep paused",
        due_at: "2026-07-20T12:00:00Z",
        structured_details_available: true,
        structured_details_unavailable_reason: null,
      }),
    ]);

    await checkA11y("/decisions?bead=bu-context");
  });

  it("has zero axe violations for malformed source metadata", async () => {
    mockDecisions([
      decision({
        id: "bu-malformed",
        description: "The source deadline remains visible.",
        options: null,
        default: null,
        due_at: "2026-07-20T12:00:00Z",
        structured_details_available: false,
        structured_details_unavailable_reason: "metadata_malformed",
      }),
    ]);

    await checkA11y("/decisions?bead=bu-malformed");
  });
});

describe("a11y (real page): Query error", () => {
  it("has zero axe violations", async () => {
    mockDecisions(undefined, undefined, {
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
    });
    await checkA11y();
  });
});

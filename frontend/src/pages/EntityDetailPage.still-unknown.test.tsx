// @vitest-environment jsdom
/**
 * The entity detail "Still unknown" section (bu-q7vx1q.71): the owner's open questions
 * about this entity, absent when there are none, with the degraded and error states named.
 * The setup mirrors the supplementary-section degraded-state suite.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage from "@/pages/EntityDetailPage";
import { useEntity } from "@/hooks/use-memory";
import { useDismissMemoryGap, useMemoryGaps } from "@/hooks/use-memory-gaps";
import type { MemoryGap } from "@/api/memory-gaps.ts";
import { useEntityActivity } from "@/hooks/use-entities";
import type { EntityDetail } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ entityId: "entity-001" })),
    useSearchParams: vi.fn(() => [new URLSearchParams("mode=editorial"), vi.fn()]),
  };
});

vi.mock("@/hooks/use-memory", () => ({
  useEntity: vi.fn(),
  useUpdateEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePromoteEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useForgetRelationshipEntity: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useCreateEntityInfo: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useUpdateEntityInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useDeleteEntityInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useRevealEntitySecret: vi.fn(() => ({ mutate: vi.fn() })),
}));

vi.mock("@/hooks/use-entities", () => ({
  ENTITY_CADENCE_REFRESH_MS: 30_000,
  ENTITY_CADENCE_MAX_AGE_MS: 90_000,
  // EntityDetailPage renders EntityVerbRail (bu-6t8ix.4); its four write verbs each
  // call a mutation hook from this module. Inert here: these suites submit nothing,
  // the hooks only need to exist and report an idle state. Declared inline because a
  // vi.mock factory is hoisted above any module-level const it might otherwise share.
  useEntityReachOutDrafts: vi.fn(() => ({
    data: [],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  })),
  useCreateEntityNote: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    error: null,
  })),
  useCreateEntityInteraction: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    error: null,
  })),
  useCreateEntityGift: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    error: null,
  })),
  useCreateEntityReachOutDraft: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    error: null,
  })),
  useEntityTimeline: vi.fn(() => ({ data: [], isLoading: false, isError: false })),
  useEntityActivity: vi.fn(() => ({ data: { pages: [{ items: [], total: 0, limit: 50, offset: 0, degraded: false, degraded_reason: null }], pageParams: [0] }, isLoading: false, isError: false, isRefetching: false, refetch: vi.fn(), fetchNextPage: vi.fn(), hasNextPage: false, isFetchingNextPage: false })),
  useEntityCadence: vi.fn(() => ({ data: { window_days: 30, interaction_count: 0, completeness: "complete", has_more: false }, isLoading: false, isError: false })),
  useEntityGifts: vi.fn(() => ({ data: [], isLoading: false, isError: false })),
  useEntityLoans: vi.fn(() => ({ data: [], isLoading: false, isError: false })),
  useEntityMessageThreads: vi.fn(() => ({ data: [], isLoading: false, isError: false })),
  useEntityLinkedContacts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityDates: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityActivityBins: vi.fn(() => ({ data: { bins: [] }, isLoading: false, isError: false })),
  useEntityDeltaFacts: vi.fn(() => ({ data: { marked_at: null, items: [] }, isSuccess: true })),
  useMarkEntityView: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityCoreDates: vi.fn(() => ({ data: { items: [] }, isLoading: false, isError: false })),
  useUpdateEntityDunbarTier: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityNeighbours: vi.fn(() => ({ data: { neighbours: {}, remainders: {} }, isError: false })),
  useRelationshipEntities: vi.fn(() => ({ data: { items: [], total: 0, limit: 200, offset: 0 } })),
  useRelationshipEntitiesByIds: vi.fn(() => ({ data: { items: [], total: 0, limit: 1, offset: 0 } })),
  useArchiveRelationshipEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityFacts: vi.fn(() => ({
    data: { items: [], next_cursor: null, has_more: false },
    isFetching: false,
    isError: false,
    error: null,
  })),
  useRelationshipEntityQueue: vi.fn(() => ({ data: { items: [], total: 0, limit: 100, offset: 0 } })),
  useCompareEntities: vi.fn(() => ({ mutateAsync: vi.fn(), reset: vi.fn(), isPending: false })),
  useDismissEntityPair: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useMergeRelationshipEntities: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));


vi.mock("@/hooks/use-memory-gaps", () => ({
  useMemoryGaps: vi.fn(),
  useDismissMemoryGap: vi.fn(),
}));

vi.mock("@/components/relationship/OwnerSetupBanner", () => ({
  OwnerSetupBanner: () => null,
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const ENTITY: EntityDetail = {
  id: "entity-001",
  canonical_name: "Test Owner",
  entity_type: "person",
  aliases: [],
  roles: ["owner"],
  fact_count: 0,
  unidentified: false,
  source_butler: null,
  source_scope: null,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
  dunbar_tier: null,
  dunbar_score: null,
  archived: false,
  metadata: {},
  recent_facts: [],
  recent_facts_total: 0,
  recent_facts_offset: 0,
  recent_facts_limit: 20,
  recent_facts_has_more: false,
  entity_info: [],
};

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <EntityDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function setMode(mode: "editorial" | "workbench") {
  vi.mocked(useSearchParams).mockReturnValue([
    new URLSearchParams(`mode=${mode}`),
    vi.fn(),
  ]);
}

beforeEach(() => {
  setMode("editorial");
  vi.mocked(useEntity).mockReturnValue({
    data: { data: ENTITY },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useEntity>);
  vi.mocked(useEntityActivity).mockReturnValue({
    data: { pages: [{ items: [], total: 0, limit: 200, offset: 0, degraded: false, degraded_reason: null }], pageParams: [0] },
    isLoading: false, isError: false, isRefetching: false, refetch: vi.fn(),
    fetchNextPage: vi.fn(), hasNextPage: false, isFetchingNextPage: false,
  } as unknown as ReturnType<typeof useEntityActivity>);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeGap(overrides: Partial<MemoryGap> = {}): MemoryGap {
  return {
    gap_id: "gap-1",
    butler: "relationship",
    entity_id: "entity-001",
    entity_name: "Test Owner",
    predicate: "favorite_coffee",
    question: "What does Test Owner drink in the morning?",
    status: "open",
    coverage_state: "unknown",
    asked_at: "2026-06-12T08:00:00Z",
    expires_at: "2026-09-10T08:00:00Z",
    answered_by_ref: null,
    delivery_failed: false,
    last_error: null,
    origins: [],
    ...overrides,
  };
}

function setGaps(
  gaps: MemoryGap[],
  extra: { degraded?: string[]; isError?: boolean; isPending?: boolean } = {},
) {
  vi.mocked(useMemoryGaps).mockReturnValue({
    data: extra.isError
      ? undefined
      : {
          pages: [
            {
              data: gaps,
              meta: {
                limit: 50,
                next_cursor: null,
                has_more: false,
                sources_degraded: extra.degraded,
              },
            },
          ],
        },
    isError: extra.isError ?? false,
    isPending: extra.isPending ?? false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useMemoryGaps>);
}

describe("EntityDetailPage Still unknown section", () => {
  beforeEach(() => {
    vi.mocked(useDismissMemoryGap).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useDismissMemoryGap>);
  });

  it("lists this entity's open questions with predicate, coverage words and asked date", () => {
    setGaps([
      makeGap(),
      makeGap({
        gap_id: "gap-2",
        question: "Where does Test Owner live?",
        predicate: "lives_in",
        coverage_state: "absent_proven",
      }),
    ]);

    renderPage();

    expect(vi.mocked(useMemoryGaps)).toHaveBeenCalledWith({ entityId: "entity-001" });
    const section = screen.getByTestId("entity-still-unknown");
    expect(section.textContent).toContain("Still unknown");
    const rows = screen.getAllByTestId("still-unknown-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("What does Test Owner drink in the morning?");
    expect(rows[0].textContent).toContain("favorite_coffee");
    expect(rows[0].textContent).toContain("never looked");
    expect(rows[0].textContent).toContain("Jun 12, 2026");
    expect(rows[1].textContent).toContain("looked, nothing there");
  });

  it("reads an answerable gap as answered, notice pending", () => {
    setGaps([makeGap({ status: "answerable", coverage_state: "present" })]);

    renderPage();

    expect(screen.getByTestId("still-unknown-row").textContent).toContain(
      "answered, notice pending",
    );
  });

  it("is absent altogether when the entity has no open questions", () => {
    setGaps([]);

    renderPage();

    expect(screen.queryByTestId("entity-still-unknown")).toBeNull();
    expect(screen.queryByTestId("entity-still-unknown-error")).toBeNull();
    expect(screen.queryByText("Still unknown")).toBeNull();
  });

  it("stays absent while the first page is loading", () => {
    setGaps([], { isPending: true });

    renderPage();

    expect(screen.queryByTestId("entity-still-unknown")).toBeNull();
  });

  it("names a pool that could not be read instead of reading as nothing unknown", () => {
    setGaps([makeGap()], { degraded: ["finance"] });

    renderPage();

    const degraded = screen.getByTestId("entity-still-unknown-degraded");
    expect(degraded.textContent).toContain("Some butlers could not be read");
    expect(degraded.textContent).toContain("finance");
    expect(screen.getAllByTestId("still-unknown-row")).toHaveLength(1);
  });

  it("shows the degraded line even when the only pool that answered had no rows", () => {
    setGaps([], { degraded: ["finance"] });

    renderPage();

    expect(screen.getByTestId("entity-still-unknown-degraded")).toBeTruthy();
  });

  it("renders an error note, never a vanished section, when the fetch fails", () => {
    setGaps([], { isError: true });

    renderPage();

    expect(screen.getByTestId("entity-still-unknown-error")).toBeTruthy();
    expect(screen.queryByTestId("entity-still-unknown")).toBeNull();
  });

  it("is not part of the workbench layout", () => {
    setMode("workbench");
    setGaps([makeGap()]);

    renderPage();

    expect(screen.queryByTestId("entity-still-unknown")).toBeNull();
  });
});

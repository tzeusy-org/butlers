// @vitest-environment jsdom
/**
 * Dossier panel on EntityDetailPage (bu-8cdl1.8, REQ-entity-graph-011).
 *
 * Mounted on the real page with the same hook mocks as the degraded-sections sweep.  The
 * dossier API is the only thing mocked for the panel, so this proves the page renders it and
 * that its outcome never takes down the activity surface.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage from "@/pages/EntityDetailPage";
import { useEntity } from "@/hooks/use-memory";
import { useEntityActivity } from "@/hooks/use-entities";
import type { EntityDetail } from "@/api/types";

const mockGetEntityDossier = vi.fn();

vi.mock("@/api/entity-dossier.ts", () => ({
  getEntityDossier: (...args: unknown[]) => mockGetEntityDossier(...args),
}));

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
  rebind_receipts: [],
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
  mockGetEntityDossier.mockReset();
});

// REQ-entity-graph-011: page integration against a mocked dossier API.
describe("EntityDetailPage graph dossier panel", () => {
  it("renders the dossier receipts and counts beside the activity timeline", async () => {
    mockGetEntityDossier.mockResolvedValue({
      data: {
        entity_id: "entity-001",
        relationships_known: 1,
        relationships_withheld: 2,
        receipts_limit: 100,
        receipts_truncated: false,
        sources: [
          {
            source_schema: "relationship",
            source_table: "entity_facts",
            live_count: 1,
            receipts: [
              {
                edge_id: "edge-1",
                source_id: "src-1",
                predicate: "friend_of",
                direction: "subject",
                counterpart_entity_id: "entity-002",
                counterpart_name: "Alex Friend",
                sensitivity: "normal",
                updated_at: "2026-10-01T00:00:00Z",
              },
            ],
          },
        ],
      },
      meta: {},
    });

    renderPage();

    await waitFor(() => expect(screen.getByTestId("entity-dossier-coverage")).toBeTruthy());
    expect(screen.getByTestId("entity-dossier-coverage").textContent).toBe(
      "1 relationship known, 2 withheld for sensitivity",
    );
    expect(screen.getByRole("link", { name: "Alex Friend" })).toBeTruthy();
    expect(mockGetEntityDossier).toHaveBeenCalledWith("entity-001");
  });

  it("keeps the activity surface when the dossier is unavailable", async () => {
    mockGetEntityDossier.mockRejectedValue(new Error("503"));

    renderPage();

    await waitFor(() => expect(screen.getByTestId("entity-dossier-error")).toBeTruthy());
    expect(screen.queryByTestId("entity-dossier-empty")).toBeNull();
    expect(screen.queryByTestId("entity-activity-error")).toBeNull();
    expect(screen.getByRole("heading", { name: "Activity" })).toBeTruthy();
  });
});

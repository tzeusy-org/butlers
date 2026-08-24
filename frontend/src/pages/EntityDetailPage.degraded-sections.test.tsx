// @vitest-environment jsdom
/**
 * Supplementary-section degraded-state sweep (bu-hckjv).
 *
 * EntityDetailPage composes ~a dozen supplementary sections, each reading from
 * its own query. Historically an errored query fell through to the SAME nothing
 * as a genuinely empty success — a vanished section (gifts/loans/threads) or a
 * calm "No relations yet." painted over an outage. This pins the fix: on
 * isError each section renders an honest inline degraded note (SourceDegradedNote,
 * additive `entity-*-error` / `workbench-*-error` testids); a genuine empty
 * success still collapses silently (no false alarm).
 *
 * Both variants are covered: "collapse-to-null" panels (Gifts, Message threads)
 * and a "visible empty-state" section (Workbench top relations).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage from "@/pages/EntityDetailPage";
import { useEntity } from "@/hooks/use-memory";
import {
  useEntityGifts,
  useEntityMessageThreads,
  useEntityNeighbours,
} from "@/hooks/use-entities";
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
  useSetLinkedContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUnlinkContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-entities", () => ({
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

vi.mock("@/hooks/use-contacts", () => ({
  useContacts: vi.fn(() => ({ data: { contacts: [] }, isError: false })),
  useCreateContactInfo: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
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
  linked_contact_id: null,
  linked_contact_name: null,
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
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EntityDetailPage — supplementary section degraded states", () => {
  it("GiftsPanel: renders a degraded note on error instead of vanishing", () => {
    vi.mocked(useEntityGifts).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityGifts>);

    renderPage();

    expect(screen.getByTestId("entity-gifts-error")).toBeTruthy();
  });

  it("GiftsPanel: shows no degraded note on a genuine empty success", () => {
    vi.mocked(useEntityGifts).mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityGifts>);

    renderPage();

    expect(screen.queryByTestId("entity-gifts-error")).toBeNull();
  });

  it("MessageThreadsSection: renders a degraded note on error instead of vanishing", () => {
    vi.mocked(useEntityMessageThreads).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityMessageThreads>);

    renderPage();

    expect(screen.getByTestId("entity-message-threads-error")).toBeTruthy();
  });

  it("MessageThreadsSection: shows no degraded note on a genuine empty success", () => {
    vi.mocked(useEntityMessageThreads).mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityMessageThreads>);

    renderPage();

    expect(screen.queryByTestId("entity-message-threads-error")).toBeNull();
  });

  it("Workbench top relations: shows a degraded note on error, not 'No relations yet.'", () => {
    setMode("workbench");
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: undefined,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage();

    expect(screen.getByTestId("workbench-top-relations-error")).toBeTruthy();
    expect(screen.queryByText("No relations yet.")).toBeNull();
  });

  it("Workbench top relations: shows the empty placeholder on a genuine empty success", () => {
    setMode("workbench");
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: { neighbours: {}, remainders: {} },
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage();

    expect(screen.queryByTestId("workbench-top-relations-error")).toBeNull();
    expect(screen.getByText("No relations yet.")).toBeTruthy();
  });
});

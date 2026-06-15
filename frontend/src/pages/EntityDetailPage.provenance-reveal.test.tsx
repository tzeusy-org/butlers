// @vitest-environment jsdom
/**
 * Editorial provenance on-demand reveal (entity v3, bu-19u8r).
 *
 * Spec scenario "Editorial reveals provenance on demand":
 * - the default fact-row chrome carries no provenance clutter;
 * - activating a row's affordance reveals `src`, `verified`, and the staleness
 *   band (the two distinct axes — confidence and staleness — never blended).
 *
 * Renders the full page in editorial mode with one editorial `recent_facts` row
 * and a matching facts-drill `EntityFact` carrying real provenance.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage from "@/pages/EntityDetailPage";
import { useEntity } from "@/hooks/use-memory";
import { useEntityFacts } from "@/hooks/use-entities";
import type { EntityDetail, EntityFact, Fact } from "@/api/types";

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
  useEntityTimeline: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityGifts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLoans: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityMessageThreads: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLinkedContacts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityDates: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityActivityBins: vi.fn(() => ({ data: { bins: [] }, isLoading: false, isError: false })),
  useEntityDeltaFacts: vi.fn(() => ({ data: { marked_at: null, items: [] }, isSuccess: true })),
  useMarkEntityView: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityCoreDates: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
  useUpdateEntityDunbarTier: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityNeighbours: vi.fn(() => ({ data: { neighbours: {}, remainders: {} } })),
  useRelationshipEntities: vi.fn(() => ({ data: { items: [], total: 0, limit: 200, offset: 0 } })),
  useRelationshipEntitiesByIds: vi.fn(() => ({ data: { items: [], total: 0, limit: 1, offset: 0 } })),
  useArchiveRelationshipEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityFacts: vi.fn(),
  useRelationshipEntityQueue: vi.fn(() => ({ data: { items: [], total: 0, limit: 100, offset: 0 } })),
  useCompareEntities: vi.fn(() => ({ mutateAsync: vi.fn(), reset: vi.fn(), isPending: false })),
  useDismissEntityPair: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useMergeRelationshipEntities: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useContacts: vi.fn(() => ({ data: { contacts: [] } })),
  useCreateContactInfo: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/components/relationship/OwnerSetupBanner", () => ({
  OwnerSetupBanner: () => null,
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const FACT: Fact = {
  id: "fact-1",
  subject: "user",
  predicate: "works_at",
  content: "Globex",
  importance: 5,
  confidence: 0.9,
  decay_rate: 0.008,
  permanence: "standard",
  source_butler: "general",
  source_episode_id: null,
  session_id: null,
  supersedes_id: null,
  entity_id: "entity-001",
  entity_name: "Test Owner",
  object_entity_id: null,
  object_entity_name: null,
  validity: "active",
  scope: "global",
  reference_count: 1,
  created_at: "2025-01-01T12:34:56Z",
  last_referenced_at: null,
  last_confirmed_at: null,
  tags: [],
  metadata: {},
};

const PROVENANCE: EntityFact = {
  id: "ef-1",
  subject: "Test Owner",
  predicate: "works_at",
  object: "Globex",
  object_kind: "literal",
  src: "relationship",
  conf: 1.0,
  weight: 3,
  last_observed_at: "2020-01-01T00:00:00Z",
  verified: true,
  primary: true,
  validity: "active",
  created_at: "2020-01-01T00:00:00Z",
  store: "identity",
  staleness_band: "stale",
};

const ENTITY: EntityDetail = {
  id: "entity-001",
  canonical_name: "Test Owner",
  entity_type: "person",
  aliases: [],
  roles: ["owner"],
  fact_count: 1,
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
  recent_facts: [FACT],
  recent_facts_total: 1,
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

beforeEach(() => {
  vi.mocked(useSearchParams).mockReturnValue([
    new URLSearchParams("mode=editorial"),
    vi.fn(),
  ]);
  vi.mocked(useEntity).mockReturnValue({
    data: { data: ENTITY },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useEntity>);
  vi.mocked(useEntityFacts).mockReturnValue({
    data: { items: [PROVENANCE], next_cursor: null, has_more: false },
    isFetching: false,
    error: null,
  } as unknown as ReturnType<typeof useEntityFacts>);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EntityDetailPage — editorial provenance reveal", () => {
  it("keeps provenance hidden until the affordance is activated", () => {
    renderPage();

    // Default chrome: the reveal block is absent.
    expect(screen.queryByTestId("fact-provenance-reveal-fact-1")).toBeNull();

    // The affordance is present.
    const toggle = screen.getByTestId("fact-provenance-toggle-fact-1");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);

    const reveal = screen.getByTestId("fact-provenance-reveal-fact-1");
    const text = reveal.textContent ?? "";
    // src + verified + the staleness band are revealed.
    expect(text).toContain("relationship");
    expect(reveal.querySelector('[data-verified="true"]')).not.toBeNull();
    expect(reveal.querySelector('[data-staleness="stale"]')).not.toBeNull();
    // Confidence and staleness render as two separate axes — a full conf bar AND
    // a stale band on the same row, never a blended score.
    expect(reveal.querySelector('[role="meter"]')).not.toBeNull();
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("offers no affordance when the fact has no drill provenance", () => {
    vi.mocked(useEntityFacts).mockReturnValue({
      data: { items: [], next_cursor: null, has_more: false },
      isFetching: false,
      error: null,
    } as unknown as ReturnType<typeof useEntityFacts>);
    renderPage();

    expect(screen.queryByTestId("fact-provenance-toggle-fact-1")).toBeNull();
  });
});

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage, { ENTITY_MODE_STORAGE_KEY } from "@/pages/EntityDetailPage";
import { useEntity } from "@/hooks/use-memory";
import type { EntityDetail } from "@/api/types";

// Mock react-router's useParams and useSearchParams so we can control both
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ entityId: "entity-001" })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
  };
});

// ---------------------------------------------------------------------------
// localStorage mock
// ---------------------------------------------------------------------------
// renderToStaticMarkup runs in Node (no real DOM), so we shim localStorage.
// The readPersistedEntityMode() helper catches access errors and falls back
// to "editorial", but having a controllable mock lets us assert mode paths.

const localStorageMock = (() => {
  let store: Record<string, string | null> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => { store[key] = value; }),
    removeItem: vi.fn((key: string) => { delete store[key]; }),
    clear: vi.fn(() => { store = {}; }),
  };
})();

Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
  writable: true,
});

// Mock all hooks used by EntityDetailPage — we only care about useEntity here
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

// Relationship-scoped hooks consumed by the consolidated page
vi.mock("@/hooks/use-entities", () => ({
  useEntityTimeline: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityGifts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLoans: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityMessageThreads: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLinkedContacts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityDates: vi.fn(() => ({ data: [], isLoading: false })),
  useUpdateEntityDunbarTier: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useContacts: vi.fn(() => ({ data: { contacts: [] } })),
}));

vi.mock("@/components/relationship/OwnerSetupBanner", () => ({
  OwnerSetupBanner: () => null,
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

type UseEntityResult = ReturnType<typeof useEntity>;

const BASE_ENTITY: EntityDetail = {
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

function setEntityState(entity: EntityDetail | null, opts: Partial<UseEntityResult> = {}) {
  vi.mocked(useEntity).mockReturnValue({
    data: entity ? { data: entity } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseEntityResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <EntityDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("EntityDetailPage — identity hero", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders the canonical name and Dunbar pulse tile", () => {
    setEntityState({ ...BASE_ENTITY, canonical_name: "Alice Example" });
    const html = renderPage();
    expect(html).toContain("Alice Example");
    // Pulse strip is always shown
    expect(html).toContain("Dunbar tier");
    expect(html).toContain("Last interaction");
  });

  it("renders the activity section heading", () => {
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Activity");
  });
});

describe("EntityDetailPage — credentials moved to /secrets", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // Credentials & Info management has moved to the User tab of /secrets.
  // The entity page only carries a link to that surface.
  it("renders a link to /secrets in the practical drawer for owners with no linked contact", () => {
    setEntityState({
      ...BASE_ENTITY,
      roles: ["owner"],
      linked_contact_id: null,
      entity_info: [],
    });

    const html = renderPage();

    expect(html).toContain("/secrets");
    expect(html).toContain("Secrets");
  });

  it("does not render the legacy Credentials & Info section", () => {
    setEntityState({
      ...BASE_ENTITY,
      roles: ["owner"],
      linked_contact_id: null,
      entity_info: [
        {
          id: "info-1",
          type: "telegram",
          value: "@ownerhandle",
          label: null,
          is_primary: true,
          secured: false,
        },
      ],
    });

    const html = renderPage();

    // The on-page credentials list is gone — value is no longer rendered here.
    expect(html).not.toContain("@ownerhandle");
    // And the old "Credentials & Info" card title is gone too.
    expect(html).not.toContain("Credentials &amp; Info");
  });
});

describe("EntityDetailPage — facts section", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders fact content with a session link and a load-more control", () => {
    setEntityState({
      ...BASE_ENTITY,
      fact_count: 2,
      recent_facts_total: 2,
      recent_facts_limit: 1,
      recent_facts_has_more: true,
      recent_facts: [
        {
          id: "fact-1",
          subject: "user",
          predicate: "prefers",
          content: "coffee",
          importance: 5,
          confidence: 0.9,
          decay_rate: 0.008,
          permanence: "standard",
          source_butler: "general",
          source_episode_id: "episode-1",
          session_id: "2e513477-a432-4d68-952b-b95226df0aa1",
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
        },
      ],
    });

    const html = renderPage();

    expect(html).toContain("Facts");
    expect(html).toContain("coffee");
    expect(html).toContain("/sessions/2e513477-a432-4d68-952b-b95226df0aa1?butler=general");
    expect(html).toContain("Load more facts");
    expect(html).toContain("1 of 2");
  });
});

// ---------------------------------------------------------------------------
// Mode toggle — Editorial / Workbench
// ---------------------------------------------------------------------------

describe("EntityDetailPage — Editorial/Workbench mode toggle", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Clear the localStorage mock store between tests
    localStorageMock.clear();
    // Reset to default (no URL mode param)
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
  });

  it("defaults to editorial mode when localStorage is empty", () => {
    localStorageMock.getItem.mockReturnValue(null);
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    // Mode toggle button renders the current mode label
    expect(html).toContain("Editorial");
    // data-testid attribute is present
    expect(html).toContain('data-testid="entity-mode-toggle"');
  });

  it("reads workbench mode from localStorage", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "workbench" : null,
    );
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Workbench");
  });

  it("reads editorial mode from localStorage", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "editorial" : null,
    );
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Editorial");
  });

  it("falls back to editorial when localStorage has an invalid value", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "not-a-valid-mode" : null,
    );
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Editorial");
    expect(html).not.toContain("not-a-valid-mode");
  });

  it("URL mode=workbench param overrides localStorage editorial", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "editorial" : null,
    );
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("mode=workbench"),
      vi.fn(),
    ]);
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Workbench");
  });

  it("URL mode=editorial param overrides localStorage workbench", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "workbench" : null,
    );
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("mode=editorial"),
      vi.fn(),
    ]);
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Editorial");
  });

  it("renders activity section in editorial mode", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "editorial" : null,
    );
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Activity");
    expect(html).not.toContain('data-testid="provenance-grid"');
  });

  it("renders provenance grid in workbench mode instead of activity section", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "workbench" : null,
    );
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain('data-testid="provenance-grid"');
    expect(html).not.toContain("Activity");
  });
});

// ---------------------------------------------------------------------------
// Entity gloss — Editorial mode renders gloss; Workbench does not
// ---------------------------------------------------------------------------

describe("EntityDetailPage — entity gloss", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
  });

  function setMode(mode: "editorial" | "workbench") {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? mode : null,
    );
  }

  it("renders the gloss in editorial mode for a healthy person at tier 5", () => {
    setMode("editorial");
    setEntityState({
      ...BASE_ENTITY,
      dunbar_tier: 5,
      unidentified: false,
      entity_type: "person",
    });
    const html = renderPage();
    expect(html).toContain("data-testid=\"entity-gloss\"");
    // Base gloss for tier 5 / healthy
    expect(html).toContain("Support clique");
  });

  it("renders the category override gloss in editorial mode for a healthy organization at tier 5", () => {
    setMode("editorial");
    setEntityState({
      ...BASE_ENTITY,
      dunbar_tier: 5,
      unidentified: false,
      entity_type: "organization",
    });
    const html = renderPage();
    expect(html).toContain("data-testid=\"entity-gloss\"");
    // Override gloss for 5:healthy:organization
    expect(html).toContain("Core institutional relationship");
  });

  it("renders the unidentified gloss in editorial mode when entity.unidentified is true", () => {
    setMode("editorial");
    setEntityState({
      ...BASE_ENTITY,
      dunbar_tier: 150,
      unidentified: true,
      entity_type: "person",
    });
    const html = renderPage();
    expect(html).toContain("data-testid=\"entity-gloss\"");
    // Base gloss for tier 150 / unidentified
    expect(html).toContain("Meaningful-tier candidate");
  });

  it("does NOT render the gloss in workbench mode", () => {
    setMode("workbench");
    setEntityState({
      ...BASE_ENTITY,
      dunbar_tier: 5,
      unidentified: false,
      entity_type: "person",
    });
    const html = renderPage();
    expect(html).not.toContain("data-testid=\"entity-gloss\"");
    expect(html).not.toContain("Support clique");
  });

  it("skips the gloss when dunbar_tier is null (no tier assigned)", () => {
    setMode("editorial");
    setEntityState({
      ...BASE_ENTITY,
      dunbar_tier: null,
      unidentified: false,
      entity_type: "person",
    });
    const html = renderPage();
    expect(html).not.toContain("data-testid=\"entity-gloss\"");
  });

  it("skips the gloss when entity_type is not a recognized EntityType", () => {
    setMode("editorial");
    setEntityState({
      ...BASE_ENTITY,
      dunbar_tier: 50,
      unidentified: false,
      entity_type: "unknown-type",
    });
    const html = renderPage();
    expect(html).not.toContain("data-testid=\"entity-gloss\"");
  });

  it("renders the gloss in editorial mode for a healthy place at tier 15", () => {
    setMode("editorial");
    setEntityState({
      ...BASE_ENTITY,
      dunbar_tier: 15,
      unidentified: false,
      entity_type: "place",
    });
    const html = renderPage();
    expect(html).toContain("data-testid=\"entity-gloss\"");
    // Override gloss for 15:healthy:place
    expect(html).toContain("Frequently visited");
  });
});

// ---------------------------------------------------------------------------
// Workbench mode — ProvenanceGrid
// ---------------------------------------------------------------------------

const SAMPLE_FACT = {
  id: "fact-wb-1",
  subject: "entity-001",
  predicate: "works_at",
  content: "Acme Corp",
  importance: 7.5,
  confidence: 0.9,
  decay_rate: 0.008,
  permanence: "standard",
  source_butler: "general",
  source_episode_id: null,
  session_id: "sess-abc",
  supersedes_id: null,
  entity_id: "entity-001",
  entity_name: "Test Owner",
  object_entity_id: null,
  object_entity_name: null,
  validity: "active",
  scope: "global",
  reference_count: 1,
  created_at: "2025-03-10T08:00:00Z",
  last_referenced_at: null,
  last_confirmed_at: null,
  tags: [],
  metadata: {},
};

describe("EntityDetailPage — ProvenanceGrid (Workbench mode)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
  });

  function setMode(mode: "editorial" | "workbench") {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? mode : null,
    );
  }

  it("Workbench mode renders the provenance grid section", () => {
    setMode("workbench");
    setEntityState({
      ...BASE_ENTITY,
      recent_facts: [SAMPLE_FACT],
      recent_facts_total: 1,
    });
    const html = renderPage();
    expect(html).toContain('data-testid="provenance-grid"');
    expect(html).toContain("Provenance");
  });

  it("Editorial mode does NOT render the provenance grid", () => {
    setMode("editorial");
    setEntityState({
      ...BASE_ENTITY,
      recent_facts: [SAMPLE_FACT],
      recent_facts_total: 1,
    });
    const html = renderPage();
    expect(html).not.toContain('data-testid="provenance-grid"');
    expect(html).not.toContain("Provenance");
  });

  it("Workbench renders fact predicate and content in the grid", () => {
    setMode("workbench");
    setEntityState({
      ...BASE_ENTITY,
      recent_facts: [SAMPLE_FACT],
      recent_facts_total: 1,
    });
    const html = renderPage();
    // predicate displayed (underscores replaced with spaces)
    expect(html).toContain("works at");
    // object content
    expect(html).toContain("Acme Corp");
    // source butler
    expect(html).toContain("general");
  });

  it("Workbench renders grid column headers (Predicate, Importance, Recorded)", () => {
    setMode("workbench");
    setEntityState({
      ...BASE_ENTITY,
      recent_facts: [SAMPLE_FACT],
      recent_facts_total: 1,
    });
    const html = renderPage();
    expect(html).toContain("Predicate");
    expect(html).toContain("Importance");
    expect(html).toContain("Recorded");
  });

  it("Workbench shows empty state when no facts exist", () => {
    setMode("workbench");
    setEntityState({
      ...BASE_ENTITY,
      recent_facts: [],
      recent_facts_total: 0,
    });
    const html = renderPage();
    expect(html).toContain('data-testid="provenance-grid"');
    expect(html).toContain("No facts linked to this entity.");
  });

  it("Workbench grid renders sort buttons for Predicate, Importance, and Recorded columns", () => {
    setMode("workbench");
    setEntityState({
      ...BASE_ENTITY,
      recent_facts: [SAMPLE_FACT],
      recent_facts_total: 1,
    });
    const html = renderPage();
    // Sort buttons are clickable — verify aria-sort attributes are present
    expect(html).toContain('aria-sort="none"');
    // The active sort column has ascending/descending
    expect(html).toMatch(/aria-sort="(ascending|descending)"/);
  });

  it("Workbench shows load-more button when hasMore is true", () => {
    setMode("workbench");
    setEntityState({
      ...BASE_ENTITY,
      recent_facts: [SAMPLE_FACT],
      recent_facts_total: 50,
      recent_facts_has_more: true,
    });
    const html = renderPage();
    expect(html).toContain("Load more facts");
  });
});

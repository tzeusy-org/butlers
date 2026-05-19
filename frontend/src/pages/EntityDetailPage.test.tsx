import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage from "@/pages/EntityDetailPage";
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

const ENTITY_MODE_KEY = "entities.detail.mode";

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
      key === ENTITY_MODE_KEY ? "workbench" : null,
    );
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Workbench");
  });

  it("reads editorial mode from localStorage", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_KEY ? "editorial" : null,
    );
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Editorial");
  });

  it("falls back to editorial when localStorage has an invalid value", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_KEY ? "not-a-valid-mode" : null,
    );
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Editorial");
    expect(html).not.toContain("not-a-valid-mode");
  });

  it("URL mode=workbench param overrides localStorage editorial", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_KEY ? "editorial" : null,
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
      key === ENTITY_MODE_KEY ? "workbench" : null,
    );
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("mode=editorial"),
      vi.fn(),
    ]);
    setEntityState(BASE_ENTITY);
    const html = renderPage();
    expect(html).toContain("Editorial");
  });

  it("renders existing content in both modes (activity section preserved)", () => {
    // Editorial mode
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_KEY ? "editorial" : null,
    );
    setEntityState(BASE_ENTITY);
    const editorialHtml = renderPage();
    expect(editorialHtml).toContain("Activity");

    // Workbench mode
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_KEY ? "workbench" : null,
    );
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
    setEntityState(BASE_ENTITY);
    const workbenchHtml = renderPage();
    expect(workbenchHtml).toContain("Activity");
  });
});

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage from "@/pages/EntityDetailPage";
import { useEntity } from "@/hooks/use-memory";
import type { EntityDetail } from "@/api/types";

// Mock react-router's useParams so we can control the entityId
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: vi.fn(() => ({ entityId: "entity-001" })) };
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

describe("EntityDetailPage — relationship activity link", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders the relationship activity link for entity_type='person'", () => {
    setEntityState({ ...BASE_ENTITY, entity_type: "person" });
    const html = renderPage();
    expect(html).toContain("View relationship activity");
    expect(html).toContain("/butlers/relationship/entities/entity-001");
  });

  it("does not render the relationship activity link for entity_type='organization'", () => {
    setEntityState({ ...BASE_ENTITY, entity_type: "organization" });
    const html = renderPage();
    expect(html).not.toContain("View relationship activity");
  });

  it("does not render the relationship activity link for entity_type='place'", () => {
    setEntityState({ ...BASE_ENTITY, entity_type: "place" });
    const html = renderPage();
    expect(html).not.toContain("View relationship activity");
  });

  it("does not render the relationship activity link for entity_type='other'", () => {
    setEntityState({ ...BASE_ENTITY, entity_type: "other" });
    const html = renderPage();
    expect(html).not.toContain("View relationship activity");
  });
});

describe("EntityDetailPage — google_oauth_refresh visibility", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("hides google_oauth_refresh entries for owner entities", () => {
    setEntityState({
      ...BASE_ENTITY,
      roles: ["owner"],
      entity_info: [
        {
          id: "info-1",
          type: "google_oauth_refresh",
          value: null,
          label: null,
          is_primary: false,
          secured: true,
        },
        {
          id: "info-2",
          type: "telegram",
          value: "@ownerhandle",
          label: null,
          is_primary: true,
          secured: false,
        },
      ],
    });

    const html = renderPage();

    // google_oauth_refresh row should not appear
    expect(html).not.toContain("Google OAuth Refresh");
    // non-filtered entries still appear
    expect(html).toContain("@ownerhandle");
  });

  it("shows google_oauth_refresh entries for non-owner entities", () => {
    setEntityState({
      ...BASE_ENTITY,
      roles: [],
      entity_info: [
        {
          id: "info-1",
          type: "google_oauth_refresh",
          value: null,
          label: null,
          is_primary: false,
          secured: true,
        },
      ],
    });

    const html = renderPage();

    // The label for the type should appear in the row
    expect(html).toContain("Google OAuth Refresh");
  });

  it("shows settings link note for owner entities", () => {
    setEntityState({ ...BASE_ENTITY, roles: ["owner"], entity_info: [] });

    const html = renderPage();

    // Note directing to /settings should be present
    expect(html).toContain("/settings");
    expect(html).toContain("Google OAuth");
  });

  it("does not show settings link note for non-owner entities", () => {
    setEntityState({ ...BASE_ENTITY, roles: [], entity_info: [] });

    const html = renderPage();

    // The Google OAuth note should not appear for plain entities
    expect(html).not.toContain("Google OAuth tokens are managed on companion");
  });

  it("shows fact provenance columns, session link, and load-more affordance", () => {
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

    expect(html).toContain("Source Butler");
    expect(html).toContain("Session");
    expect(html).toContain("general");
    expect(html).toContain("/sessions/2e513477-a432-4d68-952b-b95226df0aa1?butler=general");
    expect(html).toContain("Load more");
    expect(html).toContain("Showing 1 of 2");
  });
});

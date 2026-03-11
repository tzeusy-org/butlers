import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import EntityDetailPage from "@/pages/EntityDetailPage";
import { useEntity } from "@/hooks/use-memory";

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

const BASE_ENTITY = {
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
  metadata: {},
  recent_facts: [],
  entity_info: [],
};

function setEntityState(entity: typeof BASE_ENTITY | null, opts: Partial<UseEntityResult> = {}) {
  vi.mocked(useEntity).mockReturnValue({
    data: entity ? { data: entity } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseEntityResult);
}

function renderPage(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <EntityDetailPage />
    </MemoryRouter>,
  );
}

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
});

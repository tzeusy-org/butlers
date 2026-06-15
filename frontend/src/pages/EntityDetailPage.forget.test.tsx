// @vitest-environment jsdom
/**
 * EntityDetailPage — "Forget this entity" affordance tests.
 *
 * Covers:
 *  - Forget button renders in both Editorial and Workbench modes (Page.actions slot).
 *  - Confirmation dialog opens on button click.
 *  - Cancelling the dialog keeps the entity (API not called).
 *  - Confirming calls the API and navigates to /entities.
 *  - API error is displayed in the dialog; dialog remains open.
 *
 * Bead: bu-iny1e
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage, { ENTITY_MODE_STORAGE_KEY } from "@/pages/EntityDetailPage";
import { useEntity, useForgetRelationshipEntity } from "@/hooks/use-memory";
import type { EntityDetail } from "@/api/types";

// ---------------------------------------------------------------------------
// Router mocks
// ---------------------------------------------------------------------------

const mockNavigate = vi.fn();

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ entityId: "entity-abc" })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
    useNavigate: vi.fn(() => mockNavigate),
  };
});

// ---------------------------------------------------------------------------
// localStorage mock
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Hook mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-memory", () => ({
  useEntity: vi.fn(),
  useUpdateEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePromoteEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useForgetRelationshipEntity: vi.fn(),
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
  useRelationshipEntities: vi.fn(() => ({
    data: { items: [], total: 0, limit: 200, offset: 0 },
  })),
  useRelationshipEntitiesByIds: vi.fn(() => ({
    data: { items: [], total: 0, limit: 1, offset: 0 },
  })),
  useArchiveRelationshipEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityFacts: vi.fn(() => ({
    data: { facts: [], total: 0, offset: 0, limit: 20, has_more: false },
    isFetching: false,
    error: null,
  })),
  useRelationshipEntityQueue: vi.fn(() => ({ data: { items: [], total: 0, limit: 100, offset: 0 } })),
  useCompareEntities: vi.fn(() => ({ mutateAsync: vi.fn(), reset: vi.fn(), isPending: false })),
  useDismissEntityPair: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useMergeRelationshipEntities: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useContacts: vi.fn(() => ({ data: { contacts: [] } })),
}));

vi.mock("@/components/relationship/OwnerSetupBanner", () => ({
  OwnerSetupBanner: () => null,
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type UseEntityResult = ReturnType<typeof useEntity>;

const BASE_ENTITY: EntityDetail = {
  id: "entity-abc",
  canonical_name: "Jane Doe",
  entity_type: "person",
  aliases: [],
  roles: [],
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
    isFetching: false,
    ...opts,
  } as UseEntityResult);
}

function setupForgetMutation(opts: {
  mutateAsync?: (id: string) => Promise<void>;
  isPending?: boolean;
}) {
  const mutateAsync = opts.mutateAsync ?? vi.fn(() => Promise.resolve());
  vi.mocked(useForgetRelationshipEntity).mockReturnValue({
    mutateAsync,
    isPending: opts.isPending ?? false,
  } as ReturnType<typeof useForgetRelationshipEntity>);
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <EntityDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("EntityDetailPage — Forget this entity affordance", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
    setupForgetMutation({});
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the Forget button in Editorial mode (Page.actions slot)", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "editorial" : null,
    );
    setEntityState(BASE_ENTITY);
    renderPage();
    expect(screen.getByTestId("forget-entity-button")).toBeDefined();
  });

  it("renders the Forget button in Workbench mode (Page.actions slot)", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === ENTITY_MODE_STORAGE_KEY ? "workbench" : null,
    );
    setEntityState(BASE_ENTITY);
    renderPage();
    expect(screen.getByTestId("forget-entity-button")).toBeDefined();
  });

  it("opens the confirmation dialog when the Forget button is clicked", () => {
    setEntityState(BASE_ENTITY);
    renderPage();

    fireEvent.click(screen.getByTestId("forget-entity-button"));

    expect(screen.getByRole("alertdialog")).toBeDefined();
    expect(screen.getByText(/Forget this entity\?/i)).toBeDefined();
    // Entity name appears in the dialog (multiple elements on page — use getAllByText)
    expect(screen.getAllByText(/Jane Doe/).length).toBeGreaterThan(0);
    expect(screen.getByText(/cannot be undone/i)).toBeDefined();
  });

  it("closes the dialog and does not call API when Cancel is clicked", async () => {
    const mutateAsync = vi.fn(() => Promise.resolve());
    setupForgetMutation({ mutateAsync });
    setEntityState(BASE_ENTITY);
    renderPage();

    fireEvent.click(screen.getByTestId("forget-entity-button"));
    expect(screen.getByRole("alertdialog")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("calls the API and navigates to /entities on confirmation", async () => {
    const mutateAsync = vi.fn(() => Promise.resolve());
    setupForgetMutation({ mutateAsync });
    setEntityState(BASE_ENTITY);
    renderPage();

    fireEvent.click(screen.getByTestId("forget-entity-button"));
    fireEvent.click(screen.getByRole("button", { name: /forget this entity/i }));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith("entity-abc");
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/entities");
    });
  });

  it("shows an error message in the dialog when the API call fails", async () => {
    const mutateAsync = vi.fn(() => Promise.reject(new Error("Owner required")));
    setupForgetMutation({ mutateAsync });
    setEntityState(BASE_ENTITY);
    renderPage();

    fireEvent.click(screen.getByTestId("forget-entity-button"));
    fireEvent.click(screen.getByRole("button", { name: /forget this entity/i }));

    await waitFor(() => {
      expect(screen.getByText(/Owner required/i)).toBeDefined();
    });
    // Dialog stays open
    expect(screen.getByRole("alertdialog")).toBeDefined();
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});

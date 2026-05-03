// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

const mockNavigate = vi.fn();
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => mockNavigate };
});

import type { EntitySummary } from "@/api/types";
import {
  useArchiveEntity,
  useDeleteEntity,
  useEntities,
  useMergeEntity,
  usePromoteEntity,
  useUnarchiveEntity,
} from "@/hooks/use-memory";
import EntitiesPage from "@/pages/EntitiesPage";

vi.mock("@/hooks/use-memory", () => {
  const mutation = () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  });

  return {
    useEntities: vi.fn(),
    useDeleteEntity: vi.fn(),
    useArchiveEntity: vi.fn(mutation),
    useUnarchiveEntity: vi.fn(mutation),
    useMergeEntity: vi.fn(mutation),
    usePromoteEntity: vi.fn(mutation),
  };
});

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ACTIVE_FACT_ERROR =
  "Entity has 1 active fact(s). Reassign or retire all active facts before deleting this entity.";

const ENTITY: EntitySummary = {
  id: "entity-001",
  canonical_name: "Tanjong Katong studio",
  entity_type: "place",
  aliases: [],
  roles: [],
  fact_count: 1,
  linked_contact_id: null,
  unidentified: false,
  source_butler: "lifestyle",
  source_scope: "lifestyle",
  archived: false,
  created_at: "2026-04-06T12:19:56Z",
  updated_at: "2026-04-06T12:19:56Z",
  dunbar_tier: null,
  dunbar_score: null,
};

function mockEntitiesResult(data: EntitySummary[]): ReturnType<typeof useEntities> {
  return {
    data: {
      data,
      meta: {
        total: data.length,
        offset: 0,
        limit: data.length === 0 ? 200 : 50,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useEntities>;
}

function mockMutationResult<T>(mutateAsync = vi.fn()): T {
  return {
    mutateAsync,
    isPending: false,
  } as unknown as T;
}

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function findButtonByText(root: ParentNode, label: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll("button")).find(
    (button) => button.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined;
}

describe("EntitiesPage delete flow", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();

    vi.mocked(useEntities).mockImplementation((params) => {
      if (params?.unidentified === true) {
        return mockEntitiesResult([]);
      }

      return mockEntitiesResult([ENTITY]);
    });

    vi.mocked(useDeleteEntity).mockReturnValue(
      mockMutationResult<ReturnType<typeof useDeleteEntity>>(
        vi.fn().mockRejectedValue(new Error(ACTIVE_FACT_ERROR)),
      ),
    );

    vi.mocked(useArchiveEntity).mockReturnValue(
      mockMutationResult<ReturnType<typeof useArchiveEntity>>(),
    );

    vi.mocked(useUnarchiveEntity).mockReturnValue(
      mockMutationResult<ReturnType<typeof useUnarchiveEntity>>(),
    );

    vi.mocked(useMergeEntity).mockReturnValue(mockMutationResult<ReturnType<typeof useMergeEntity>>());

    vi.mocked(usePromoteEntity).mockReturnValue(
      mockMutationResult<ReturnType<typeof usePromoteEntity>>(),
    );

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <EntitiesPage />
        </MemoryRouter>,
      );
    });
  }

  it("keeps the delete dialog open and switches to retire-facts confirmation after a 409", async () => {
    renderPage();

    const entityLink = Array.from(container.querySelectorAll("a")).find(
      (link) => link.textContent?.trim() === ENTITY.canonical_name,
    );
    expect(entityLink).toBeDefined();

    const row = entityLink?.closest("tr");
    expect(row).toBeTruthy();

    const rowButtons = row?.querySelectorAll("button") ?? [];
    const deleteButton = rowButtons[rowButtons.length - 1] as HTMLButtonElement | undefined;
    expect(deleteButton).toBeDefined();

    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const confirmDeleteButton = findButtonByText(document.body, "Delete");
    expect(confirmDeleteButton).toBeDefined();

    await act(async () => {
      confirmDeleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(document.body.textContent).toContain("1 active fact(s) that will be retired");
    expect(findButtonByText(document.body, "Retire facts & delete")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Activity button tests
// ---------------------------------------------------------------------------

const PERSON_ENTITY: EntitySummary = {
  id: "entity-person-001",
  canonical_name: "Alice Person",
  entity_type: "person",
  aliases: [],
  roles: [],
  fact_count: 0,
  linked_contact_id: null,
  unidentified: false,
  source_butler: "relationship",
  source_scope: "relationship",
  archived: false,
  created_at: "2026-04-06T12:00:00Z",
  updated_at: "2026-04-06T12:00:00Z",
  dunbar_tier: null,
  dunbar_score: null,
};

describe("EntitiesPage Activity button", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    mockNavigate.mockReset();

    vi.mocked(useArchiveEntity).mockReturnValue(
      mockMutationResult<ReturnType<typeof useArchiveEntity>>(),
    );
    vi.mocked(useUnarchiveEntity).mockReturnValue(
      mockMutationResult<ReturnType<typeof useUnarchiveEntity>>(),
    );
    vi.mocked(useMergeEntity).mockReturnValue(mockMutationResult<ReturnType<typeof useMergeEntity>>());
    vi.mocked(usePromoteEntity).mockReturnValue(
      mockMutationResult<ReturnType<typeof usePromoteEntity>>(),
    );
    vi.mocked(useDeleteEntity).mockReturnValue(
      mockMutationResult<ReturnType<typeof useDeleteEntity>>(),
    );

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  function renderWith(entity: EntitySummary) {
    vi.mocked(useEntities).mockImplementation((params) => {
      if (params?.unidentified === true) return mockEntitiesResult([]);
      return mockEntitiesResult([entity]);
    });
    act(() => {
      root.render(
        <MemoryRouter>
          <EntitiesPage />
        </MemoryRouter>,
      );
    });
  }

  /** Find the Activity button in the row for the given entity by aria-label or svg title fallback.
   *  We locate it by position: it is the 3rd button in the row actions cluster (0-indexed: 0=User, 1=Edit, 2=Activity, 3=Merge, 4=Archive, 5=Delete). */
  function getActivityButton(entity: EntitySummary): HTMLButtonElement | undefined {
    const entityLink = Array.from(container.querySelectorAll("a")).find(
      (a) => a.textContent?.trim() === entity.canonical_name,
    );
    const row = entityLink?.closest("tr");
    const buttons = row?.querySelectorAll("button") ?? [];
    // buttons: [User, Edit, Activity, Merge, Archive/Restore, Delete]
    return buttons[2] as HTMLButtonElement | undefined;
  }

  it("Activity button is enabled for entity_type='person'", () => {
    renderWith(PERSON_ENTITY);
    const btn = getActivityButton(PERSON_ENTITY);
    expect(btn).toBeDefined();
    expect(btn?.disabled).toBe(false);
  });

  it.each(["organization", "place", "other"] as const)(
    "Activity button is disabled for entity_type='%s'",
    (entityType) => {
      const entity: EntitySummary = { ...PERSON_ENTITY, id: `entity-${entityType}`, entity_type: entityType };
      renderWith(entity);
      const btn = getActivityButton(entity);
      expect(btn).toBeDefined();
      expect(btn?.disabled).toBe(true);
    },
  );

  it("clicking enabled Activity button navigates to /entities/<id>", async () => {
    renderWith(PERSON_ENTITY);
    const btn = getActivityButton(PERSON_ENTITY);
    expect(btn).toBeDefined();

    await act(async () => {
      btn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mockNavigate).toHaveBeenCalledWith(`/entities/${PERSON_ENTITY.id}`);
  });

  it.each(["organization", "place", "other"] as const)(
    "clicking disabled Activity button does not navigate for entity_type='%s'",
    async (entityType) => {
      const entity: EntitySummary = { ...PERSON_ENTITY, id: `entity-${entityType}`, entity_type: entityType };
      renderWith(entity);
      const btn = getActivityButton(entity);
      expect(btn?.disabled).toBe(true);

      await act(async () => {
        btn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      expect(mockNavigate).not.toHaveBeenCalled();
    },
  );
});

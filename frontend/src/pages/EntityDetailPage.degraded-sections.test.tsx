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
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityDetailPage from "@/pages/EntityDetailPage";
import { useEntity } from "@/hooks/use-memory";
import {
  useEntityActivity,
  useEntityCommitments,
  useEntityGifts,
  useEntitySharedChronicles,
  useEntityMessageThreads,
  useEntityNeighbours,
} from "@/hooks/use-entities";
import type {
  EntityActivityItem,
  EntityActivityResponse,
  EntityCommitment,
  EntityCommitmentsResponse,
  EntityDetail,
} from "@/api/types";

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
  useEntitySharedChronicles: vi.fn(() => ({ data: { items: [], total: 0, limit: 5, offset: 0, degraded: false, degraded_reason: null }, isLoading: false, isError: false, isRefetching: false, refetch: vi.fn() })),
  useEntityCommitments: vi.fn(() => ({ data: { items: [], total: 0, limit: 50, offset: 0, degraded: false, degraded_reason: null }, isLoading: false, isError: false, isRefetching: false, refetch: vi.fn() })),
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
  // Healthy defaults; a test overrides one section at a time.
  mockSharedChronicles({});
  mockCommitments({});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function episodeRow(index: number, overrides: Partial<EntityActivityItem> = {}): EntityActivityItem {
  const id = `00000000-0000-4000-8000-00000000000${index}`;
  return {
    id, ts: `2026-05-0${index + 1}T23:30:00Z`, kind: "episode", src: "chronicler",
    store: null, predicate: null, episode_id: id, summary: `Episode ${index}`, ...overrides,
  };
}

function mockSharedChronicles(
  result: Partial<EntityActivityResponse> | undefined,
  state: { isError?: boolean; refetch?: () => void } = {},
) {
  vi.mocked(useEntitySharedChronicles).mockReturnValue({
    data: result === undefined ? undefined : {
      items: [], total: 0, limit: 5, offset: 0, degraded: false, degraded_reason: null, ...result,
    },
    isLoading: false, isError: state.isError ?? false, isRefetching: false,
    refetch: state.refetch ?? vi.fn(),
  } as unknown as ReturnType<typeof useEntitySharedChronicles>);
}

function mockCommitments(
  result: Partial<EntityCommitmentsResponse> | undefined,
  state: { isError?: boolean; refetch?: () => void } = {},
) {
  vi.mocked(useEntityCommitments).mockReturnValue({
    data: result === undefined ? undefined : {
      items: [], total: 0, limit: 50, offset: 0, degraded: false, degraded_reason: null, ...result,
    },
    isLoading: false, isError: state.isError ?? false, isRefetching: false,
    refetch: state.refetch ?? vi.fn(),
  } as unknown as ReturnType<typeof useEntityCommitments>);
}

describe("EntityDetailPage — Shared Chronicles and Commitments doors", () => {
  it("renders at most five dated episode doors with the title fallback", () => {
    const rows = Array.from({ length: 6 }, (_, i) => episodeRow(i));
    rows[1] = episodeRow(1, { summary: null });
    mockSharedChronicles({ items: rows, total: 6 });
    renderPage();

    const section = screen.getByTestId("entity-shared-chronicles");
    const links = within(section).getAllByRole("link");
    expect(links).toHaveLength(5);
    expect(links[1].textContent).toBe("Untitled episode");
    // The door targets the Chronicles day in the owner timezone plus the exact episode.
    const door = new URL(links[0].getAttribute("href") ?? "", "http://x");
    expect(door.pathname).toBe("/chronicles");
    expect(door.searchParams.get("episode")).toBe(rows[0].episode_id);
    expect(door.searchParams.get("date")).toMatch(/^2026-05-0[12]$/);
    expect(within(section).queryByText("No shared episodes yet.")).toBeNull();
    expect(useEntitySharedChronicles).toHaveBeenCalledWith("entity-001");
  });

  it("shows the empty copy only for a healthy zero-row read", () => {
    renderPage();
    const section = screen.getByTestId("entity-shared-chronicles");
    expect(within(section).getByText("No shared episodes yet.")).toBeTruthy();
    expect(screen.queryByTestId("entity-shared-chronicles-degraded")).toBeNull();
  });

  it.each([
    ["degraded empty", { degraded: true, degraded_reason: "chronicler_activity_unavailable" as const }, {}],
    ["request failure", undefined, { isError: true }],
    ["unreadable envelope", { items: "nope" as unknown as EntityActivityItem[] }, {}],
  ])("renders a degraded note, never empty history, for %s", (_label, result, state) => {
    mockSharedChronicles(result, state);
    renderPage();
    const section = screen.getByTestId("entity-shared-chronicles");
    expect(within(section).getByTestId("entity-shared-chronicles-degraded").textContent)
      .toContain("Shared Chronicles");
    expect(within(section).queryByText("No shared episodes yet.")).toBeNull();
    // The unified timeline and other sections keep rendering.
    expect(screen.getByTestId("entity-commitments")).toBeTruthy();
    expect(useEntityActivity).toHaveBeenCalled();
  });

  it("keeps valid rows beside the degraded note for a partial read", () => {
    mockSharedChronicles({
      items: [episodeRow(0)], total: 1, degraded: true,
      degraded_reason: "chronicler_activity_unavailable",
    });
    renderPage();
    const section = screen.getByTestId("entity-shared-chronicles");
    expect(within(section).getByTestId("entity-shared-chronicles-degraded")).toBeTruthy();
    expect(within(section).getAllByRole("link")).toHaveLength(1);
  });

  it("retries only the failed section's own read", async () => {
    const user = userEvent.setup();
    const chroniclesRefetch = vi.fn();
    const commitmentsRefetch = vi.fn();
    mockSharedChronicles(undefined, { isError: true, refetch: chroniclesRefetch });
    mockCommitments({}, { refetch: commitmentsRefetch });
    renderPage();
    const section = screen.getByTestId("entity-shared-chronicles");
    await user.click(within(section).getByRole("button", { name: "Retry" }));
    expect(chroniclesRefetch).toHaveBeenCalledWith({ cancelRefetch: false });
    expect(commitmentsRefetch).not.toHaveBeenCalled();
  });

  it("renders both commitment directions read-only, outside any calendar event", () => {
    const owed: EntityCommitment = {
      kind: "promise", direction: "owner_to_other", summary: "Send the book",
      deadline: "2026-09-08T00:00:00Z", escalation_level: "L2", fingerprint: "fp-1",
    };
    const owedToOwner: EntityCommitment = {
      kind: "obligation", direction: "other_to_owner", summary: "Repay the deposit",
      deadline: null, escalation_level: "L0", fingerprint: "fp-2",
    };
    mockCommitments({ items: [owed, owedToOwner], total: 2 });
    renderPage();
    const section = screen.getByTestId("entity-commitments");
    const rows = within(section).getAllByTestId("prep-commitment");
    expect(rows[0].textContent).toContain("Owner owes");
    expect(rows[1].textContent).toContain("Counterparty owes owner");
    expect(within(rows[0]).getByTestId("prep-commitment-deadline")).toBeTruthy();
    expect(within(rows[0]).getByTestId("prep-commitment-escalation").textContent).toBe("L2");
    expect(within(section).queryByRole("button")).toBeNull();
  });

  it.each([
    ["honest empty", {}, {}, false],
    ["degraded source", { degraded: true, degraded_reason: "commitment_source_unavailable" as const }, {}, true],
    ["request failure", undefined, { isError: true }, true],
  ])("Commitments %s", (_label, result, state, degraded) => {
    mockCommitments(result, state);
    renderPage();
    const section = screen.getByTestId("entity-commitments");
    expect(within(section).queryByTestId("entity-commitments-degraded") !== null).toBe(degraded);
    expect(within(section).queryByText("No open commitments.") !== null).toBe(!degraded);
  });
});

describe("EntityDetailPage — supplementary section degraded states", () => {
  it("loads activity beyond 200 while keeping totals and unloaded filters honest", async () => {
    const user = userEvent.setup();
    let finishPage: (() => void) | undefined;
    const pendingPage = new Promise<void>((resolve) => { finishPage = resolve; });
    const fetchNextPage = vi.fn(() => pendingPage);
    const items: EntityActivityItem[] = Array.from({ length: 200 }, (_, index) => ({
      id: `note-${index}`, ts: "2026-05-01T00:00:00Z", kind: "note",
      src: "relationship", store: "narrative", predicate: "contact_note",
      episode_id: null, summary: `Note ${index}`,
    }));
    vi.mocked(useEntityActivity).mockReturnValue({
      data: { pages: [{ items, total: 201, limit: 200, offset: 0, degraded: false, degraded_reason: null }], pageParams: [0] },
      isLoading: false, isError: false, isRefetching: false, refetch: vi.fn(),
      fetchNextPage, hasNextPage: true, isFetchingNextPage: false,
    } as unknown as ReturnType<typeof useEntityActivity>);
    renderPage();
    expect(screen.getByText("Showing 200 of 201")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Gifts 0+" }) as HTMLButtonElement).disabled).toBe(false);
    await user.click(screen.getByRole("button", { name: "Load more activity" }));
    await user.click(screen.getByRole("button", { name: "Load more activity" }));
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
    expect(fetchNextPage).toHaveBeenCalledWith({ cancelRefetch: false });
    finishPage?.();
  });

  it.each([
    ["overlap", 3, "same-id"],
    ["total shrink", 2, "new-id"],
  ])("offers a content-blind refresh when multi-page activity has %s", async (_label, secondTotal, secondId) => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    const item = (id: string): EntityActivityItem => ({
      id, ts: "2026-05-01T00:00:00Z", kind: "note", src: "relationship",
      store: "narrative", predicate: "contact_note", episode_id: null, summary: "Visible summary",
    });
    vi.mocked(useEntityActivity).mockReturnValue({
      data: { pages: [
        { items: [item("same-id")], total: 3, limit: 1, offset: 0, degraded: false, degraded_reason: null },
        { items: [item(secondId)], total: secondTotal, limit: 1, offset: 1, degraded: false, degraded_reason: null },
      ], pageParams: [0, 1] },
      isLoading: false, isError: false, isRefetching: false, refetch,
      fetchNextPage: vi.fn(), hasNextPage: false, isFetchingNextPage: false,
    } as unknown as ReturnType<typeof useEntityActivity>);

    renderPage();
    expect(screen.getByRole("alert").textContent).toContain("Activity changed while loading.");
    expect(screen.queryByRole("button", { name: "Load more activity" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Refresh activity" }));
    expect(refetch).toHaveBeenCalledWith({ cancelRefetch: false });
  });

  it("retries activity without cancelling an in-flight repeat", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    vi.mocked(useEntityActivity).mockReturnValue({
      data: undefined, isLoading: false, isError: true, isRefetching: false,
      refetch, fetchNextPage: vi.fn(), hasNextPage: false, isFetchingNextPage: false,
    } as unknown as ReturnType<typeof useEntityActivity>);
    renderPage();
    const retry = screen.getByRole("button", { name: "Retry" });
    await user.click(retry);
    await user.click(retry);
    expect(refetch).toHaveBeenNthCalledWith(1, { cancelRefetch: false });
    expect(refetch).toHaveBeenNthCalledWith(2, { cancelRefetch: false });
  });

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

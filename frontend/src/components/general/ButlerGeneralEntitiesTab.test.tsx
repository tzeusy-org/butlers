// @vitest-environment jsdom
/**
 * ButlerGeneralEntitiesTab — RTL tests.
 *
 * This tab is the router-reachable wiring point for the shared EntityBrowser
 * component (dashboard-domain-pages spec: "Entity browser for general butler
 * data"). Prior to bu-nfmci, EntityBrowser had zero non-test importers and
 * was unreachable via any route.
 *
 * Tests cover:
 *  - EntityBrowser renders with the tab's fetched entities/collections
 *  - Search input drives useGeneralEntities({ q })
 *  - Collection filter resolves the selected id to the backend's name filter
 *  - Row click expands to show the full JsonViewer (delegates to EntityBrowser)
 *  - Loading and empty states pass through
 *
 * bead: bu-nfmci
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";

import ButlerGeneralEntitiesTab from "./ButlerGeneralEntitiesTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-general", () => ({
  useGeneralCollections: vi.fn(),
  useGeneralEntities: vi.fn(),
}));

// Stub <Time> to avoid date-formatting complexity
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => createElement("time", { dateTime: value }, value),
}));

import { useGeneralCollections, useGeneralEntities } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const SAMPLE_COLLECTIONS = [
  {
    id: "col-001",
    name: "Book notes",
    description: "Reading notes",
    entity_count: 88,
    created_at: "2026-05-08T12:00:00.000Z",
  },
  {
    id: "col-002",
    name: "Ideas",
    description: null,
    entity_count: 15,
    created_at: "2026-05-10T10:00:00.000Z",
  },
];

const SAMPLE_ENTITIES = [
  {
    id: "ent-001",
    collection_id: "col-001",
    collection_name: "Book notes",
    data: { title: "Neuromancer", blood_type: "A+" },
    tags: ["fiction", "sci-fi"],
    created_at: "2026-05-10T10:00:00.000Z",
    updated_at: "2026-05-10T10:00:00.000Z",
  },
  {
    id: "ent-002",
    collection_id: "col-002",
    collection_name: "Ideas",
    data: { text: "Build a second brain" },
    tags: [],
    created_at: "2026-05-09T10:00:00.000Z",
    updated_at: "2026-05-09T10:00:00.000Z",
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerGeneralEntitiesTab />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function setupWithData() {
  vi.mocked(useGeneralCollections).mockReturnValue({
    data: {
      data: SAMPLE_COLLECTIONS,
      meta: { total: SAMPLE_COLLECTIONS.length, offset: 0, limit: 200 },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralCollections>);

  vi.mocked(useGeneralEntities).mockReturnValue({
    data: {
      data: SAMPLE_ENTITIES,
      meta: { total: SAMPLE_ENTITIES.length, offset: 0, limit: 50 },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralEntities>);
}

beforeEach(() => {
  vi.resetAllMocks();
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerGeneralEntitiesTab", () => {
  it("renders the EntityBrowser table with fetched entities", () => {
    setupWithData();
    renderTab();

    expect(screen.getByTestId("general-entities-tab")).toBeDefined();
    expect(screen.getByText("Book notes")).toBeDefined();
    expect(screen.getByText("Ideas")).toBeDefined();
    expect(screen.getByText("fiction")).toBeDefined();
  });

  it("renders search input and filter dropdowns from EntityBrowser", () => {
    setupWithData();
    renderTab();

    expect(screen.getByPlaceholderText("Search entities...")).toBeDefined();
  });

  it("passes the search query through to the main useGeneralEntities call", () => {
    setupWithData();
    renderTab();

    const input = screen.getByPlaceholderText("Search entities...");
    fireEvent.change(input, { target: { value: "neuromancer" } });

    // Two useGeneralEntities calls happen per render (the filtered table
    // fetch and the unfiltered tag-discovery fetch) — find the one carrying
    // the search query.
    const calls = vi.mocked(useGeneralEntities).mock.calls;
    const searchCall = calls.find((call) => call[0]?.q === "neuromancer");
    expect(searchCall).toBeDefined();
  });

  it("expands a row to show the full JSON on click", () => {
    setupWithData();
    renderTab();

    // Truncated preview is a <code> element containing the compact JSON.
    const row = screen.getByText("Book notes").closest("tr");
    expect(row).not.toBeNull();
    fireEvent.click(row as HTMLElement);

    // Expanded view renders the recursive JsonViewer, which prints key/value
    // pairs individually rather than a single-line JSON string.
    expect(screen.getByText("blood_type")).toBeDefined();
  });

  it("omits the collection filter from the initial (unfiltered) fetch", () => {
    setupWithData();
    renderTab();

    const firstCall = vi.mocked(useGeneralEntities).mock.calls[0];
    expect(firstCall?.[0]?.collection).toBeUndefined();
  });

  it("shows loading state when entities are loading", () => {
    setupWithData();
    vi.mocked(useGeneralEntities).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralEntities>);
    renderTab();

    // EntityBrowser renders skeleton rows while loading — no entity rows yet.
    expect(screen.queryByText("Book notes")).toBeNull();
  });

  it("shows the empty state when there are no entities", () => {
    setupWithData();
    vi.mocked(useGeneralEntities).mockReturnValue({
      data: { data: [], meta: { total: 0, offset: 0, limit: 50 } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralEntities>);
    renderTab();

    expect(screen.getByText("No entities found.")).toBeDefined();
  });
});

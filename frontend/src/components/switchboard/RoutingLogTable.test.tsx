// @vitest-environment jsdom
/**
 * RoutingLogTable — focused tests for empty-state rendering.
 *
 * Tests cover:
 *  - Empty state renders when hook returns zero entries (not loading)
 *  - Empty state title and description contain expected text
 *  - Empty state copy contains no user-visible em-dash (—)
 *  - Table renders (not empty state) when entries are present
 *  - Table (skeleton) renders while loading
 *
 * bead: bu-vbizz
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, it, expect, vi, beforeEach } from "vitest";

import RoutingLogTable from "@/components/switchboard/RoutingLogTable";
import { useRoutingLog } from "@/hooks/use-general";

vi.mock("@/hooks/use-general", () => ({
  useRoutingLog: vi.fn(),
}));

type UseRoutingLogResult = ReturnType<typeof useRoutingLog>;

function setQueryState(state: Partial<UseRoutingLogResult>) {
  vi.mocked(useRoutingLog).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseRoutingLogResult);
}

function renderTable(): string {
  return renderToStaticMarkup(<RoutingLogTable />);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SAMPLE_ENTRY = {
  id: "entry-001",
  source_butler: "memory",
  target_butler: "general",
  tool_name: "get_status",
  success: true,
  duration_ms: 42,
  error: null,
  created_at: "2026-05-13T10:00:00Z",
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("RoutingLogTable — empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders the empty state with expected copy when there are no entries and not loading", () => {
    setQueryState({
      data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } },
      isLoading: false,
    });

    const html = renderTable();
    expect(html).toContain("No routing log entries found");
    expect(html).toContain("inter-butler requests");
    expect(html).toContain("switchboard");
  });

  it("empty state copy contains no user-visible em-dash", () => {
    setQueryState({
      data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } },
      isLoading: false,
    });

    const html = renderTable();

    // Isolate the empty-state block (between the <h2> and closing tag).
    // The EmptyState component renders an h2 for title and a <p> for description.
    // We extract all text nodes from those two elements and check for em-dash.
    const h2Match = html.match(/<h2[^>]*>(.*?)<\/h2>/);
    const pMatch = html.match(/<p[^>]*>(.*?)<\/p>/);

    const titleText = h2Match?.[1] ?? "";
    const descText = pMatch?.[1] ?? "";

    expect(titleText).not.toContain("—"); // em-dash
    expect(descText).not.toContain("—"); // em-dash
    // Also guard against the HTML entity form
    expect(titleText).not.toContain("&mdash;");
    expect(descText).not.toContain("&mdash;");
  });

  it("does NOT render the empty state while loading (table skeleton shown instead)", () => {
    setQueryState({
      data: undefined,
      isLoading: true,
    });

    const html = renderTable();
    expect(html).not.toContain("No routing log entries found");
    // The table element is present during loading
    expect(html).toContain("<table");
  });

  it("renders the table (not empty state) when entries are present", () => {
    setQueryState({
      data: {
        data: [SAMPLE_ENTRY],
        meta: { total: 1, offset: 0, limit: 50, has_more: false },
      },
      isLoading: false,
    });

    const html = renderTable();
    expect(html).not.toContain("No routing log entries found");
    expect(html).toContain("<table");
    expect(html).toContain("memory");
    expect(html).toContain("general");
    expect(html).toContain("get_status");
  });

  it("renders no data, undefined state as empty state", () => {
    setQueryState({
      data: undefined,
      isLoading: false,
    });

    // data?.data ?? [] resolves to [] when data is undefined
    const html = renderTable();
    expect(html).toContain("No routing log entries found");
  });
});

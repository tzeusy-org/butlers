// @vitest-environment jsdom
/**
 * RoutingLogTable — focused tests for query-state rendering.
 *
 * Tests cover:
 *  - Empty state renders only for a defined successful zero-entry response
 *  - Empty state title and description contain expected text
 *  - Empty state copy contains no user-visible em-dash (—)
 *  - Table renders (not empty state) when entries are present
 *  - Table (skeleton) renders while loading
 *  - Initial query errors and failed cached refetches remain visibly degraded
 *
 * bead: bu-vbizz
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RoutingLogTable from "@/components/switchboard/RoutingLogTable";
import { useRoutingLog } from "@/hooks/use-general";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("@/hooks/use-general", () => ({
  useRoutingLog: vi.fn(),
}));

type UseRoutingLogResult = ReturnType<typeof useRoutingLog>;

const mountedTables: Array<{ container: HTMLDivElement; root: Root }> = [];

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

async function mountTable(): Promise<HTMLDivElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedTables.push({ container, root });

  await act(async () => {
    root.render(<RoutingLogTable />);
  });

  return container;
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

describe("RoutingLogTable — query states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    for (const { container, root } of mountedTables.splice(0)) {
      act(() => {
        root.unmount();
      });
      container.remove();
    }
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

  it("renders an initial query error with Retry instead of the empty state", async () => {
    const refetch = vi.fn();
    setQueryState({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("routing log unavailable"),
      refetch,
    });

    const container = await mountTable();
    expect(container.querySelector('[role="alert"]')).toBeTruthy();
    expect(container.textContent).toContain("Couldn't reach routing log");
    expect(container.textContent).toContain("routing log unavailable");
    expect(container.textContent).not.toContain("No routing log entries found");

    const retry = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === "Retry",
    );
    expect(retry).toBeTruthy();
    act(() => {
      retry!.click();
    });
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("keeps cached rows visibly degraded when a refetch fails", async () => {
    const refetch = vi.fn();
    setQueryState({
      data: {
        data: [SAMPLE_ENTRY],
        meta: { total: 1, offset: 0, limit: 50, has_more: false },
      },
      isLoading: false,
      isError: true,
      error: new Error("routing log refresh failed"),
      refetch,
    });

    const container = await mountTable();
    const degraded = container.querySelector('[data-testid="routing-log-degraded"]');
    expect(degraded).toBeTruthy();
    expect(degraded?.getAttribute("role")).toBe("alert");
    expect(degraded?.textContent).toContain("Routing log: could not be reached");
    expect(container.textContent).toContain("memory");
    expect(container.textContent).toContain("Page 1 of 1");
    expect(container.textContent).not.toContain("Couldn't reach routing log");

    const retry = degraded?.querySelector("button");
    expect(retry).toBeTruthy();
    act(() => {
      retry!.click();
    });
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("keeps a cached empty response visibly degraded when a refetch fails", async () => {
    const refetch = vi.fn();
    setQueryState({
      data: {
        data: [],
        meta: { total: 0, offset: 0, limit: 25, has_more: false },
      },
      isLoading: false,
      isError: true,
      error: new Error("routing log refresh failed"),
      refetch,
    });

    const container = await mountTable();
    const degraded = container.querySelector('[data-testid="routing-log-degraded"]');

    expect(degraded).toBeTruthy();
    expect(degraded?.getAttribute("role")).toBe("alert");
    expect(container.textContent).toContain("Routing log: could not be reached");
    expect(container.textContent).not.toContain("No routing log entries found");
    expect(container.querySelector("table")).toBeTruthy();

    const retry = degraded?.querySelector("button");
    expect(retry).toBeTruthy();
    act(() => {
      retry!.click();
    });
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("keeps Previous available on a successful empty later page", async () => {
    vi.mocked(useRoutingLog).mockImplementation((params) => {
      const isSecondPage = params?.offset === 25;
      return {
        data: isSecondPage
          ? {
              data: [],
              meta: { total: 26, offset: 25, limit: 25, has_more: false },
            }
          : {
              data: [SAMPLE_ENTRY],
              meta: { total: 26, offset: 0, limit: 25, has_more: true },
            },
        isLoading: false,
        isError: false,
        error: null,
      } as UseRoutingLogResult;
    });

    const container = await mountTable();
    const next = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === "Next",
    );
    expect(next).toBeTruthy();

    act(() => {
      next!.click();
    });

    expect(container.textContent).toContain("No routing log entries found");
    expect(container.textContent).toContain("Page 2 of 2");
    const previous = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === "Previous",
    );
    expect(previous).toBeTruthy();
    expect((previous as HTMLButtonElement).disabled).toBe(false);
  });
});

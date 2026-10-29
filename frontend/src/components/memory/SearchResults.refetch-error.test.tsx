// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";

import { inspectMemory } from "@/api/client";
import { ownerFetch } from "@/api/owner-session";
vi.mock("@/api/owner-session", async (original) => ({ ...await original<typeof import("@/api/owner-session")>(), ownerFetch: vi.fn() }));
import SearchResults from "@/components/memory/SearchResults";

vi.mock("@/api/client", async (original) => ({
  ...await original<typeof import("@/api/client")>(),
  inspectMemory: vi.fn((await original<typeof import("@/api/client")>()).inspectMemory),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("search refetch failure after a cached response", () => {
  afterEach(() => { vi.mocked(inspectMemory).mockClear(); vi.mocked(ownerFetch).mockReset(); });

  it.each(["empty", "non-empty"] as const)("replaces cached %s results with a retryable error, then recovers", async (population) => {
    const rows = population === "empty" ? [] : [{
      id: "search-episode",
      kind: "episode",
      content: "Retained search episode",
      butler: "general",
      created_at: "2026-02-19T01:00:00Z",
      metadata: {},
    }];
    const cached = {
      data: rows,
      meta: { total: rows.length, offset: 0, limit: 50, has_more: false },
    };
    const recovered = {
      ...cached,
      data: rows.map((row) => ({ ...row, content: "Recovered search episode" })),
    };
    const initialText = population === "empty" ? "Nothing in the books." : "Retained search episode";
    const recoveredText = population === "empty" ? "Nothing in the books." : "Recovered search episode";
    vi.mocked(ownerFetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(cached)))
      .mockRejectedValueOnce(new Error("inspect unavailable"))
      .mockResolvedValueOnce(new Response(JSON.stringify(recovered)));

    // Use the real hook/cache: refetch errors retain successful data in React Query.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    try {
      await act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <MemoryRouter>
              <SearchResults q="fatigue" kind="all" />
            </MemoryRouter>
          </QueryClientProvider>,
        );
      });
      await waitFor(() => expect(container.textContent).toContain(initialText));

      await act(async () => { await client.refetchQueries(); });

      await waitFor(() => {
        const error = container.querySelector('[role="alert"]');
        expect(error?.getAttribute("role")).toBe("alert");
        expect(error?.textContent).toContain("search results");
        expect(container.textContent).not.toContain("Nothing in the books.");
        expect(container.textContent).not.toContain("Retained search episode");
      });

      const retry = container.querySelector<HTMLButtonElement>('[role="alert"] button');
      expect(retry?.textContent).toMatch(/^retry$/i);
      await act(async () => { retry!.click(); });
      await waitFor(() => {
        expect(inspectMemory).toHaveBeenCalledTimes(3);
        expect(container.textContent).toContain(recoveredText);
        expect(container.querySelector('[role="alert"]')).toBeNull();
      });
    } finally {
      act(() => root.unmount());
      client.clear();
      container.remove();
    }
  });
});

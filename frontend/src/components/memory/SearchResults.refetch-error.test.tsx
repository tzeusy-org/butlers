// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";

import { inspectMemory } from "@/api/client";
import SearchResults from "@/components/memory/SearchResults";

vi.mock("@/api/client", async (original) => ({
  ...await original<typeof import("@/api/client")>(),
  inspectMemory: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("search refetch failure after an empty response", () => {
  afterEach(() => vi.mocked(inspectMemory).mockReset());

  it("replaces the cached empty verdict with a retryable error, then recovers", async () => {
    const empty = { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } };
    vi.mocked(inspectMemory)
      .mockResolvedValueOnce(empty)
      .mockRejectedValueOnce(new Error("inspect unavailable"))
      .mockResolvedValueOnce(empty);

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
      await waitFor(() => expect(container.textContent).toContain("Nothing in the books."));

      await act(async () => { await client.refetchQueries(); });

      await waitFor(() => {
        const error = container.querySelector('[data-testid="memory-search-error"]');
        expect(error?.getAttribute("role")).toBe("alert");
        expect(error?.textContent).toContain("Couldn’t load search results.");
        expect(container.textContent).not.toContain("Nothing in the books.");
      });

      const retry = container.querySelector<HTMLButtonElement>('[data-testid="memory-search-error"] button');
      expect(retry?.textContent).toBe("retry");
      await act(async () => { retry!.click(); });
      await waitFor(() => {
        expect(inspectMemory).toHaveBeenCalledTimes(3);
        expect(container.textContent).toContain("Nothing in the books.");
        expect(container.querySelector('[data-testid="memory-search-error"]')).toBeNull();
      });
    } finally {
      act(() => root.unmount());
      client.clear();
      container.remove();
    }
  });
});

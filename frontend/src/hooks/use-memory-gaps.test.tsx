// @vitest-environment jsdom
/** Cursor paging and refetch-on-dismiss of the knowledge-gap hooks (bu-q7vx1q.73). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useDismissMemoryGap, useMemoryGaps } from "@/hooks/use-memory-gaps";
import { dismissMemoryGap, getMemoryGaps } from "@/api/memory-gaps.ts";

vi.mock("@/api/memory-gaps.ts", () => ({
  getMemoryGaps: vi.fn(),
  getMemoryGapDemand: vi.fn(),
  dismissMemoryGap: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function page(ids: string[], next: string | null) {
  return {
    data: ids.map((id) => ({ gap_id: id })),
    meta: { limit: 2, next_cursor: next, has_more: next !== null },
  };
}

describe("memory gap hooks", () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let latest: ReturnType<typeof useMemoryGaps>;
  let dismiss: ReturnType<typeof useDismissMemoryGap>;

  function Probe({
    onRead,
  }: {
    onRead: (
      gaps: ReturnType<typeof useMemoryGaps>,
      dismissal: ReturnType<typeof useDismissMemoryGap>,
    ) => void;
  }) {
    onRead(useMemoryGaps({ limit: 2 }), useDismissMemoryGap());
    return null;
  }
  const read = (
    gaps: ReturnType<typeof useMemoryGaps>,
    dismissal: ReturnType<typeof useDismissMemoryGap>,
  ) => {
    latest = gaps;
    dismiss = dismissal;
  };

  beforeEach(() => {
    vi.resetAllMocks();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function mount() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe onRead={read} />
        </QueryClientProvider>,
      );
    });
    // Let the query resolve and the observer re-render.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it("walks the keyset cursor: the first page has none, the next sends next_cursor", async () => {
    vi.mocked(getMemoryGaps)
      .mockResolvedValueOnce(page(["a", "b"], "CUR1") as never)
      .mockResolvedValueOnce(page(["c"], null) as never);
    await mount();

    expect(vi.mocked(getMemoryGaps).mock.calls[0][0]).toMatchObject({ limit: 2, cursor: null });
    expect(latest.hasNextPage).toBe(true);

    await act(async () => {
      await latest.fetchNextPage();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(vi.mocked(getMemoryGaps).mock.calls[1][0]).toMatchObject({ cursor: "CUR1" });
    expect(latest.hasNextPage).toBe(false);
    expect(latest.data?.pages.flatMap((p) => p.data.map((g) => g.gap_id))).toEqual(["a", "b", "c"]);
  });

  it("refetches the list after a dismissal succeeds", async () => {
    vi.mocked(getMemoryGaps).mockResolvedValue(page(["a"], null) as never);
    vi.mocked(dismissMemoryGap).mockResolvedValue({
      data: { gap_id: "a", status: "dismissed" },
      meta: {},
    } as never);
    await mount();
    expect(vi.mocked(getMemoryGaps)).toHaveBeenCalledTimes(1);

    await act(async () => {
      await dismiss.mutateAsync("a");
    });

    expect(vi.mocked(dismissMemoryGap).mock.calls[0][0]).toBe("a");
    expect(vi.mocked(getMemoryGaps)).toHaveBeenCalledTimes(2);
  });

  it("also refetches when the dismissal fails (404/409 shows the current state, no ghost)", async () => {
    vi.mocked(getMemoryGaps).mockResolvedValue(page(["a"], null) as never);
    vi.mocked(dismissMemoryGap).mockRejectedValue(new Error("409"));
    await mount();

    await act(async () => {
      await dismiss.mutateAsync("a").catch(() => undefined);
    });

    expect(vi.mocked(getMemoryGaps)).toHaveBeenCalledTimes(2);
  });
});

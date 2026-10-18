// @vitest-environment jsdom
/** useMemoryGaps scopes its query to one entity (bu-q7vx1q.71). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useMemoryGaps } from "@/hooks/use-memory-gaps";
import { getMemoryGaps } from "@/api/memory-gaps.ts";

vi.mock("@/api/memory-gaps.ts", () => ({
  getMemoryGaps: vi.fn(),
  getMemoryGapDemand: vi.fn(),
  dismissMemoryGap: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function Probe({ entityId }: { entityId?: string }) {
  useMemoryGaps({ entityId });
  return null;
}

describe("useMemoryGaps entity scope", () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getMemoryGaps).mockResolvedValue({
      data: [],
      meta: { limit: 50, next_cursor: null, has_more: false },
    } as never);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function render(entityId?: string) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe entityId={entityId} />
        </QueryClientProvider>,
      );
    });
  }

  it("forwards the entity id to the client", async () => {
    await render("entity-A");

    expect(vi.mocked(getMemoryGaps).mock.calls[0][0]).toMatchObject({ entityId: "entity-A" });
  });

  it("does not share a cache entry between two entities or with the unfiltered list", async () => {
    await render("entity-A");
    await render("entity-B");
    await render(undefined);

    const seen = vi.mocked(getMemoryGaps).mock.calls.map((call) => call[0]?.entityId);
    expect(seen).toEqual(["entity-A", "entity-B", undefined]);
  });
});

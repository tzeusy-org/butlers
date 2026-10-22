// @vitest-environment jsdom
//
// Registers stop fabricating health (bu-kqnum.14.5): when a memory pool drops out
// (`meta.pools_failed`), the episode / fact / rule registers name it, including with
// zero rows, and show their calm empty copy only for a healthy empty response.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import EpisodesRegister from "@/components/memory/EpisodesRegister";
import FactsRegister from "@/components/memory/FactsRegister";
import RulesRegister from "@/components/memory/RulesRegister";
import {
  useEpisodes,
  useFacts,
  useRetryEpisodeConsolidation,
  useRules,
} from "@/hooks/use-memory";

vi.mock("@/hooks/use-memory", () => ({
  useEpisodes: vi.fn(),
  useFacts: vi.fn(),
  useRules: vi.fn(),
  useRetryEpisodeConsolidation: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const CASES = [
  {
    name: "facts",
    Register: FactsRegister,
    hook: useFacts,
    calm: "The ledger is empty.",
    testId: "memory-facts-degraded",
  },
  {
    name: "rules",
    Register: RulesRegister,
    hook: useRules,
    calm: "No standing orders yet.",
    testId: "memory-rules-degraded",
  },
  {
    name: "episodes",
    Register: EpisodesRegister,
    hook: useEpisodes,
    calm: "Nothing observed yet.",
    testId: "memory-episodes-degraded",
  },
] as const;

function prime(hook: unknown, value: unknown) {
  (hook as ReturnType<typeof vi.fn>).mockReturnValue(value);
}

function page(rows: unknown[], poolsFailed?: string[], total = rows.length) {
  return {
    data: {
      data: rows,
      meta: {
        total,
        offset: 0,
        limit: 50,
        has_more: false,
        ...(poolsFailed ? { pools_failed: poolsFailed } : {}),
      },
    },
  };
}

describe("memory registers under a partial pool outage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useRetryEpisodeConsolidation).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useRetryEpisodeConsolidation>);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(Register: (typeof CASES)[number]["Register"]) {
    act(() => {
      root.render(
        <MemoryRouter>
          <Register />
        </MemoryRouter>,
      );
    });
  }

  describe.each(CASES)("$name register", ({ Register, hook, calm, testId }) => {
    it("names the failed pools and withholds the calm empty copy with zero rows", () => {
      prime(hook, page([], ["finance", "health"]));

      render(Register);

      const note = container.querySelector(`[data-testid="${testId}"]`);
      expect(note?.textContent).toContain("finance, health");
      expect(container.textContent).not.toContain(calm);
    });

    it("withholds the calm empty copy until the first response arrives", () => {
      prime(hook, { data: undefined, isPending: true });

      render(Register);

      expect(container.textContent).not.toContain(calm);
    });

    it("marks only degraded ranges partial", () => {
      prime(hook, page([], undefined, 7));
      render(Register);
      expect(container.textContent).toContain("0 of 7");
      expect(container.textContent).not.toContain("(partial)");

      prime(hook, page([], ["finance"], 7));

      render(Register);

      expect(container.textContent).toContain("0 of 7 (partial)");
    });
  });

  it("shows the note alongside rows that did come back", () => {
    const episode = {
      id: "e1",
      butler: "general",
      session_id: null,
      content: "Owner mentioned fatigue.",
      importance: 5,
      reference_count: 0,
      consolidated: false,
      consolidation_status: "pending",
      created_at: "2026-02-19T01:00:00Z",
      last_referenced_at: null,
      expires_at: null,
      metadata: {},
    };
    vi.mocked(useEpisodes).mockReturnValue(page([episode], ["finance"]) as never);

    render(EpisodesRegister);

    expect(container.textContent).toContain("Owner mentioned fatigue.");
    expect(
      container.querySelector('[data-testid="memory-episodes-degraded"]')?.textContent,
    ).toContain("finance");
  });
});

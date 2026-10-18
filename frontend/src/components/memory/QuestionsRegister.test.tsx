// @vitest-environment jsdom
/**
 * Component tests for QuestionsRegister (bu-q7vx1q.73).
 *
 * The register lists open and answerable knowledge gaps with coverage words, the
 * server-aggregated demand summary, the calm empty line, a named degraded line, a
 * load-error line that never reads as empty, and dismiss through the mutation.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import QuestionsRegister from "@/components/memory/QuestionsRegister";
import { useDismissMemoryGap, useMemoryGapDemand, useMemoryGaps } from "@/hooks/use-memory-gaps";
import { coverageWord } from "@/lib/memory-gaps";
import type { MemoryGap, MemoryGapDemandRow } from "@/api/memory-gaps.ts";

vi.mock("@/hooks/use-memory-gaps", () => ({
  useMemoryGaps: vi.fn(),
  useMemoryGapDemand: vi.fn(),
  useDismissMemoryGap: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function makeGap(overrides: Partial<MemoryGap> = {}): MemoryGap {
  return {
    gap_id: "gap-1",
    butler: "relationship",
    entity_id: "11111111-2222-3333-4444-555555555555",
    entity_name: "Wei",
    predicate: "favorite_coffee",
    question: "What does Wei drink in the morning?",
    status: "open",
    coverage_state: "unknown",
    asked_at: "2026-06-12T08:00:00Z",
    expires_at: "2026-09-10T08:00:00Z",
    answered_by_ref: null,
    delivery_failed: false,
    last_error: null,
    origins: [
      {
        conversation_id: "c1",
        request_id: null,
        channel: "dashboard",
        asked_at: "2026-06-12T08:00:00Z",
        delivered_at: null,
      },
      {
        conversation_id: "c2",
        request_id: null,
        channel: "dashboard",
        asked_at: "2026-06-13T08:00:00Z",
        delivered_at: null,
      },
    ],
    ...overrides,
  };
}

interface GapsState {
  gaps?: MemoryGap[];
  degraded?: string[];
  isError?: boolean;
  isPending?: boolean;
  hasNextPage?: boolean;
}

const fetchNextPage = vi.fn();
const refetchGaps = vi.fn();
const mutate = vi.fn();

function setGaps(state: GapsState = {}) {
  vi.mocked(useMemoryGaps).mockReturnValue({
    data: state.isError
      ? undefined
      : {
          pages: [
            {
              data: state.gaps ?? [],
              meta: {
                limit: 50,
                next_cursor: null,
                has_more: false,
                sources_degraded: state.degraded,
              },
            },
          ],
        },
    isError: state.isError ?? false,
    isPending: state.isPending ?? false,
    hasNextPage: state.hasNextPage ?? false,
    isFetchingNextPage: false,
    fetchNextPage,
    refetch: refetchGaps,
  } as unknown as ReturnType<typeof useMemoryGaps>);
}

function setDemand(rows: MemoryGapDemandRow[] | "error", degraded?: string[]) {
  vi.mocked(useMemoryGapDemand).mockReturnValue(
    (rows === "error"
      ? { data: undefined, isError: true }
      : {
          data: { data: rows, meta: { sources_degraded: degraded } },
          isError: false,
        }) as unknown as ReturnType<typeof useMemoryGapDemand>,
  );
}

describe("QuestionsRegister", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDismissMemoryGap).mockReturnValue({
      mutate,
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useDismissMemoryGap>);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QuestionsRegister />
        </MemoryRouter>,
      );
    });
  }

  it("renders open and answerable rows with status, coverage words, asked date and thread count", () => {
    setGaps({
      gaps: [
        makeGap(),
        makeGap({
          gap_id: "gap-2",
          question: "Where does Wei live?",
          predicate: "lives_in",
          status: "answerable",
          coverage_state: "present",
          origins: [makeGap().origins[0]],
        }),
      ],
    });
    setDemand([]);
    render();

    const rows = container.querySelectorAll('[data-testid="question-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("What does Wei drink in the morning?");
    expect(rows[0].textContent).toContain("open");
    expect(rows[0].textContent).toContain("never looked");
    expect(rows[0].textContent).toContain("2 threads");
    expect(rows[0].textContent).toContain("Jun 12, 2026");
    expect(rows[0].textContent).toContain("favorite_coffee");
    expect(rows[1].textContent).toContain("answerable");
    expect(rows[1].textContent).toContain("answered");
    expect(rows[1].textContent).toContain("1 thread");
    expect(rows[1].textContent).not.toContain("1 threads");
  });

  it("links the entity and says which coverage word each state means", () => {
    setGaps({ gaps: [makeGap()] });
    setDemand([]);
    render();

    const link = container.querySelector('a[href="/entities/11111111-2222-3333-4444-555555555555"]');
    expect(link?.textContent).toBe("Wei");
    expect(coverageWord("unknown")).toBe("never looked");
    expect(coverageWord("absent_proven")).toBe("looked, nothing there");
    expect(coverageWord("unavailable")).toBe("unavailable");
    expect(coverageWord("present")).toBe("answered");
    expect(coverageWord("some_new_state")).toBe("some new state");
  });

  it("marks a gap whose delivery failed", () => {
    setGaps({ gaps: [makeGap({ delivery_failed: true, status: "answerable" })] });
    setDemand([]);
    render();

    expect(container.textContent).toContain("delivery failed");
  });

  it("shows the per-predicate demand summary above the rows", () => {
    setGaps({ gaps: [makeGap()] });
    setDemand([
      { predicate: "favorite_coffee", open_gaps: 2, asks: 3 },
      { predicate: "lives_in", open_gaps: 1, asks: 1 },
    ]);
    render();

    const demand = container.querySelector('[data-testid="questions-demand"]');
    expect(demand?.textContent).toContain("favorite_coffee");
    expect(demand?.textContent).toContain("3 asks");
    expect(demand?.textContent).toContain("2 open");
    expect(demand?.textContent).toContain("1 ask ");
    expect(
      demand!.compareDocumentPosition(container.querySelector('[data-testid="question-row"]')!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("reads No open questions. and hides the demand summary when there are none", () => {
    setGaps({ gaps: [] });
    setDemand([]);
    render();

    expect(container.textContent).toContain("No open questions.");
    expect(container.querySelector('[data-testid="questions-demand"]')).toBeNull();
  });

  it("names the pools that could not be read instead of reading as empty", () => {
    setGaps({ gaps: [makeGap()], degraded: ["finance"] });
    setDemand([], ["health"]);
    render();

    const line = container.querySelector('[data-testid="questions-degraded"]');
    expect(line?.textContent).toContain("Some butlers could not be read");
    expect(line?.textContent).toContain("finance");
    expect(line?.textContent).toContain("health");
  });

  it("renders the load error, never the empty line, when the list fails", () => {
    setGaps({ isError: true });
    setDemand([]);
    render();

    expect(container.querySelector('[data-testid="memory-questions-error"]')).not.toBeNull();
    expect(container.textContent).not.toContain("No open questions.");
    act(() => {
      (container.querySelector('[data-testid="memory-questions-error"] button') as HTMLElement).click();
    });
    expect(refetchGaps).toHaveBeenCalledTimes(1);
  });

  it("says when the demand summary failed rather than showing zero demand", () => {
    setGaps({ gaps: [makeGap()] });
    setDemand("error");
    render();

    expect(container.textContent).toContain("Couldn’t load which questions are asked most.");
    expect(container.querySelector('[data-testid="questions-demand"]')).toBeNull();
    expect(container.querySelectorAll('[data-testid="question-row"]')).toHaveLength(1);
  });

  it("dismisses only after a confirm and passes the gap id to the mutation", () => {
    setGaps({ gaps: [makeGap()] });
    setDemand([]);
    render();

    const dismiss = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent === "dismiss",
    )!;
    act(() => dismiss.click());
    expect(mutate).not.toHaveBeenCalled();

    const confirm = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent === "confirm dismiss",
    )!;
    act(() => confirm.click());
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toBe("gap-1");
  });

  it("keep backs out of a dismiss without calling the mutation", () => {
    setGaps({ gaps: [makeGap()] });
    setDemand([]);
    render();

    act(() => {
      Array.from(container.querySelectorAll("button"))
        .find((b) => b.textContent === "dismiss")!
        .click();
    });
    act(() => {
      Array.from(container.querySelectorAll("button"))
        .find((b) => b.textContent === "keep")!
        .click();
    });

    expect(mutate).not.toHaveBeenCalled();
    expect(Array.from(container.querySelectorAll("button")).some((b) => b.textContent === "dismiss")).toBe(
      true,
    );
  });

  it("names a failed dismissal", () => {
    setGaps({ gaps: [makeGap()] });
    setDemand([]);
    vi.mocked(useDismissMemoryGap).mockReturnValue({
      mutate,
      isPending: false,
      isError: true,
    } as unknown as ReturnType<typeof useDismissMemoryGap>);
    render();

    expect(container.querySelector('[role="alert"]')?.textContent).toContain("dismiss failed");
  });

  it("pages with a more pill when the server has another page", () => {
    setGaps({ gaps: [makeGap()], hasNextPage: true });
    setDemand([]);
    render();

    const more = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "more")!;
    act(() => more.click());
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });
});

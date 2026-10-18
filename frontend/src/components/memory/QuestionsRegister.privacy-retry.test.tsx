// @vitest-environment jsdom
/**
 * Review follow-ups for QuestionsRegister (bu-q7vx1q.73).
 *
 * - A row whose delivery failed says so and nothing more: `last_error` can echo the answered
 *   value, and origin conversation ids are thread identifiers, so neither is rendered.
 * - A failed dismissal offers a way to try again that returns to the confirm step.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import QuestionsRegister from "@/components/memory/QuestionsRegister";
import { useDismissMemoryGap, useMemoryGapDemand, useMemoryGaps } from "@/hooks/use-memory-gaps";
import type { MemoryGap } from "@/api/memory-gaps.ts";

vi.mock("@/hooks/use-memory-gaps", () => ({
  useMemoryGaps: vi.fn(),
  useMemoryGapDemand: vi.fn(),
  useDismissMemoryGap: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const SECRET = "SECRET-VALUE";

function makeGap(overrides: Partial<MemoryGap> = {}): MemoryGap {
  return {
    gap_id: "gap-1",
    butler: "relationship",
    entity_id: "11111111-2222-3333-4444-555555555555",
    entity_name: "Wei",
    predicate: "favorite_coffee",
    question: "What does Wei drink in the morning?",
    status: "answerable",
    coverage_state: "unknown",
    asked_at: "2026-06-12T08:00:00Z",
    expires_at: "2026-09-10T08:00:00Z",
    answered_by_ref: null,
    delivery_failed: false,
    last_error: null,
    origins: [
      {
        conversation_id: "thread-origin-aaa",
        request_id: null,
        channel: "dashboard",
        asked_at: "2026-06-12T08:00:00Z",
        delivered_at: null,
      },
      {
        conversation_id: "thread-origin-bbb",
        request_id: null,
        channel: "dashboard",
        asked_at: "2026-06-13T08:00:00Z",
        delivered_at: null,
      },
    ],
    ...overrides,
  };
}

function setGaps(gaps: MemoryGap[]) {
  vi.mocked(useMemoryGaps).mockReturnValue({
    data: {
      pages: [{ data: gaps, meta: { limit: 50, next_cursor: null, has_more: false } }],
    },
    isError: false,
    isPending: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useMemoryGaps>);
  vi.mocked(useMemoryGapDemand).mockReturnValue({
    data: { data: [], meta: {} },
    isError: false,
  } as unknown as ReturnType<typeof useMemoryGapDemand>);
}

describe("QuestionsRegister review follow-ups", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
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

  function button(label: string): HTMLButtonElement | undefined {
    return Array.from(container.querySelectorAll("button")).find((b) => b.textContent === label);
  }

  it("renders neither last_error nor origin conversation ids on a delivery-failed row", () => {
    vi.mocked(useDismissMemoryGap).mockReturnValue({
      mutate: vi.fn(),
      reset: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useDismissMemoryGap>);
    setGaps([
      makeGap({
        delivery_failed: true,
        last_error: `HTTPError: payload Now known: ${SECRET} (source: chat)`,
      }),
    ]);
    render();

    const text = container.textContent ?? "";
    expect(text).toContain("delivery failed");
    expect(text).not.toContain(SECRET);
    expect(text).not.toContain("HTTPError");
    expect(text).not.toContain("thread-origin-aaa");
    expect(text).not.toContain("thread-origin-bbb");
    expect(container.innerHTML).not.toContain(SECRET);
    expect(container.innerHTML).not.toContain("thread-origin");
  });

  it("offers try again after a failed dismissal and returns to the confirm step", () => {
    const state = { isError: true };
    const reset = vi.fn(() => {
      state.isError = false;
    });
    vi.mocked(useDismissMemoryGap).mockImplementation(
      () =>
        ({
          mutate: vi.fn(),
          reset,
          isPending: false,
          isError: state.isError,
        }) as unknown as ReturnType<typeof useDismissMemoryGap>,
    );
    setGaps([makeGap()]);
    render();

    expect(container.querySelector('[role="alert"]')?.textContent).toContain("dismiss failed");
    expect(button("confirm dismiss")).toBeUndefined();

    act(() => button("try again")!.click());

    expect(reset).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(button("confirm dismiss")).toBeDefined();
    expect(button("keep")).toBeDefined();
  });
});

// @vitest-environment jsdom
/**
 * Privacy pin for the entity "Still unknown" row (bu-q7vx1q.71).
 *
 * `last_error` is the delivery post's `str(exc)`, and the posted text contains the answered
 * value ("Now known: ..."). The row says "delivery failed" and renders neither the error
 * text nor its exception class.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import EntityStillUnknown from "@/components/memory/EntityStillUnknown";
import { useDismissMemoryGap, useMemoryGaps } from "@/hooks/use-memory-gaps";
import type { MemoryGap } from "@/api/memory-gaps.ts";

vi.mock("@/hooks/use-memory-gaps", () => ({
  useMemoryGaps: vi.fn(),
  useDismissMemoryGap: vi.fn(),
}));

const SECRET = "SECRET-VALUE";

function makeGap(overrides: Partial<MemoryGap> = {}): MemoryGap {
  return {
    gap_id: "gap-1",
    butler: "relationship",
    entity_id: "entity-001",
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
    ],
    ...overrides,
  };
}

describe("EntityStillUnknown privacy", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDismissMemoryGap).mockReturnValue({
      mutate: vi.fn(),
      reset: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useDismissMemoryGap>);
  });

  afterEach(() => cleanup());

  it("renders neither last_error nor its exception class on a delivery-failed row", () => {
    vi.mocked(useMemoryGaps).mockReturnValue({
      data: {
        pages: [
          {
            data: [
              makeGap({
                delivery_failed: true,
                last_error: `HTTPError: payload Now known: ${SECRET} (source: chat)`,
              }),
            ],
            meta: { limit: 50, next_cursor: null, has_more: false },
          },
        ],
      },
      isError: false,
      isPending: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMemoryGaps>);

    const { container } = render(
      <MemoryRouter>
        <EntityStillUnknown entityId="entity-001" />
      </MemoryRouter>,
    );

    const row = container.querySelector('[data-testid="still-unknown-row"]');
    expect(row).not.toBeNull();
    expect(row?.textContent).toContain("delivery failed");
    expect(container.innerHTML).not.toContain(SECRET);
    expect(container.innerHTML).not.toContain("HTTPError");
    expect(container.innerHTML).not.toContain("Now known");
  });
});

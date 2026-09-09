// @vitest-environment jsdom
/**
 * HeldCapturesPanel tests (bu-2jtfw.9).
 *
 * A held capture is a ledger row whose routing session never finished --
 * these tests pin that it always renders as "Held", never as though it were
 * saved/filed away, and that an unreadable ledger degrades honestly instead
 * of rendering a false "nothing held" empty state.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import HeldCapturesPanel from "./HeldCapturesPanel";
import type { CapturesListResponse } from "@/api/index.ts";

vi.mock("@/hooks/use-captures", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-captures")>();
  return { ...actual, useHeldCaptures: vi.fn() };
});

import { useHeldCaptures } from "@/hooks/use-captures";

const mockUseHeldCaptures = vi.mocked(useHeldCaptures);

type CapturesQuery = ReturnType<typeof useHeldCaptures>;

function stubQuery(
  data: CapturesListResponse | undefined,
  extra: Partial<{ isError: boolean; isLoading: boolean }> = {},
) {
  mockUseHeldCaptures.mockReturnValue({
    data,
    isError: extra.isError ?? false,
    isLoading: extra.isLoading ?? false,
    refetch: vi.fn(),
  } as unknown as CapturesQuery);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("HeldCapturesPanel", () => {
  it("renders a held capture with a Held badge, never a saved/filed claim", () => {
    stubQuery({
      data: [
        {
          capture_id: "11111111-1111-1111-1111-111111111111",
          channel: "telegram",
          content: "a stray thought",
          receipt_state: "held",
          source_butler: "general",
          created_at: "2026-09-09T12:00:00+00:00",
          updated_at: "2026-09-09T12:00:00+00:00",
        },
      ],
      meta: { limit: 50, has_more: false, next_cursor: null },
    });

    render(<HeldCapturesPanel />);

    const row = screen.getByTestId("held-capture-row");
    expect(row.textContent).toMatch(/a stray thought/);
    const badge = screen.getByTestId("held-capture-badge");
    expect(badge.textContent).toMatch(/Held/);
    expect(screen.queryByText(/saved/i)).toBeNull();
  });

  it("renders an explicit empty state when nothing is held", () => {
    stubQuery({ data: [], meta: { limit: 50, has_more: false, next_cursor: null } });

    render(<HeldCapturesPanel />);

    expect(screen.getByTestId("held-captures-empty")).toBeTruthy();
    expect(screen.queryByTestId("held-capture-row")).toBeNull();
  });

  it("degrades honestly instead of showing a false empty state when the ledger is unreachable", () => {
    stubQuery({
      data: [],
      meta: { limit: 50, has_more: false, next_cursor: null, sources_degraded: ["general"] },
    });

    render(<HeldCapturesPanel />);

    expect(screen.getByTestId("held-captures-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("held-captures-empty")).toBeNull();
  });

  it("degrades honestly on a query error", () => {
    stubQuery(undefined, { isError: true });

    render(<HeldCapturesPanel />);

    expect(screen.getByTestId("held-captures-unavailable")).toBeTruthy();
  });
});

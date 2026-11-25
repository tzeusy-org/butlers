// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client.ts";
import type { NowCard } from "@/api/types.ts";
import { NowStrip } from "@/components/layout/NowStrip";
import { msUntilNextBoundary } from "@/hooks/use-now.ts";

const getNow = vi.fn();
vi.mock("@/api/client.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client.ts")>()),
  getNow: () => getNow(),
}));

function card(text: string, extra: Record<string, unknown> = {}): { data: NowCard } {
  return {
    data: {
      as_of: "2026-11-26T08:00:00Z",
      headline: "1 of 1 signals not current",
      all_current: true,
      text: "",
      lines: [
        {
          kind: "owner.agenda",
          label: "Next",
          state: "known",
          text,
          stale: false,
          source: "calendar",
          age_seconds: 0,
          door: "/calendar?entry=inst-1",
          ...extra,
        },
      ],
    },
  };
}

function mount(
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  announce?: boolean | "below-md",
) {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NowStrip announce={announce} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => {
  getNow.mockReset();
});
afterEach(cleanup);

describe("NowStrip", () => {
  it("shows the agenda line with a door to its entry", async () => {
    getNow.mockResolvedValue(card("In meeting until 15:00; next 16:00"));
    mount();

    const strip = await screen.findByTestId("now-strip");
    expect(strip.textContent).toContain("In meeting until 15:00; next 16:00");
    expect(screen.getByRole("link").getAttribute("href")).toBe("/calendar?entry=inst-1");
  });

  it("renders nothing, and no fake line, when the surface is off (404)", async () => {
    getNow.mockRejectedValue(new ApiError("NOT_FOUND", "Not Found", 404));
    const client = mount();

    await waitFor(() => expect(client.getQueryState(["now"])?.status).toBe("error"));
    expect(screen.queryByTestId("now-strip")).toBeNull();
    expect(screen.queryByTestId("now-strip-unavailable")).toBeNull();
    expect(document.body.textContent).toBe("");
  });

  it("says it is unavailable, not free, when the reader fails (503)", async () => {
    getNow.mockRejectedValue(new ApiError("now_unavailable", "down", 503));
    mount();

    expect((await screen.findByTestId("now-strip-unavailable")).textContent).toBe("Now unavailable");
    expect(screen.queryByTestId("now-strip")).toBeNull();
  });

  it("renders nothing when the card has no agenda line", async () => {
    getNow.mockResolvedValue({ data: { ...card("x").data, lines: [] } });
    const client = mount();

    await waitFor(() => expect(client.getQueryState(["now"])?.status).toBe("success"));
    expect(screen.queryByTestId("now-strip")).toBeNull();
  });

  it("marks an unknown line and keeps its last-known text", async () => {
    getNow.mockResolvedValue(card("Unknown (last known: next 16:00, as of 14:10)", { state: "unknown" }));
    mount();

    expect((await screen.findByTestId("now-strip")).textContent).toContain("Unknown (last known");
    expect(screen.getByTestId("now-strip").textContent).not.toMatch(/free/i);
  });

  it("announces only a change of the line, not the first read", async () => {
    getNow.mockResolvedValue(card("Free until 14:00"));
    const client = mount();
    await screen.findByTestId("now-strip");
    expect(screen.getByTestId("now-strip-live").textContent).toBe("");

    getNow.mockResolvedValue(card("In meeting until 15:00; next 16:00"));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["now"] });
    });
    await waitFor(() =>
      expect(screen.getByTestId("now-strip-live").textContent).toBe(
        "In meeting until 15:00; next 16:00",
      ),
    );
    expect(screen.getByTestId("now-strip-live").getAttribute("aria-live")).toBe("polite");
  });

  it("renders no live region when announce is off, and a below-md one otherwise", async () => {
    getNow.mockResolvedValue(card("Free until 14:00"));
    mount(undefined, false);
    await screen.findByTestId("now-strip");
    expect(screen.queryByTestId("now-strip-live")).toBeNull();
    cleanup();

    mount(undefined, "below-md");
    await screen.findByTestId("now-strip");
    expect(screen.getByTestId("now-strip-live").className).toContain("md:hidden");
  });
});

describe("msUntilNextBoundary", () => {
  it("is the soonest of a line's expiry and local midnight, never under five seconds", () => {
    const now = new Date(2026, 10, 26, 10, 0, 0);
    const soon = new Date(2026, 10, 26, 10, 30, 0).toISOString();
    expect(msUntilNextBoundary(card("x", { fresh_until: soon }).data, now)).toBe(30 * 60_000);
    expect(msUntilNextBoundary(card("x", { fresh_until: "2020-01-01T00:00:00Z" }).data, now)).toBe(5000);
    expect(msUntilNextBoundary(undefined, now)).toBe(14 * 60 * 60_000);
  });
});

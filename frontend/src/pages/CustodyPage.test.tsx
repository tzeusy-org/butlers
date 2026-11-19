// @vitest-environment jsdom
/**
 * <CustodyPage>: the owner's holds. An outage never renders as "nothing held"; a hold names only a
 * kind of channel; release and replace post the generation the owner saw and a command id.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

import CustodyPage from "@/pages/CustodyPage.tsx";
import { getShellCapability } from "@/lib/shell-capability.ts";

const server = vi.hoisted(() => ({
  holds: [] as unknown[],
  fail: false,
  posts: [] as { path: string; body: unknown }[],
}));

vi.mock("@/api/client.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  return {
    ...actual,
    apiFetch: vi.fn(async (path: string, init?: { method?: string; body?: string }) => {
      if (server.fail) throw new actual.ApiError("x", "no", 503);
      if (init?.method === "POST") {
        server.posts.push({ path, body: JSON.parse(init.body ?? "{}") });
        return { data: { outcome: "released" } };
      }
      return { data: { holds: server.holds } };
    }),
  };
});

const hold = {
  hold_id: "11111111-1111-4111-8111-111111111111",
  target_kind: "has-email",
  generation: 2,
  episode_id: "22222222-2222-4222-8222-222222222222",
  reason: "lost",
  held_from: "2030-01-01T00:00:00Z",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CustodyPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  server.holds = [];
  server.fail = false;
  server.posts = [];
});
afterEach(cleanup);

describe("CustodyPage", () => {
  it("is in the navigation", () => {
    expect(getShellCapability("/custody")?.discoverability).toBe("global");
  });

  it("says nothing is held only when the list loaded and is empty", async () => {
    renderPage();
    await screen.findByText(/No channels are held/);
  });

  it("is unavailable, not clear, when holds cannot be read", async () => {
    server.fail = true;
    renderPage();
    await screen.findByText(/Holds are unavailable/);
    expect(screen.queryByText(/No channels are held/)).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("shows a kind of channel and links the case, never an address", async () => {
    server.holds = [hold];
    renderPage();
    await screen.findByText(/An email address is held/);
    const link = screen.getByRole("link", { name: /recovery checklist/ });
    expect(link.getAttribute("href")).toBe(`/custody/${hold.episode_id}`);
    expect(document.body.textContent).not.toMatch(/@/);
  });

  it("releases only after confirmation, with the seen generation and a command id", async () => {
    server.holds = [hold];
    renderPage();
    await screen.findByText(/An email address is held/);
    fireEvent.click(screen.getByRole("button", { name: "Release" }));
    expect(server.posts).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm release" }));
    await waitFor(() => expect(server.posts).toHaveLength(1));
    expect(server.posts[0].path).toBe(`/custody/holds/${hold.hold_id}/release`);
    expect(server.posts[0].body).toMatchObject({ expected_generation: 2 });
    expect(typeof (server.posts[0].body as { command_id: string }).command_id).toBe("string");
  });
});

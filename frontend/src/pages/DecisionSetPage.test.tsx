// @vitest-environment jsdom
/**
 * <DecisionSetPage> through the real hooks and API module: it renders the server's view and posts
 * only the version, the verdict and the ticked items' ids with the digests it was shown. It never
 * decides decidability or the default selection itself.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";

import DecisionSetPage from "@/pages/DecisionSetPage.tsx";

const SET_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const E1 = "11111111-1111-4111-8111-111111111111";
const E2 = "22222222-2222-4222-8222-222222222222";
const E3 = "33333333-3333-4333-8333-333333333333";

const server = vi.hoisted(() => ({
  view: null as unknown,
  failure: null as null | { code: string; status: number },
  posts: [] as { path: string; body: Record<string, unknown> }[],
  postFailure: null as null | { status: number; detail: unknown },
  afterPost: null as null | (() => void),
  reads: 0,
}));

vi.mock("@/api/client.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  return {
    ...actual,
    apiFetch: vi.fn(async (path: string, init?: { method?: string; body?: string }) => {
      if (init?.method === "POST") {
        server.posts.push({ path, body: JSON.parse(init.body ?? "{}") });
        server.afterPost?.();
        if (server.postFailure) {
          throw new actual.ApiError("http_409", "refused", server.postFailure.status, server.postFailure.detail);
        }
        return {
          data: {
            status: "approved",
            set_id: SET_ID,
            version: 1,
            outcomes: [{ effect_id: E1, outcome: "approved", status: "committed" }],
          },
        };
      }
      server.reads += 1;
      if (server.failure) throw new actual.ApiError(server.failure.code, "no", server.failure.status);
      return { data: server.view, meta: {} };
    }),
  };
});

function item(position: number, effect: string, over: Record<string, unknown> = {}) {
  return {
    position,
    effect_id: effect,
    capability: "messenger__notify",
    explicit_only: false,
    in_default_selection: true,
    item_digest: `${position}`.repeat(64),
    recipient_channel: "telegram",
    exact: true,
    reason: null,
    title: `Send message ${position + 1}`,
    facts: [{ label: "Message", value: `Hello ${position}` }],
    claims: [{ label: "Why", value: "Owner asked" }],
    lineage: { facts: [{ label: "Origin", value: "Owner" }] },
    ...over,
  };
}

function setView(items: unknown[], over: Record<string, unknown> = {}) {
  return {
    set_id: SET_ID,
    version: 1,
    goal_id: "g",
    state: "open",
    expires_at: "2030-01-01T00:00:00Z",
    digest: "d".repeat(64),
    items,
    decidable: true,
    decisions_live: true,
    reason: null,
    unticked_outcome: "left",
    ...over,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/decision-sets/${SET_ID}`]}>
        <Routes>
          <Route path="/decision-sets/:setId" element={<DecisionSetPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  server.view = setView([item(0, E1), item(1, E2), item(2, E3, { explicit_only: true, in_default_selection: false })]);
  server.failure = null;
  server.posts = [];
  server.postFailure = null;
  server.afterPost = null;
  server.reads = 0;
});
afterEach(cleanup);

const tick = (name: RegExp) => screen.getByRole("checkbox", { name });

describe("DecisionSetPage", () => {
  it("pre-ticks exactly what the server marks and never an explicit-only item", async () => {
    renderPage();
    await screen.findByText(/3 calls waiting/);
    expect(tick(/Send message 1/).getAttribute("aria-checked")).toBe("true");
    expect(tick(/Send message 2/).getAttribute("aria-checked")).toBe("true");
    expect(tick(/Send message 3/).getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(/decided only when you tick it yourself/)).toBeTruthy();
  });

  it("labels the assistant's words as claims apart from the server facts", async () => {
    renderPage();
    await screen.findByText(/3 calls waiting/);
    const claims = screen.getAllByRole("region", { name: /Assistant says \(claims, not verified\)/ });
    expect(within(claims[0]).getByText("Owner asked")).toBeTruthy();
    const call = screen.getAllByRole("region", { name: "The call" })[0];
    expect(within(call).getByText("Hello 0")).toBeTruthy();
    expect(screen.getAllByRole("region", { name: /Parked by a goal/ }).length).toBe(3);
  });

  it("shows markup in a value as inert text", async () => {
    server.view = setView([
      item(0, E1, { facts: [{ label: "Message", value: "<img src=x onerror=alert(1)> [a](http://x)" }] }),
    ]);
    const { container } = renderPage();
    await screen.findByText(/1 call waiting/);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a[href='http://x']")).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeTruthy();
  });

  it.each([
    ["left", /stay waiting for your decision/],
    ["rejected", /are rejected/],
    ["whatever", /are not approved/],
  ])("words the unticked outcome %s from the server value", async (outcome, expected) => {
    server.view = setView([item(0, E1)], { unticked_outcome: outcome });
    renderPage();
    await screen.findByText(expected);
  });

  it("posts only the version, the verdict and the ticked items with the digests shown", async () => {
    renderPage();
    await screen.findByText(/3 calls waiting/);
    fireEvent.click(tick(/Send message 2/));
    fireEvent.click(screen.getByRole("button", { name: "Approve 1 ticked" }));
    await screen.findByText("Approved.");
    expect(server.posts).toEqual([
      {
        path: `/decision-sets/${SET_ID}/decision`,
        body: {
          version: 1,
          decision: "approve",
          selected: [{ effect_id: E1, item_digest: "0".repeat(64) }],
        },
      },
    ]);
  });

  it("sends an explicit-only item only when the owner ticks it", async () => {
    renderPage();
    await screen.findByText(/3 calls waiting/);
    fireEvent.click(tick(/Send message 3/));
    fireEvent.click(screen.getByRole("button", { name: "Reject 3 ticked" }));
    await screen.findByText("Approved.");
    const selected = server.posts[0].body.selected as { effect_id: string }[];
    expect(selected.map((p) => p.effect_id)).toEqual([E1, E2, E3]);
    expect(server.posts[0].body.decision).toBe("reject");
  });

  it("offers no decision with nothing ticked", async () => {
    renderPage();
    await screen.findByText(/3 calls waiting/);
    fireEvent.click(tick(/Send message 1/));
    fireEvent.click(tick(/Send message 2/));
    expect((screen.getByRole("button", { name: "Approve 0 ticked" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("offers no controls for a set the server says is not decidable", async () => {
    server.view = setView(
      [item(0, E1, { exact: false, reason: "changed", title: null, facts: [], claims: [], lineage: null, in_default_selection: false })],
      { decidable: false, reason: "items" },
    );
    renderPage();
    await screen.findByText(/cannot be shown exactly/);
    expect(screen.queryByRole("button", { name: /ticked/ })).toBeNull();
    expect((tick(/Cannot be shown here/) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/changed after the assistant parked it/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /approval page/ }).getAttribute("href")).toBe(`/approvals/${E1}`);
  });

  it("says nothing was decided on a refusal and reads the set again", async () => {
    server.postFailure = { status: 409, detail: { error: "decision_set_refused", reason: "stale" } };
    renderPage();
    await screen.findByText(/3 calls waiting/);
    const before = server.reads;
    fireEvent.click(screen.getByRole("button", { name: "Approve 2 ticked" }));
    await screen.findByText(/changed while you were reviewing it. Nothing was decided/);
    await waitFor(() => expect(server.reads).toBeGreaterThan(before));
  });

  it.each([
    [404, /not found/],
    [503, /not switched on/],
    [500, /could not be loaded/],
  ])("says plainly when the set cannot be read (%s)", async (status, expected) => {
    server.failure = { code: "x", status };
    renderPage();
    await screen.findByText(expected);
  });

  it("renders no ticks and no buttons while deciding is not switched on", async () => {
    server.view = setView(
      [item(0, E1), item(1, E2)],
      { decisions_live: false },
    );
    renderPage();
    await screen.findByText(/2 calls waiting/);
    expect(screen.getByText(/Deciding sets is not switched on/)).toBeTruthy();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /ticked/ })).toBeNull();
    // The server's facts are still readable.
    expect(screen.getByText("Hello 0")).toBeTruthy();
  });

  it("resets the ticks for a re-read version and posts only the items it renders", async () => {
    const v2 = setView([item(1, E2, { item_digest: "9".repeat(64) }), item(3, "44444444-4444-4444-8444-444444444444")], {
      version: 2,
      digest: "e".repeat(64),
    });
    server.view = setView([item(0, E1), item(1, E2)]);
    server.postFailure = { status: 409, detail: { error: "decision_set_refused", reason: "stale" } };
    server.afterPost = () => {
      server.view = v2;
    };
    renderPage();
    await screen.findByText(/2 calls waiting/);
    // The owner unticks the second item of version 1, then submits and is refused as stale.
    fireEvent.click(tick(/Send message 2/));
    fireEvent.click(screen.getByRole("button", { name: "Approve 1 ticked" }));
    await screen.findByText(/changed while you were reviewing it/);
    // Version 2 shows both of its items ticked again, whatever the owner had unticked in version 1.
    await waitFor(() => expect(screen.getAllByRole("checkbox")).toHaveLength(2));
    expect(tick(/Send message 2/).getAttribute("aria-checked")).toBe("true");
    expect(tick(/Send message 4/).getAttribute("aria-checked")).toBe("true");
    server.postFailure = null;
    fireEvent.click(screen.getByRole("button", { name: "Approve 2 ticked" }));
    await screen.findByText("Approved.");
    expect(server.posts[1].body).toEqual({
      version: 2,
      decision: "approve",
      selected: [
        { effect_id: E2, item_digest: "9".repeat(64) },
        { effect_id: "44444444-4444-4444-8444-444444444444", item_digest: "3".repeat(64) },
      ],
    });
  });
});

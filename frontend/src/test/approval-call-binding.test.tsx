// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";
import ApprovalsPage from "@/pages/ApprovalsPage";
import type { ApprovalDetail } from "@/api/types";

// The live bus is unrelated to this HTTP/query/keyboard boundary.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", health: "healthy", lastEventAt: null, subscribe: () => () => {} }),
}));
import { ApprovalCallReview, useApprovalDecisionMutations } from "@/hooks/use-approval-decisions";

const shown = { tool_name: "telegram_send_message", args_digest: "a".repeat(64) };
const waitingKey = ["approvals", "flat", "waiting"];
function Harness() {
  const decisions = useApprovalDecisionMutations({ undoWindow: true });
  return <>
    <button onClick={() => void decisions.requestReview("action-1")}>Review action</button>
    <ApprovalCallReview review={decisions.review} onCancel={decisions.cancelReview}
      onConfirm={(view) => {
        decisions.cancelReview();
        decisions.scheduleDecision(view.id, "approve", () => decisions.approveMut.mutate(view));
      }} />
  </>;
}
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(waitingKey, { data: [{ id: "action-1" }], meta: {} });
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  return client;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("actual approval HTTP snapshot handoff", () => {
  it("keeps the shown snapshot through undo and rolls back a stale refusal", async () => {
    const posts: { displayed_call: typeof shown }[] = [];
    let changed = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
      if (init?.method === "POST") {
        posts.push(JSON.parse(String(init.body)));
        return new Response(JSON.stringify({ detail: "stored call changed; nothing executed" }), { status: 409 });
      }
      return new Response(JSON.stringify({ data: {
        id: "action-1", status: "pending", call_preview: {
          title: "Send Telegram message", facts: [{ label: "Message", value: "Owner reviewed A" }],
          displayed_call: changed ? { ...shown, args_digest: "b".repeat(64) } : shown,
        },
      } }));
    });
    const client = mount();
    fireEvent.click(screen.getByText("Review action"));
    await screen.findByText("Owner reviewed A");
    expect(posts).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Approve reviewed call" }));
    changed = true;
    expect(posts).toHaveLength(0);
    await waitFor(() => expect(posts).toHaveLength(1), { timeout: 6500 });
    expect(posts[0]).toEqual({ displayed_call: shown });
    await waitFor(() => expect(client.getQueryData(waitingKey)).toEqual({ data: [{ id: "action-1" }], meta: {} }));
    client.clear();
  }, 10_000);

  it("does not offer approval for an unshowable call, or send on cancelled review", async () => {
    let preview: unknown = null;
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ data: {
      id: "action-1", status: "pending", call_preview: preview,
    } })));
    const client = mount();
    fireEvent.click(screen.getByText("Review action"));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "Approve reviewed call" })).toBeNull();
    preview = { title: "Send message", facts: [{ label: "Message", value: "Full body" }], displayed_call: shown };
    fireEvent.click(screen.getByText("Review action"));
    await screen.findByText("Full body");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetch.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
    await act(async () => client.clear());
  });
});


// Exercise the real page, query cache, API client and keyboard registration.
// A cached call is not a shown call while the source-bound dossier is hidden.
describe("dossier keyboard visibility at the actual HTTP boundary", () => {
  const detail: ApprovalDetail = {
    id: "action-1", title: "Send message", butler: "general", status: "pending",
    created_at: "2026-10-01T12:00:00Z",
    proposed_action: { tool_name: shown.tool_name, tool_args: {} },
    call_preview: {
      title: "Send Telegram message", facts: [{ label: "Message", value: "Cached safe body" }],
      displayed_call: shown,
    },
  };
  const detailKey = ["approvals", "detail", detail.id, "review-source", "messenger"];

  async function flush() {
    for (let i = 0; i < 8; i++) await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  }

  function mountPage() {
    vi.useFakeTimers();
    const posts: unknown[] = [];
    const reads: string[] = [];
    let finishDetail!: (response: Response) => void;
    const pendingDetail = new Promise<Response>((resolve) => { finishDetail = resolve; });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = new URL(String(input), "http://localhost");
      if (init?.method === "POST") {
        posts.push(JSON.parse(String(init.body)));
        return new Response(JSON.stringify({ detail: "test dispatch refused" }), { status: 409 });
      }
      if (url.pathname === "/api/approvals/action-1") {
        reads.push(url.search);
        return (await pendingDetail).clone();
      }
      if (url.pathname === "/api/approvals") return new Response(JSON.stringify({
        data: [{ id: detail.id, butler: detail.butler, tool_name: shown.tool_name,
          status: "pending", created_at: detail.created_at }], meta: {},
      }));
      // Independent page sections are explicitly unavailable in this fixture.
      return new Response(JSON.stringify({ detail: "Unrelated test source unavailable" }), { status: 503 });
    });
    const client = new QueryClient({ defaultOptions: {
      queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
    } });
    client.setQueryData(detailKey, { data: detail, meta: {} });
    render(<MemoryRouter initialEntries={["/approvals/action-1?review_source=messenger"]}>
      <QueryClientProvider client={client}><Routes>
        <Route path="/approvals/:id" element={<ApprovalsPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
      </Routes></QueryClientProvider>
    </MemoryRouter>);
    return { client, posts, reads, finishDetail };
  }

  afterEach(() => { vi.useRealTimers(); });

  it("refuses cached-hidden keyboard approval during both fetching and failed refresh", async () => {
    const { client, posts, reads, finishDetail } = mountPage();
    try {
      await flush();
      expect(reads).toEqual(["?review_source=messenger"]);
      expect(screen.getByTestId("approval-dossier-pane").textContent).toBe("loading…");
      expect(screen.queryByRole("region", { name: "Stored call preview" })).toBeNull();
      expect(screen.queryByText("Cached safe body")).toBeNull();
      fireEvent.keyDown(window, { key: "a" });
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      expect(posts).toEqual([]);

      await act(async () => { finishDetail(new Response(JSON.stringify({ detail: "Source unavailable" }), { status: 503 })); });
      await flush();
      expect(screen.getByTestId("approval-dossier-pane").textContent).toBe("failed to load dossier");
      expect(client.getQueryData(detailKey)).toEqual({ data: detail, meta: {} });
      fireEvent.keyDown(window, { key: "a" });
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      expect(posts).toEqual([]);
    } finally { cleanup(); client.clear(); }
  });

  it("keeps visible keyboard approval undoable and retains the scheduled shown call", async () => {
    const { client, posts, finishDetail } = mountPage();
    try {
      await flush();
      await act(async () => { finishDetail(new Response(JSON.stringify({ data: detail, meta: {} }))); });
      await flush();
      expect(screen.getByText("Cached safe body")).toBeTruthy();
      fireEvent.keyDown(window, { key: "a" });
      await flush();
      expect(posts).toEqual([]);
      // Scheduling the last row advances selection; its rail Undo remains real.
      fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      expect(posts).toEqual([]);
      fireEvent.click(screen.getByTestId("rail-item"));
      await flush();
      expect(screen.getByText("Cached safe body")).toBeTruthy();
      fireEvent.keyDown(window, { key: "a" });
      await flush();
      expect(screen.getByRole("button", { name: /Undo/ })).toBeTruthy();
      // A later cache update cannot replace the view already handed to Undo.
      await act(async () => { client.setQueriesData({ queryKey: ["approvals", "detail", detail.id] }, { data: { ...detail,
        call_preview: { ...detail.call_preview!, displayed_call: { ...shown, args_digest: "c".repeat(64) } },
      }, meta: {} }); });
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      expect(posts).toEqual([{ displayed_call: shown }]);
    } finally { cleanup(); client.clear(); }
  });
});

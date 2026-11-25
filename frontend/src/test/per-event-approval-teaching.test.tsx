// @vitest-environment jsdom
/**
 * RFC 0038 D13 at the real ApprovalsPage, query cache and API client: approving an
 * always_per_event action (rule_eligible false) never opens the "Always allow this shape"
 * teaching digest, while an ordinary action still does. Synthetic data only; fetch is the boundary.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApprovalDetail } from "@/api/types";
import ApprovalsPage from "@/pages/ApprovalsPage";

vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", health: "healthy", lastEventAt: null, subscribe: () => () => {} }),
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const TOOL = "calendar_respond";

function detail(ruleEligible: boolean): ApprovalDetail {
  return {
    id: "action-1",
    title: "Calendar Respond (relationship)",
    butler: "relationship",
    status: "pending",
    created_at: "2026-11-01T08:30:00Z",
    why: "A synthetic invitation awaits a reply.",
    rule_eligible: ruleEligible,
    proposed_action: { tool_name: TOOL, agent_summary: "Reply to a synthetic invitation" },
    call_preview: {
      title: "Reply to a synthetic invitation",
      facts: [{ label: "Response", value: "accepted" }],
      displayed_call: { tool_name: TOOL, args_digest: "d".repeat(64) },
    },
    external_proposal_review: null,
  };
}

function mountPage(shownDetail: ApprovalDetail) {
  const posts: string[] = [];
  const reads: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    reads.push(url.pathname);
    if (init?.method === "POST") {
      posts.push(url.pathname);
      return new Response(JSON.stringify({ data: {
        id: "action-1", butler: "relationship", tool_name: TOOL, tool_args: {},
        status: "executed", requested_at: shownDetail.created_at, dispatched: true,
      } }));
    }
    if (url.pathname === "/api/approvals/action-1") {
      return new Response(JSON.stringify({ data: shownDetail }));
    }
    if (url.pathname === "/api/approvals") return new Response(JSON.stringify({
      data: [{ id: "action-1", butler: "relationship", tool_name: TOOL,
        status: "pending", created_at: shownDetail.created_at }], meta: {},
    }));
    return new Response(JSON.stringify({ detail: "Unrelated test source unavailable" }), { status: 503 });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <MemoryRouter initialEntries={["/approvals/action-1"]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/approvals/:id" element={<ApprovalsPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { posts, reads };
}

async function approve(posts: string[]) {
  const button = await screen.findByRole("button", { name: "Approve" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  await userEvent.setup().click(button);
  await waitFor(() => expect(posts).toHaveLength(1));
  await new Promise((resolve) => setTimeout(resolve, 50));
}

describe("per-event approval teaching prompt", () => {
  it("offers no standing rule after approving an always_per_event action", async () => {
    const { posts, reads } = mountPage(detail(false));
    await approve(posts);

    expect(screen.queryByRole("region", { name: "Teach this approval" })).toBeNull();
    expect(screen.queryByText(/always allow/i)).toBeNull();
    expect(reads.some((path) => path.includes("/rules/suggestions/"))).toBe(false);
  });

  it("still offers the prompt for an ordinary action", async () => {
    const { posts, reads } = mountPage(detail(true));
    await approve(posts);

    await waitFor(() =>
      expect(reads.some((path) => path.includes("/rules/suggestions/"))).toBe(true),
    );
  });
});

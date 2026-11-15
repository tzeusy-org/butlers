// @vitest-environment jsdom
/**
 * REQ-dashboard-approvals-904 at the real ApprovalsPage, query cache and API client:
 * the dossier of an external MCP proposal approves only with the typed review's
 * displayed call, offers no standing rule, shows no raw arguments, and keeps Reject
 * when an entity is unresolvable. Synthetic data only; fetch is the boundary.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApprovalDetail, ExternalProposalReview } from "@/api/types";
import ApprovalsPage from "@/pages/ApprovalsPage";

vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", health: "healthy", lastEventAt: null, subscribe: () => () => {} }),
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const shown = { tool_name: "relationship_assert_fact", args_digest: "c".repeat(64) };
const URL_TEXT = "https://example.com/synthetic/evidence?ref=1";

function detail(review: Partial<ExternalProposalReview> = {}): ApprovalDetail {
  return {
    id: "action-1",
    title: "Relationship Assert Fact (relationship)",
    butler: "relationship",
    status: "pending",
    created_at: "2026-11-01T08:30:00Z",
    why: "An external AI client proposed a 'works-at' relationship.",
    proposed_action: {
      tool_name: "relationship_assert_fact",
      agent_summary: "External proposal: relationship fact 'works-at'",
    },
    call_preview: null,
    external_proposal_review: {
      proposal_id: "11111111-1111-4111-8111-111111111111",
      client_label: "synthetic-client",
      subject: { id: "22222222-2222-4222-8222-222222222222", name: "Synthetic Person A", entity_type: "person", status: "live" },
      predicate: "works-at",
      object: { id: "33333333-3333-4333-8333-333333333333", name: "Synthetic Org B", entity_type: "organization", status: "live" },
      note: "Synthetic note.",
      evidence_url: URL_TEXT,
      authority_statement: "An external AI client proposed this fact; neither you nor your butlers did.",
      unresolvable_entity: false,
      approve_unavailable_reason: null,
      displayed_call: shown,
      ...review,
    },
  };
}

function mountPage(shownDetail: ApprovalDetail) {
  const posts: { path: string; body: unknown }[] = [];
  const reads: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    reads.push(url.pathname);
    if (init?.method === "POST") {
      posts.push({ path: url.pathname, body: JSON.parse(String(init.body ?? "{}")) });
      const status = url.pathname.endsWith("/approve") ? "executed" : "rejected";
      return new Response(JSON.stringify({ data: {
        id: "action-1", butler: "relationship", tool_name: shown.tool_name, tool_args: {},
        status, requested_at: shownDetail.created_at, dispatched: status === "executed",
      } }));
    }
    if (url.pathname === "/api/approvals/action-1") {
      return new Response(JSON.stringify({ data: shownDetail }));
    }
    if (url.pathname === "/api/approvals") return new Response(JSON.stringify({
      data: [{ id: "action-1", butler: "relationship", tool_name: shown.tool_name,
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

describe("external proposal dossier", () => {
  // Scenarios: who and what; no raw tool_args; approval submits the displayed call;
  // no rule can be created from the review.
  it("approves with the exact displayed call and offers no standing rule", async () => {
    const { posts, reads } = mountPage(detail());
    const review = await screen.findByRole("region", { name: "External proposal review" });
    expect(review.textContent).toContain("Synthetic Person A");
    expect(screen.queryByText(/Open only/)).toBeNull();
    expect(document.querySelector("pre")).toBeNull();
    expect(screen.queryByRole("link", { name: /example\.com/ })).toBeNull();

    const approve = screen.getByRole("button", { name: "Approve" });
    expect((approve as HTMLButtonElement).disabled).toBe(false);
    await userEvent.setup().click(approve);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ path: "/api/approvals/action-1/approve", body: { displayed_call: shown } });

    // The teaching digest ("Always allow this shape?") never opens for an outside proposal.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("region", { name: "Teach this approval" })).toBeNull();
    expect(screen.queryByText(/always allow/i)).toBeNull();
    expect(reads.some((path) => path.includes("/rules/suggestions/"))).toBe(false);
  });

  // Scenario: Unresolvable entity blocks approval but not rejection; keyboard and names.
  it("disables Approve with the stated reason and keeps Deny reachable by keyboard", async () => {
    const reason =
      "The subject entity 22222222-2222-4222-8222-222222222222 was merged into another entity (merged).";
    const { posts } = mountPage(detail({
      unresolvable_entity: true,
      displayed_call: null,
      approve_unavailable_reason: reason,
      subject: { id: "22222222-2222-4222-8222-222222222222", name: "Synthetic Person A", entity_type: "person", status: "merged" },
    }));
    await screen.findByRole("region", { name: "External proposal review" });
    const approve = screen.getByRole("button", { name: "Approve" });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(approve.getAttribute("aria-describedby")).toBe("external-proposal-approve-unavailable");
    expect(document.getElementById("external-proposal-approve-unavailable")?.textContent).toContain(reason);

    const deny = screen.getByRole("button", { name: "Deny" });
    deny.focus();
    expect(document.activeElement).toBe(deny);
    await userEvent.setup().keyboard("{Enter}");
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/api/approvals/action-1/deny");
  });
});

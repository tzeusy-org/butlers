// @vitest-environment jsdom
/**
 * REQ-dashboard-approvals-902/903 at the real ApprovalsPage, query cache and API
 * client: a parked entity merge is approved only after the structural comparison
 * of exactly its stored pair renders, through one accessible confirmation that
 * submits the original displayed call. Synthetic data only; fetch is the boundary.
 */
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApprovalDetail, CompareEntitiesResponse, EntityMergeReview } from "@/api/types";
import { ApprovalCallReview } from "@/hooks/use-approval-decisions";
import ApprovalsPage from "@/pages/ApprovalsPage";

vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", health: "healthy", lastEventAt: null, subscribe: () => () => {} }),
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const SOURCE = "28039ce4-c849-49a4-a7bf-c951b68fcb41";
const TARGET = "22b6c972-626e-49b8-be9b-c13f8b882573";
const shown = { tool_name: "entity_merge", args_digest: "d".repeat(64) };
const review: EntityMergeReview = { source_entity_id: SOURCE, target_entity_id: TARGET, displayed_call: shown };

function detail(overrides: Partial<ApprovalDetail> = {}): ApprovalDetail {
  return {
    id: "action-1",
    title: "Memory Entity Merge (relationship)",
    butler: "relationship",
    status: "pending",
    created_at: "2026-11-01T08:30:00Z",
    why: "Entity dedup curation detected a potential duplicate entity pair.",
    proposed_action: { tool_name: "entity_merge", agent_summary: "Merge duplicate entity" },
    call_preview: null,
    entity_merge_review: review,
    ...overrides,
  };
}

function block(id: string, type: string) {
  return {
    entity: { id, canonical_name: "Synthetic Same Name", entity_type: type, aliases: [], tier: null, state: "active" },
    identity_facts: [],
    narrative_facts: [],
  };
}

const diff: CompareEntitiesResponse = {
  a: block(SOURCE, "person"),
  b: block(TARGET, "organization"),
  shared: [],
  divergent: [],
};

type CompareReply = () => Promise<Response> | Response;

function mountPage({ compare = () => new Response(JSON.stringify(diff)) }: { compare?: CompareReply } = {}) {
  const posts: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/relationship/entities/compare") {
      posts.push({ path: url.pathname, body: JSON.parse(String(init?.body ?? "{}")) });
      return compare();
    }
    if (init?.method === "POST") {
      posts.push({ path: url.pathname, body: JSON.parse(String(init.body ?? "{}")) });
      const status = url.pathname.endsWith("/approve") ? "executed" : "rejected";
      return new Response(JSON.stringify({ data: {
        id: "action-1", butler: "relationship", tool_name: shown.tool_name, tool_args: {},
        status, requested_at: "2026-11-01T08:30:00Z", dispatched: status === "executed",
      } }));
    }
    if (url.pathname === "/api/approvals/action-1") return new Response(JSON.stringify({ data: detail() }));
    if (url.pathname === "/api/approvals") return new Response(JSON.stringify({
      data: [{ id: "action-1", butler: "relationship", tool_name: shown.tool_name,
        status: "pending", created_at: "2026-11-01T08:30:00Z" }], meta: {},
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
  const decisions = () => posts.filter((post) => !post.path.endsWith("/entities/compare"));
  return { posts, decisions };
}

function approveButton() {
  return screen.getByRole("button", { name: /Approve merge/ }) as HTMLButtonElement;
}

describe("parked entity merge dossier", () => {
  // Scenarios: identical names remain distinguishable; final confirmation is
  // accessible and cancelable; confirmation resolves the original call once.
  it("reviews the stored pair, then confirms once with the original displayed call", async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const { posts, decisions } = mountPage({
      compare: async () => { await gate; return new Response(JSON.stringify(diff)); },
    });

    await screen.findByTestId("merge-review-loading");
    expect(approveButton().disabled).toBe(true);
    expect(screen.queryByText(/Open only/)).toBeNull();
    await act(async () => { release?.(); });

    const source = await screen.findByTestId("merge-review-source");
    const target = screen.getByTestId("merge-review-target");
    expect(source.textContent).toContain("Absorb source");
    expect(source.textContent).toContain("person");
    expect(source.textContent).toContain(SOURCE);
    expect(target.textContent).toContain("Keep target");
    expect(target.textContent).toContain("organization");
    expect(target.textContent).toContain(TARGET);
    expect(screen.getByText("No shared identifiers.")).toBeTruthy();
    expect(screen.getByText("No conflicting facts.")).toBeTruthy();
    expect(screen.getByTestId("merge-review-consequences").textContent).toContain(
      "The surviving record stays a organization",
    );
    // Direction is fixed: no survivor picker.
    expect(screen.queryByRole("radio")).toBeNull();
    expect(posts[0]).toEqual({
      path: "/api/relationship/entities/compare",
      body: { entity_a: SOURCE, entity_b: TARGET },
    });

    const user = userEvent.setup();
    await user.click(approveButton());
    let dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain(SOURCE);
    expect(dialog.textContent).toContain(TARGET);
    expect(dialog.textContent).toContain("Survives");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await user.click(approveButton());
    dialog = await screen.findByRole("alertdialog");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(decisions()).toEqual([]);

    await user.click(approveButton());
    dialog = await screen.findByRole("alertdialog");
    const confirm = within(dialog).getByRole("button", { name: "Confirm merge" });
    await user.dblClick(confirm);
    await waitFor(() => expect(decisions()).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(decisions()).toEqual([
      { path: "/api/approvals/action-1/approve", body: { displayed_call: shown } },
    ]);
    expect(posts.some((post) => post.path.includes("/entities/merge"))).toBe(false);
    // No "Always allow this shape" prompt: a merge never becomes a standing rule.
    expect(screen.queryByRole("region", { name: "Teach this approval" })).toBeNull();
    expect(screen.queryByText(/always allow/i)).toBeNull();
  });

  // Scenarios: comparison failure offers recovery without approval; Deny works
  // while the comparison is unavailable.
  it("keeps approval unavailable on a failed comparison and still denies", async () => {
    let fail = true;
    const { decisions } = mountPage({
      compare: () => fail
        ? new Response(JSON.stringify({ detail: "Entity not found" }), { status: 404 })
        : new Response(JSON.stringify(diff)),
    });
    const failure = await screen.findByTestId("merge-review-error");
    expect(within(failure).getByRole("alert").textContent).toContain("no longer exists");
    expect(approveButton().disabled).toBe(true);

    const user = userEvent.setup();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry comparison" }));
    await screen.findByTestId("merge-review-source");
    expect(decisions()).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Deny" }));
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].path).toBe("/api/approvals/action-1/deny");
  });

  // Scenario: a comparison for another (here, the inverse) pair never unlocks this call.
  it("refuses a comparison that does not describe the stored direction", async () => {
    mountPage({ compare: () => new Response(JSON.stringify({ ...diff, a: diff.b, b: diff.a })) });
    await screen.findByTestId("merge-review-error");
    expect(approveButton().disabled).toBe(true);
  });

  // Scenario: every dashboard entry uses the reviewed operation (keyboard).
  it("opens the same confirmation from the keyboard instead of scheduling a decision", async () => {
    const { decisions } = mountPage();
    await screen.findByTestId("merge-review-source");
    const user = userEvent.setup();
    await user.keyboard("a");
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain(TARGET);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(decisions()).toEqual([]);
  });
});

describe("summary-surface merge review", () => {
  it("shows the comparison and hands back the reviewed call only after confirmation", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(diff)));
    const onConfirm = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ApprovalCallReview review={{ id: "action-1", merge: review }} onConfirm={onConfirm} onCancel={() => {}} />
      </QueryClientProvider>,
    );
    await screen.findByTestId("merge-review-source");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Approve merge…" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(onConfirm).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Confirm merge" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0]).toMatchObject({ id: "action-1", displayed_call: shown });
  });
});

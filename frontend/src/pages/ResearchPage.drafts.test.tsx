// @vitest-environment jsdom
// REQ-dashboard-shell-001/006: the production owner stays enrolled when storage fails.
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { getShellCapability } from "@/lib/shell-capability";

let client: QueryClient | undefined;
afterEach(() => { cleanup(); client?.clear(); vi.unstubAllGlobals(); });

it("keeps routed research submission success distinct from unconfirmed browser deletion", async () => {
  const posts: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path === "/api/auth/owner/csrf") return Response.json({ csrf_token: "research-test-csrf", csrf_expires_at: "2099-01-01T00:00:00Z" });
    expect(path).toBe("/api/health/research");
    if (options?.method === "POST") {
      posts.push(JSON.parse(options.body as string));
      return Response.json({ id: "created-research" });
    }
    return Response.json({ data: [], meta: { total: 0, has_more: false } });
  }));
  const { default: Page } = await getShellCapability("/health/research")!.loader();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Page /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Add research/ }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "A research subject" } });
  fireEvent.change(screen.getByLabelText("Content"), { target: { value: "The complete research notes" } });
  expect(await screen.findByText("Draft saving unavailable. Keep this tab open.")).toBeTruthy();
  const submit = screen.getByRole("button", { name: "Add research" });
  fireEvent.click(submit);
  await waitFor(() => expect(posts).toEqual([{ title: "A research subject", content: "The complete research notes", tags: [], source_url: null }]));
  expect(await screen.findByText("Browser draft deletion unconfirmed")).toBeTruthy();
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(submit).toHaveProperty("disabled", true);
  fireEvent.click(submit);
  expect(posts).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Retry discard" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close with deletion unconfirmed" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(posts).toHaveLength(1);
});

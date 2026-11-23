// @vitest-environment jsdom
/**
 * <SecurityQuestionPage> through the real API module: yes posts nothing else, no posts only the
 * ticked fact ids the server listed with one command id per page view, and the page never answers
 * by itself.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";

import SecurityQuestionPage from "@/pages/SecurityQuestionPage.tsx";

const EVENT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const F1 = "11111111-1111-4111-8111-111111111111";
const F2 = "22222222-2222-4222-8222-222222222222";

const server = vi.hoisted(() => ({
  view: null as unknown,
  posts: [] as { path: string; body: Record<string, unknown> }[],
  postStatus: 200,
}));

vi.mock("@/api/client.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  return {
    ...actual,
    apiFetch: vi.fn(async (path: string, init?: { method?: string; body?: string }) => {
      if (init?.method === "POST") {
        server.posts.push({ path, body: JSON.parse(init.body ?? "{}") });
        if (server.postStatus !== 200) {
          throw new actual.ApiError("CUSTODY_REFUSED", "refused", server.postStatus);
        }
        return { data: { outcome: "answered", answer: "yes" } };
      }
      return { data: server.view };
    }),
  };
});

function view(over: Record<string, unknown> = {}) {
  return {
    event_id: EVENT,
    provider: "google",
    kind: "new_sign_in",
    presentation: "presented",
    admitted_at: "2030-01-01T00:00:00Z",
    answer: null,
    case_recorded: false,
    recovery_door: "https://myaccount.google.com/notification/recovery",
    candidates: [
      { fact_id: F1, kind: "has-email", hint: "o***@example.test", held: false },
      { fact_id: F2, kind: "has-phone", hint: "...1234", held: true },
    ],
    ...over,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/security/${EVENT}`]}>
        <Routes>
          <Route path="/security/:eventId" element={<SecurityQuestionPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  server.view = view();
  server.posts = [];
  server.postStatus = 200;
});
afterEach(cleanup);

describe("SecurityQuestionPage", () => {
  it("shows the question with masked hints and does not answer by itself", async () => {
    renderPage();
    await screen.findByText(/reported a new sign-in/);
    expect(screen.getByText(/o\*\*\*@example.test/)).toBeTruthy();
    expect(screen.getByText(/does not show whether your account was compromised/)).toBeTruthy();
    expect(server.posts).toEqual([]);
  });

  it("posts yes with nothing else", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Yes, this was me/ }));
    await waitFor(() => expect(server.posts).toHaveLength(1));
    expect(server.posts[0].path).toBe(`/custody/security-questions/${EVENT}/answer`);
    expect(server.posts[0].body).toEqual({ answer: "yes" });
  });

  it("needs a ticked channel for no and posts exactly the ticked ids", async () => {
    renderPage();
    const no = await screen.findByRole("button", { name: /No, hold the ticked channels/ });
    expect((no as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("checkbox", { name: /phone/ }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /email/ }));
    fireEvent.click(no);
    await waitFor(() => expect(server.posts).toHaveLength(1));
    const body = server.posts[0].body;
    expect(body.answer).toBe("no");
    expect(body.fact_ids).toEqual([F1]);
    expect(body.reason).toBe("disowned_access");
    expect(typeof body.command_id).toBe("string");
  });

  it("says nothing changed when the server refuses the targets", async () => {
    server.postStatus = 409;
    renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: /email/ }));
    fireEvent.click(screen.getByRole("button", { name: /No, hold the ticked channels/ }));
    await screen.findByText(/Nothing was changed/);
  });

  it("keeps a retained no visible with the recovery door and offers no answer", async () => {
    server.view = view({ answer: "no", case_recorded: true });
    renderPage();
    await screen.findByText(/You answered no/);
    expect(screen.getByText(/A case is open/)).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toContain("myaccount.google.com");
    expect(screen.queryByRole("button", { name: /Yes, this was me/ })).toBeNull();
  });

  it("states an ambiguous notification honestly", async () => {
    server.view = view({ presentation: "ambiguous" });
    renderPage();
    await screen.findByText(/may or may not have reached you/);
  });
});

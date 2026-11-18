// @vitest-environment jsdom
/**
 * <SecurityQuestionsPage>: the owner's way to find an unanswered question. Unavailable and
 * ambiguous deliveries are listed and worded as such; answered questions drop off; each row links
 * to its question page.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

import SecurityQuestionsPage from "@/pages/SecurityQuestionsPage.tsx";
import { getShellCapability } from "@/lib/shell-capability.ts";

const server = vi.hoisted(() => ({ questions: [] as unknown[], fail: false }));

vi.mock("@/api/client.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  return {
    ...actual,
    apiFetch: vi.fn(async () => {
      if (server.fail) throw new actual.ApiError("x", "no", 503);
      return { data: { questions: server.questions } };
    }),
  };
});

function q(id: string, over: Record<string, unknown> = {}) {
  return {
    event_id: id,
    provider: "google",
    kind: "new_sign_in",
    presentation: "presented",
    admitted_at: "2030-01-01T00:00:00Z",
    answer: null,
    case_recorded: false,
    ...over,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SecurityQuestionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  server.questions = [];
  server.fail = false;
});
afterEach(cleanup);

describe("SecurityQuestionsPage", () => {
  it("is in the navigation, not context-only", () => {
    const capability = getShellCapability("/security");
    expect(capability?.placement).not.toBeNull();
    expect(capability?.discoverability).toBe("global");
  });

  it("lists unanswered questions including unavailable and ambiguous ones, each linked", async () => {
    server.questions = [
      q("e1", { presentation: "unavailable" }),
      q("e2", { presentation: "ambiguous" }),
      q("e3", { answer: "yes" }),
      q("e4", { answer: "no", case_recorded: true }),
      q("e5", { answer: "no", case_recorded: false }),
    ];
    renderPage();
    await screen.findByText(/Notification could not be delivered/);
    expect(screen.getByText(/Notification may not have reached you/)).toBeTruthy();
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/security/e1", "/security/e2", "/security/e5"]);
  });

  it("says nothing is waiting only when the list loaded and is empty", async () => {
    renderPage();
    await screen.findByText(/No unanswered security questions/);
  });

  it("does not look calm when the list cannot be loaded", async () => {
    server.fail = true;
    renderPage();
    await screen.findByText(/None are being treated as answered/);
    expect(screen.queryByText(/No unanswered security questions/)).toBeNull();
  });
});

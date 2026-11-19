// @vitest-environment jsdom
/**
 * <CustodyCasePage>: the static recovery checklist, descriptive progress only, evidence summaries
 * rendered generically, and an unavailable read that never looks clear.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";

import CustodyCasePage from "@/pages/CustodyCasePage.tsx";

const EPISODE = "22222222-2222-4222-8222-222222222222";
const server = vi.hoisted(() => ({
  caseBody: null as unknown,
  holds: [] as unknown[],
  failCase: false,
  failHolds: false,
  posts: 0,
}));

vi.mock("@/api/client.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  return {
    ...actual,
    apiFetch: vi.fn(async (path: string, init?: { method?: string }) => {
      if (init?.method === "POST") server.posts += 1;
      if (path.includes("/case")) {
        if (server.failCase) throw new actual.ApiError("x", "no", 503);
        return { data: server.caseBody };
      }
      if (server.failHolds) throw new actual.ApiError("x", "no", 503);
      return { data: { holds: server.holds } };
    }),
  };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/custody/${EPISODE}`]}>
        <Routes>
          <Route path="/custody/:episodeId" element={<CustodyCasePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseCase = {
  state: "open",
  outcome: null,
  opened_at: "2030-01-01T00:00:00Z",
  closed_at: null,
  truncated: false,
  evidence: [] as unknown[],
};

beforeEach(() => {
  server.caseBody = baseCase;
  server.holds = [];
  server.failCase = false;
  server.failHolds = false;
  server.posts = 0;
  window.localStorage.clear();
});
afterEach(cleanup);

describe("CustodyCasePage", () => {
  it("renders the SIM and sign-out checklist with the recovery doors and the security link", async () => {
    renderPage();
    await screen.findByText(/Nothing recorded yet/);
    expect(screen.getByText(/suspend or lock the SIM/)).toBeTruthy();
    expect(screen.getByText(/Sign the lost device out of your Google account/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Apple account recovery" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Security questions" }).getAttribute("href")).toBe(
      "/security",
    );
  });

  it("ticking a step is descriptive progress only: it posts nothing", async () => {
    renderPage();
    await screen.findByText(/Nothing recorded yet/);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expect(screen.getByText(/1 of 6 steps ticked/)).toBeTruthy();
    expect(server.posts).toBe(0);
  });

  it("renders evidence rows generically with the source and time", async () => {
    server.caseBody = {
      ...baseCase,
      evidence: [
        {
          contributor: "finance",
          kind: "held_window",
          contributed_at: "2030-01-01T01:00:00Z",
          summary: { transactions: 3, debits: 2, anomalies: { total: 1 } },
        },
        {
          contributor: "chronicler",
          kind: "device_last_fix",
          contributed_at: "2030-01-01T02:00:00Z",
          summary: { status: "unknown", reason: "stale", stale_fix_at: "2000-01-01T00:00:00Z" },
        },
      ],
    };
    renderPage();
    await screen.findByText(/Finance: Held window so far/);
    expect(screen.getByText(/Owner device: Last known device fix/)).toBeTruthy();
    expect(screen.getByText(/gone stale and is unknown now/)).toBeTruthy();
    expect(screen.queryByText("Home")).toBeNull();
    expect(screen.queryByText("known")).toBeNull();
    expect(screen.getByText("transactions")).toBeTruthy();
  });

  it("is unavailable, not clear, when the case cannot be read", async () => {
    server.failCase = true;
    renderPage();
    await screen.findByText(/The case is unavailable/);
    expect(screen.queryByText(/Nothing recorded yet/)).toBeNull();
  });

  it("reports partial data: holds unavailable while the case loads", async () => {
    server.failHolds = true;
    renderPage();
    await screen.findByText(/Nothing recorded yet/);
    await screen.findByText(/Holds are unavailable/);
    expect(screen.queryByText(/currently held/)).toBeNull();
  });
});

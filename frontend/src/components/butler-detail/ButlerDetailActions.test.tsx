// @vitest-environment jsdom
/**
 * ButlerDetailActions — Force-Run session linking (bu-dr03f.4).
 *
 * The Force-Run button previously discarded the session_id returned by
 * triggerButler. It now navigates the operator to /sessions/:id for the
 * spawned session.
 *
 * bead: bu-dr03f.4
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const navigateMock = vi.fn();
vi.mock("react-router", async () => {
  const actual = await vi.importActual<typeof import("react-router")>("react-router");
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/api/index.ts", () => ({ triggerButler: vi.fn() }));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(() => ({ data: { data: [] }, isLoading: false })),
  useSetEligibility: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

// ChatPanel pulls in heavy dependencies; stub it to a no-op for this unit test.
vi.mock("@/components/chat/ChatPanel", () => ({
  ChatPanel: () => null,
}));

import { ButlerDetailActions } from "./ButlerDetailActions";
import { triggerButler } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderActions(butlerName = "general") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ButlerDetailActions butlerName={butlerName} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerDetailActions — Force-Run session linking", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("navigates to /sessions/:id with the returned session_id", async () => {
    vi.mocked(triggerButler).mockResolvedValue({
      success: true,
      session_id: "sess-123",
      output: "",
    });

    renderActions();
    fireEvent.click(screen.getByTestId("butler-force-run"));

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/sessions/sess-123"),
    );
  });

  it("does not navigate when no session_id is returned", async () => {
    vi.mocked(triggerButler).mockResolvedValue({
      success: true,
      session_id: "",
      output: "",
    });

    renderActions();
    fireEvent.click(screen.getByTestId("butler-force-run"));

    await waitFor(() => expect(triggerButler).toHaveBeenCalled());
    expect(navigateMock).not.toHaveBeenCalled();
  });
});

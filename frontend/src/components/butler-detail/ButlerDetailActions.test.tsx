// @vitest-environment jsdom
/**
 * ButlerDetailActions — unified command bar (bu-86c4c.18) + Force-Run
 * session linking (bu-dr03f.4).
 *
 * Force Run / the Trigger tab / the ChatPanel "Prompt" button used to be
 * three separate names for "make this butler run". They are now one
 * prompt-first command bar: an empty submit fires the default scheduler
 * prompt (same as the old Force Run button); a custom prompt + complexity
 * tier replaces the old Trigger tab. The command bar still navigates the
 * operator to /sessions/:id for the spawned session.
 *
 * bead: bu-86c4c.18 (builds on bu-dr03f.4)
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
import { useRegistry } from "@/hooks/use-general";
import type { RegistryEntry } from "@/api/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mockRegistryEntry(overrides: Partial<RegistryEntry> = {}): void {
  const entry: RegistryEntry = {
    name: "general",
    endpoint_url: "http://x",
    description: null,
    modules: [],
    capabilities: [],
    last_seen_at: null,
    eligibility_state: "active",
    derived_eligibility_state: "active",
    liveness_ttl_seconds: 300,
    quarantined_at: null,
    quarantine_reason: null,
    route_contract_min: 1,
    route_contract_max: 1,
    eligibility_updated_at: null,
    registered_at: "2026-01-01T00:00:00Z",
    agent_type: "butler",
    ...overrides,
  };
  vi.mocked(useRegistry).mockReturnValue({
    data: { data: [entry] },
    isLoading: false,
  } as unknown as ReturnType<typeof useRegistry>);
}

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

describe("ButlerDetailActions — unified command bar (bu-86c4c.18)", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("submits the default scheduler prompt when the input is left empty", async () => {
    vi.mocked(triggerButler).mockResolvedValue({
      success: true,
      session_id: "",
      output: "",
    });

    renderActions("general");
    fireEvent.click(screen.getByTestId("butler-force-run"));

    await waitFor(() =>
      expect(triggerButler).toHaveBeenCalledWith(
        "general",
        "Run your scheduled tick now.",
        "workhorse",
      ),
    );
  });

  it("submits a custom prompt typed into the command bar (replaces the Trigger tab)", async () => {
    vi.mocked(triggerButler).mockResolvedValue({
      success: true,
      session_id: "",
      output: "",
    });

    renderActions("general");
    fireEvent.change(screen.getByTestId("butler-command-input"), {
      target: { value: "Summarize today's activity" },
    });
    fireEvent.click(screen.getByTestId("butler-force-run"));

    await waitFor(() =>
      expect(triggerButler).toHaveBeenCalledWith(
        "general",
        "Summarize today's activity",
        "workhorse",
      ),
    );
  });

  it("submits on Enter in the command input", async () => {
    vi.mocked(triggerButler).mockResolvedValue({
      success: true,
      session_id: "",
      output: "",
    });

    renderActions("general");
    const input = screen.getByTestId("butler-command-input");
    fireEvent.change(input, { target: { value: "Run finance reconciliation" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(triggerButler).toHaveBeenCalledWith(
        "general",
        "Run finance reconciliation",
        "workhorse",
      ),
    );
  });

  it("does not render a separate ChatPanel trigger labeled Prompt (unified into the command bar)", () => {
    renderActions("general");
    expect(screen.queryByText("Prompt")).toBeNull();
  });
});

describe("ButlerDetailActions — quarantine reason/timestamp at the restore decision point (bu-86c4c.3)", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("shows quarantine_reason and quarantined_at next to the Resume button", () => {
    mockRegistryEntry({
      eligibility_state: "quarantined",
      quarantined_at: "2026-07-01T00:00:00Z",
      quarantine_reason: "3 consecutive healing failures",
    });

    renderActions();

    const info = screen.getByTestId("butler-quarantine-info");
    expect(info.textContent).toContain("3 consecutive healing failures");
    expect(screen.getByRole("button", { name: /Resume/ })).toBeDefined();
  });

  it("does not render quarantine info when the butler is not quarantined", () => {
    mockRegistryEntry({ eligibility_state: "active" });

    renderActions();

    expect(screen.queryByTestId("butler-quarantine-info")).toBeNull();
    expect(screen.getByRole("button", { name: /Pause/ })).toBeDefined();
  });

  it("does not render quarantine info when quarantined but neither field is set", () => {
    mockRegistryEntry({
      eligibility_state: "quarantined",
      quarantined_at: null,
      quarantine_reason: null,
    });

    renderActions();

    expect(screen.queryByTestId("butler-quarantine-info")).toBeNull();
  });
});

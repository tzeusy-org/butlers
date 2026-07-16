// @vitest-environment jsdom
/**
 * GlobalActionsRegistrar — always-available "Run <butler>" command-menu
 * action (bu-86c4c.7).
 *
 * Regression coverage for bu-jlhk5: the quick-trigger action used to send
 * complexity="medium", which is not a valid backend tier (valid:
 * reasoning/workhorse/cheap/specialty/local/legacy — the backend
 * TriggerRequest defaults to "workhorse"). Mirrors the fix already shipped
 * for the unified command bar (bu-86c4c.18 / PR #2874,
 * ButlerDetailActions.tsx's DEFAULT_COMPLEXITY).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, act } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { PaletteCommand } from "@/lib/command-registry";
import type { TriggerResponse } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const navigateMock = vi.fn();
vi.mock("react-router", async () => {
  const actual = await vi.importActual<typeof import("react-router")>("react-router");
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/api/index", () => ({ triggerButler: vi.fn() }));

vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }));

let registeredCommands: PaletteCommand[] = [];
vi.mock("@/lib/command-registry", () => ({
  useRegisterCommands: (commands: PaletteCommand[]) => {
    registeredCommands = commands;
  },
}));

import { GlobalActionsRegistrar } from "./GlobalActionsRegistrar";
import { triggerButler } from "@/api/index";
import { useButlers } from "@/hooks/use-butlers";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderRegistrar() {
  return render(
    <MemoryRouter>
      <GlobalActionsRegistrar />
    </MemoryRouter>,
  );
}

describe("GlobalActionsRegistrar — Run <butler> command", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    registeredCommands = [];
    vi.mocked(useButlers).mockReturnValue({
      data: {
        data: [
          {
            name: "general",
            status: "ok",
            port: 9000,
            type: "butler",
            sessions_24h: 0,
          },
        ],
      },
    } as unknown as ReturnType<typeof useButlers>);
  });
  afterEach(() => cleanup());

  it("registers a Run command per butler", () => {
    renderRegistrar();
    expect(registeredCommands).toMatchObject([
      { id: "trigger:general", label: "Run general" },
    ]);
  });

  it("triggers with a valid backend complexity tier, not 'medium' (bu-jlhk5)", async () => {
    vi.mocked(triggerButler).mockResolvedValue({
      session_id: "sess-1",
      success: true,
      output: "",
    } as TriggerResponse);
    renderRegistrar();

    const command = registeredCommands.find((c) => c.id === "trigger:general");
    expect(command).toBeTruthy();

    await act(async () => {
      await command?.perform();
    });

    expect(triggerButler).toHaveBeenCalledWith(
      "general",
      "Run your scheduled tick now.",
      "workhorse",
    );
    expect(navigateMock).toHaveBeenCalledWith("/sessions/sess-1");
  });
});

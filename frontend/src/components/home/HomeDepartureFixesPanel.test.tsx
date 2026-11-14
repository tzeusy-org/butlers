// @vitest-environment jsdom
/**
 * Tests for <HomeDepartureFixesPanel>: the owner declares one exact fix per watched item, the
 * panel runs nothing, and an unset fix is visibly absent.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { HomeDepartureFixesPanel } from "@/components/home/HomeDepartureFixesPanel.tsx";

const saveFix = vi.fn();
const removeFix = vi.fn();
const refetch = vi.fn();
const state = vi.hoisted(() => ({
  watch: {} as Record<string, unknown>,
  fixes: {} as Record<string, unknown>,
}));

const idle = (mutate: unknown) => ({ mutate, isPending: false, isError: false });

vi.mock("@/hooks/use-home-departure-fixes.ts", () => ({
  useHomeDepartureWatch: () => ({ refetch, ...state.watch }),
  useHomeDepartureFixes: () => ({ refetch, ...state.fixes }),
  useSaveDepartureFix: () => idle(saveFix),
  useDeleteDepartureFix: () => idle(removeFix),
}));

const WATCH = [
  { entity_id: "light.garage", owner_label: "Garage light", secure_states: ["off"], updated_at: null },
  { entity_id: "lock.front", owner_label: "Front door", secure_states: ["locked"], updated_at: null },
];

beforeEach(() => {
  state.watch = { data: WATCH };
  state.fixes = {
    data: [
      {
        entity_id: "lock.front",
        domain: "lock",
        service: "lock",
        target_entity_id: "lock.front",
        data: {},
        risk: "protected",
        updated_at: null,
      },
    ],
  };
});

afterEach(() => {
  cleanup();
  saveFix.mockReset();
  removeFix.mockReset();
  refetch.mockReset();
});

describe("HomeDepartureFixesPanel", () => {
  it("shows a declared fix with its class and says it always needs approval", () => {
    render(<HomeDepartureFixesPanel />);

    const row = screen.getByTestId("home-departure-fix-lock.front").textContent ?? "";
    expect(row).toContain("lock.lock");
    expect(row).toContain("protected, always needs your approval");
    expect(screen.getByRole("button", { name: "Remove the fix for Front door" })).toBeTruthy();
  });

  it("pre-fills nothing for an item without a fix and offers no run button", () => {
    render(<HomeDepartureFixesPanel />);

    expect((screen.getByLabelText("Fix action for Garage light") as HTMLSelectElement).value).toBe("");
    expect((screen.getByLabelText("Entity to act on (Garage light)") as HTMLInputElement).value).toBe(
      "",
    );
    expect(screen.queryByRole("button", { name: /run|secure all|approve/i })).toBeNull();
  });

  it("saves the exact call the owner chose, with trimmed target and empty data", () => {
    render(<HomeDepartureFixesPanel />);
    fireEvent.change(screen.getByLabelText("Fix action for Garage light"), {
      target: { value: "light.turn_off" },
    });
    fireEvent.change(screen.getByLabelText("Entity to act on (Garage light)"), {
      target: { value: " light.garage " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save the fix for Garage light" }));

    expect(saveFix.mock.calls[0][0]).toEqual({
      entity_id: "light.garage",
      domain: "light",
      service: "turn_off",
      target_entity_id: "light.garage",
      data: {},
    });
  });

  it("refuses a target of another kind, or no action, without calling the API", () => {
    render(<HomeDepartureFixesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Save the fix for Garage light" }));
    expect(screen.getByRole("alert").textContent).toContain("Choose what the fix does");

    fireEvent.change(screen.getByLabelText("Fix action for Garage light"), {
      target: { value: "light.turn_off" },
    });
    fireEvent.change(screen.getByLabelText("Entity to act on (Garage light)"), {
      target: { value: "switch.garage" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save the fix for Garage light" }));

    expect(screen.getByRole("alert").textContent).toContain("same kind");
    expect(saveFix).not.toHaveBeenCalled();
  });

  it("removes a fix by its watched entity", () => {
    render(<HomeDepartureFixesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Remove the fix for Front door" }));

    expect(removeFix.mock.calls[0][0]).toBe("lock.front");
  });

  it("says there is nothing to fix when nothing is watched", () => {
    state.watch = { data: [] };
    state.fixes = { data: [] };
    render(<HomeDepartureFixesPanel />);

    expect(screen.getByText(/nothing to fix/)).toBeTruthy();
  });

  it("offers a reload when a read fails and keeps the saved fixes in force", () => {
    state.watch = { isError: true };
    state.fixes = { isError: true };
    render(<HomeDepartureFixesPanel />);

    expect(screen.getByTestId("home-departure-fixes-error").textContent).toContain("still apply");
    fireEvent.click(screen.getByRole("button", { name: "Reload departure fixes" }));
    expect(refetch).toHaveBeenCalledTimes(2);
  });
});

// @vitest-environment jsdom
/**
 * Tests for <CreateRuleDialog>: the create payload must carry the butler that
 * owns the rule (POST /approvals/rules requires it).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CreateRuleDialog } from "@/components/approvals/create-rule-dialog.tsx";

const mutate = vi.fn();
const LOADED = { data: { data: [{ name: "relationship" }, { name: "finance" }] } };
const butlersState = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));

vi.mock("@/hooks/use-approvals", () => ({
  useCreateRule: () => ({ mutate, isPending: false }),
}));

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: () => ({ refetch: vi.fn(), ...butlersState.current }),
}));

beforeEach(() => {
  butlersState.current = LOADED;
});

afterEach(() => {
  cleanup();
  mutate.mockReset();
});

function fillRule() {
  fireEvent.change(screen.getByLabelText("Tool Name"), { target: { value: "notify" } });
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Allow notify" } });
}

describe("CreateRuleDialog", () => {
  it("cannot submit until a butler is chosen", () => {
    render(<CreateRuleDialog open onOpenChange={() => {}} />);
    fillRule();

    const submit = screen.getByRole("button", { name: "Create rule" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Butler"), { target: { value: "finance" } });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it("sends the chosen butler in the create payload", () => {
    render(<CreateRuleDialog open onOpenChange={() => {}} />);
    fillRule();
    fireEvent.change(screen.getByLabelText("Butler"), { target: { value: "relationship" } });

    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toMatchObject({
      butler: "relationship",
      tool_name: "notify",
      description: "Allow notify",
    });
  });

  it("disables the selector and submit when the butlers list fails", () => {
    butlersState.current = { isError: true };
    render(<CreateRuleDialog open onOpenChange={() => {}} />);
    fillRule();

    expect(screen.getByRole("alert").textContent).toContain("Butlers");
    expect((screen.getByLabelText("Butler") as HTMLSelectElement).disabled).toBe(true);
    const submit = screen.getByRole("button", { name: "Create rule" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });

  it("disables the selector while the butlers list loads", () => {
    butlersState.current = { isLoading: true };
    render(<CreateRuleDialog open onOpenChange={() => {}} />);
    fillRule();

    expect((screen.getByLabelText("Butler") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByText("Loading butlers")).toBeTruthy();
    const submit = screen.getByRole("button", { name: "Create rule" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });
});

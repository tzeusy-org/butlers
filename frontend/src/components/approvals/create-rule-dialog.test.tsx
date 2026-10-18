// @vitest-environment jsdom
/**
 * Tests for <CreateRuleDialog>: the create payload must carry the butler that
 * owns the rule (POST /approvals/rules requires it).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CreateRuleDialog } from "@/components/approvals/create-rule-dialog.tsx";

const mutate = vi.fn();

vi.mock("@/hooks/use-approvals", () => ({
  useCreateRule: () => ({ mutate, isPending: false }),
}));

let butlersState: { data?: unknown; isError?: boolean } = {
  data: { data: [{ name: "relationship" }, { name: "finance" }] },
};
const refetch = vi.fn();

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: () => ({ ...butlersState, refetch }),
}));

afterEach(() => {
  cleanup();
  mutate.mockReset();
  refetch.mockReset();
  butlersState = { data: { data: [{ name: "relationship" }, { name: "finance" }] } };
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

  it("shows a degraded note and stays unsubmittable when the butlers list fails", () => {
    butlersState = { isError: true };
    render(<CreateRuleDialog open onOpenChange={() => {}} />);
    fillRule();

    expect(screen.getByRole("alert").textContent).toContain("Butlers");
    const submit = screen.getByRole("button", { name: "Create rule" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

// @vitest-environment jsdom
// REQ-dashboard-shell-006: unavailable persistence must preserve editing honestly.
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConditionForm } from "./ConditionForm";

vi.mock("@/hooks/use-health", () => ({
  useCreateCondition: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateCondition: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
afterEach(cleanup);

it("keeps the full form editable and discloses unavailable browser recovery", async () => {
  render(<ConditionForm onDone={() => {}} onCancel={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "A condition" } });
  fireEvent.change(screen.getByLabelText("Notes (optional)"), { target: { value: "Keep these words" } });
  expect(await screen.findByText("Draft saving unavailable. Keep this tab open.")).toBeTruthy();
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("A condition");
  expect((screen.getByLabelText("Notes (optional)") as HTMLTextAreaElement).value).toBe("Keep these words");
});
